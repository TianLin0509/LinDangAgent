# knowledge/learning_engine.py
# -*- coding: utf-8 -*-
"""统一学习引擎 — 主入口，编排五轮循环。

用法:
  python cli.py learn general 50    # 通用分析回测
  python cli.py learn general 5     # 小规模测试
"""

import json
import logging
from datetime import datetime

from knowledge.learning_config import (
    ensure_staging, clear_staging, save_learning_log,
    STAGING_PROMPT_PATH,
)

logger = logging.getLogger(__name__)


def run_learning_cycle(
    mode: str = "general",
    count: int = 50,
    delay_between: int = 30,
    progress_cb=None,
) -> dict:
    """执行完整五轮学习循环。

    Args:
        mode: 学习模式 ("general" | "dragon" | "weights" | "full")
        count: 回测股票数量
        delay_between: 每只股票之间的间隔秒数
        progress_cb: 进度回调函数

    Returns:
        学习结果字典
    """
    if progress_cb:
        progress_cb(f"=== 统一学习引擎启动 [{mode}] count={count} ===")

    ensure_staging()
    clear_staging()

    result = {
        "mode": mode,
        "count": count,
        "started_at": datetime.now().isoformat(),
        "rounds": {},
    }

    # ── Round 1: 批量回测 ─────────────────────────────────────
    if progress_cb:
        progress_cb("=" * 50)
        progress_cb("Round 1: 批量回测")

    from knowledge.learning_backtester import run_backtest_round

    r1 = run_backtest_round(
        count=count,
        delay_between=delay_between,
        progress_cb=progress_cb,
    )

    if r1["status"] != "ok":
        result["status"] = "failed_round1"
        result["message"] = r1.get("message", "Round 1 失败")
        if progress_cb:
            progress_cb(f"Round 1 失败: {result['message']}")
        return result

    result["rounds"]["round1"] = {
        **r1["stats"],
        "train_results": r1["train_results"],  # 保留案例详情供摘要调试
    }
    train_results = r1["train_results"]
    holdout_exams = r1["holdout_exams"]

    if progress_cb:
        s = r1["stats"]
        progress_cb(f"Round 1 完成: {s['total']} 只, 胜率 {s['hit_rate']}%")

    # ── Round 2: Opus 统一反思 ────────────────────────────────
    if progress_cb:
        progress_cb("=" * 50)
        progress_cb("Round 2: Opus 统一反思")

    from knowledge.learning_reflector import run_reflection

    proposals = run_reflection(train_results, r1["stats"], progress_cb)
    result["rounds"]["round2"] = {"proposals": proposals}

    if not proposals:
        result["status"] = "no_proposals"
        result["message"] = "Opus 认为当前配置已足够好，无需调整"
        result["summary"] = "本轮学习: Opus 审视后认为当前配置无需调整"
        save_learning_log(result, mode, count)
        if progress_cb:
            progress_cb("Opus 未提出调整建议，本轮结束")
        return result

    # ── Round 3: Opus 交叉审视 ────────────────────────────────
    if progress_cb:
        progress_cb("=" * 50)
        progress_cb("Round 3: Opus 交叉审视")

    from knowledge.learning_reflector import run_cross_review

    audit = run_cross_review(proposals, train_results, r1["stats"], progress_cb)
    adopted_proposals = audit["adopted"]
    result["rounds"]["round3"] = {
        "original_count": len(proposals),
        "adopted_count": len(adopted_proposals),
        "adopted_proposals": adopted_proposals,
        "verdicts": audit.get("verdicts", []),
        "defenses": audit.get("defenses", []),
        "arbitrations": audit.get("arbitrations", []),
        "fast_path": audit.get("fast_path", False),
    }

    if not adopted_proposals:
        result["status"] = "all_rejected"
        result["message"] = "所有建议在交叉审视中被否决"
        result["summary"] = "本轮学习: 所有调整建议被风控审视否决"
        save_learning_log(result, mode, count)
        if progress_cb:
            progress_cb("所有建议被否决，本轮结束")
        return result

    # ── Round 4: 应用候选配置 ─────────────────────────────────
    if progress_cb:
        progress_cb("=" * 50)
        progress_cb("Round 4: 应用候选配置")

    from knowledge.learning_optimizer import apply_all_proposals

    r4 = apply_all_proposals(adopted_proposals, progress_cb)
    result["rounds"]["round4"] = {
        "applied_count": r4["applied_count"],
        "errors": r4["errors"],
        "has_prompt_changes": len(r4["prompt_proposals"]) > 0,
        "diff": r4.get("diff", []),
        "prompt_proposals": r4["prompt_proposals"],
    }

    # Prompt 变更需要人工审批
    if r4["prompt_proposals"]:
        if progress_cb:
            progress_cb(f"⚠️ {len(r4['prompt_proposals'])} 条 prompt 变更需要你的审批")
            progress_cb(f"   运行: python cli.py learn approve-prompt")

        # 发邮件通知
        try:
            from utils.email_sender import send_text_email, smtp_configured
            if smtp_configured():
                body_lines = ["统一学习引擎 — Prompt 变更待审批\n"]
                for p in r4["prompt_proposals"]:
                    body_lines.append(f"[{p.get('id')}] {p.get('target')}")
                    body_lines.append(f"  理由: {p.get('evidence', '')}")
                    body_lines.append(f"  建议: {p.get('proposed_value', '')[:200]}")
                    body_lines.append("")
                body_lines.append("请运行 python cli.py learn approve-prompt 审批")
                send_text_email("学习引擎 Prompt 变更待审批", "\n".join(body_lines))
        except Exception as exc:
            logger.warning("[learn] prompt notification email failed: %s", exc)

    if r4["applied_count"] == 0 or (r4["applied_count"] == len(r4["prompt_proposals"])):
        # 只有 prompt 变更（需人工审批），无自动变更可验证
        result["status"] = "pending_prompt_approval"
        result["message"] = "非 prompt 变更为零，prompt 变更待审批"
        result["summary"] = f"本轮学习: {len(r4['prompt_proposals'])} 条 prompt 变更待审批"
        save_learning_log(result, mode, count)
        return result

    # ── Round 5: 验证集对比 ───────────────────────────────────
    if progress_cb:
        progress_cb("=" * 50)
        progress_cb("Round 5: 验证集对比")

    from knowledge.learning_optimizer import run_validation

    r5 = run_validation(
        holdout_exams=holdout_exams,
        staging_tree=r4["staging_tree"],
        delay_between=delay_between,
        progress_cb=progress_cb,
    )
    result["rounds"]["round5"] = r5

    if r5["adopted"]:
        result["status"] = "adopted"
        old_hr = r5["old_stats"]["hit_rate"]
        new_hr = r5["new_stats"]["hit_rate"]
        result["summary"] = f"本轮学习: 新配置采纳，胜率 {old_hr}% → {new_hr}%"
    else:
        result["status"] = "not_adopted"
        result["summary"] = f"本轮学习: 未达标回退。原因: {r5['reason']}"

    result["finished_at"] = datetime.now().isoformat()

    # 保存日志
    save_learning_log(result, mode, count)

    # 发送结果邮件
    _send_result_email(result)

    if progress_cb:
        progress_cb(f"=== 学习引擎完成: {result['status']} ===")
        progress_cb(result["summary"])

    return result


def approve_prompt_patches(progress_cb=None) -> dict:
    """审批 staging 中的 prompt 变更。"""
    if not STAGING_PROMPT_PATH.exists():
        return {"status": "no_pending", "message": "无待审批的 prompt 变更"}

    patches = json.loads(STAGING_PROMPT_PATH.read_text(encoding="utf-8"))
    if not patches:
        return {"status": "no_pending", "message": "无待审批的 prompt 变更"}

    if progress_cb:
        progress_cb(f"审批 {len(patches)} 条 prompt 变更:")
        for p in patches:
            progress_cb(f"  [{p.get('id')}] {p.get('target')}: {p.get('proposed_value', '')[:100]}")

    # 标记为已审批
    for p in patches:
        p["approved_at"] = datetime.now().isoformat()
    STAGING_PROMPT_PATH.write_text(
        json.dumps(patches, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if progress_cb:
        progress_cb("✅ Prompt 变更已审批，将在下次分析时生效")

    return {"status": "approved", "count": len(patches)}


def _send_result_email(result: dict):
    """生成 HTML 摘要、弹出浏览器、发送邮件。"""
    try:
        from knowledge.learning_summary import (
            save_summary_html, open_in_browser, send_summary_email,
        )
        # 保存为 HTML
        mode = result.get("mode", "general")
        count = result.get("count", 0)
        html_path = save_summary_html(result, mode, count)
        logger.info("[learn] summary saved to %s", html_path)
        print(f"\n📊 学习报告已生成: {html_path}")

        # 弹出浏览器
        open_in_browser(html_path)
        print("🌐 已在默认浏览器中打开")

        # 邮件
        send_summary_email(result, html_path)
    except Exception as exc:
        logger.warning("[learn] result email failed: %s", exc)
