# -*- coding: utf-8 -*-
"""统一学习引擎 — 四阶段解耦编排器（每 stage 产出报告，用户审查后手动推进）。

Stage 1 backtest  → 用户审查回测结果
Stage 2 reflect   → 用户审查 Opus 建议和审视
Stage 3 validate  → 用户审查验证效果（不自动采纳）
Stage 4 adopt/reject → 用户最终决策
"""

import json
import logging
from datetime import datetime

from knowledge.learning_config import (
    ensure_staging, clear_staging, save_learning_log, STAGING_PROMPT_PATH,
)
from knowledge.learning_session import (
    create_session, load_state, session_dir, stage_dir,
    save_proposals, load_proposals, save_audit, load_audit,
    load_exams, load_backtest_stats, load_completed_results,
    save_validation, load_validation, save_diff, load_diff,
    append_validation_result, load_validation_results, validation_completed_codes,
    save_decision, load_decision, update_stage,
    STATE_IN_PROGRESS, STATE_DONE, STATE_ADOPTED, STATE_REJECTED,
)

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# Stage 1: 批量回测
# ══════════════════════════════════════════════════════════════════

def run_stage1_backtest(
    session_id: str = "",
    mode: str = "general",
    count: int = 50,
    delay_between: int = 30,
    progress_cb=None,
) -> dict:
    """Stage 1: 创建 session（或恢复）并执行批量回测。

    返回: {session_id, status, stats, html_path, message}
    """
    # 创建新 session 或复用已有
    if not session_id:
        session_id = create_session(mode=mode, count=count, delay_between=delay_between)
        if progress_cb:
            progress_cb(f"新建 session: {session_id}")
    else:
        state = load_state(session_id)
        if not state:
            return {"status": "not_found", "message": f"Session {session_id} 不存在"}
        if progress_cb:
            progress_cb(f"恢复 session: {session_id}")

    from knowledge.learning_backtester import run_backtest_stage

    state = load_state(session_id)
    r1 = run_backtest_stage(
        session_id=session_id,
        count=state.get("count", count),
        delay_between=state.get("delay_between", delay_between),
        progress_cb=progress_cb,
    )

    if r1["status"] != "ok":
        return {"session_id": session_id, **r1}

    # 生成 Stage 1 HTML 报告
    html_path = _generate_stage_summary(session_id, "stage1")

    return {
        "session_id": session_id,
        "status": "ok",
        "stats": r1["stats"],
        "train_count": len(r1["train_results"]),
        "holdout_count": len(r1["holdout_exams"]),
        "html_path": html_path,
        "next_step": f"python cli.py learn reflect {session_id}",
    }


# ══════════════════════════════════════════════════════════════════
# Stage 2: Opus 反思 + 交叉审视
# ══════════════════════════════════════════════════════════════════

def run_stage2_reflect(session_id: str, progress_cb=None) -> dict:
    """Stage 2: 基于 Stage 1 结果执行 Round 2 + Round 3。

    返回: {session_id, status, proposals, adopted, html_path, next_step}
    """
    state = load_state(session_id)
    if not state:
        return {"status": "not_found", "message": f"Session {session_id} 不存在"}
    if state["stages"]["backtest"] != STATE_DONE:
        return {"status": "precondition_failed",
                "message": "Stage 1 未完成，请先 python cli.py learn backtest"}

    train_results = load_completed_results(session_id)
    stats = load_backtest_stats(session_id)
    if not train_results or not stats:
        return {"status": "missing_data", "message": "回测结果缺失"}

    update_stage(session_id, "reflect", STATE_IN_PROGRESS)

    # Round 2: 反思
    from knowledge.learning_reflector import run_reflection, run_cross_review
    if progress_cb:
        progress_cb("Stage 2 Round 2: Opus 统一反思...")
    proposals = run_reflection(train_results, stats, progress_cb)
    save_proposals(session_id, proposals)

    if not proposals:
        update_stage(session_id, "reflect", STATE_DONE)
        html_path = _generate_stage_summary(session_id, "stage2")
        return {
            "session_id": session_id,
            "status": "no_proposals",
            "message": "Opus 认为当前配置无需调整",
            "html_path": html_path,
        }

    # Round 3: 交叉审视
    if progress_cb:
        progress_cb("Stage 2 Round 3: Opus 交叉审视...")
    audit = run_cross_review(proposals, train_results, stats, progress_cb)
    save_audit(session_id, audit)

    update_stage(session_id, "reflect", STATE_DONE)
    html_path = _generate_stage_summary(session_id, "stage2")

    adopted = audit.get("adopted", [])
    if not adopted:
        return {
            "session_id": session_id,
            "status": "all_rejected",
            "message": "所有建议被交叉审视否决",
            "html_path": html_path,
        }

    return {
        "session_id": session_id,
        "status": "ok",
        "proposals_count": len(proposals),
        "adopted_count": len(adopted),
        "html_path": html_path,
        "next_step": f"python cli.py learn validate {session_id}",
    }


# ══════════════════════════════════════════════════════════════════
# Stage 3: 应用候选配置 + 验证（不自动采纳）
# ══════════════════════════════════════════════════════════════════

def run_stage3_validate(session_id: str, progress_cb=None) -> dict:
    """Stage 3: Round 4 应用到 staging + Round 5 验证集对比。

    关键：不自动 promote，等待 Stage 4 用户审批。
    返回: {session_id, status, validation, html_path, next_step}
    """
    state = load_state(session_id)
    if not state:
        return {"status": "not_found", "message": f"Session {session_id} 不存在"}
    if state["stages"]["reflect"] != STATE_DONE:
        return {"status": "precondition_failed",
                "message": "Stage 2 未完成，请先 python cli.py learn reflect"}

    audit = load_audit(session_id)
    adopted_proposals = audit.get("adopted", [])
    if not adopted_proposals:
        return {"status": "nothing_to_validate",
                "message": "Stage 2 无采纳的建议，无需验证"}

    update_stage(session_id, "validate", STATE_IN_PROGRESS)

    # Round 4: 应用到 staging
    from knowledge.learning_optimizer import apply_all_proposals
    if progress_cb:
        progress_cb("Stage 3 Round 4: 应用到 staging...")
    r4 = apply_all_proposals(adopted_proposals, progress_cb)
    save_diff(session_id, r4.get("diff", []), r4.get("prompt_proposals", []))

    # Round 5: 验证集对比（支持断点续跑）
    _, holdout_exams = load_exams(session_id)
    if holdout_exams and r4["applied_count"] > len(r4.get("prompt_proposals", [])):
        if progress_cb:
            progress_cb(f"Stage 3 Round 5: 验证集 {len(holdout_exams)} 只对比...")

        validation = _run_validation_with_resume(
            session_id, holdout_exams, r4["staging_tree"], progress_cb,
        )
        save_validation(session_id, validation)
    else:
        validation = {"skipped": True, "reason": "仅 prompt 变更或无可验证项"}
        save_validation(session_id, validation)

    update_stage(session_id, "validate", STATE_DONE)
    html_path = _generate_stage_summary(session_id, "stage3")

    return {
        "session_id": session_id,
        "status": "ok",
        "validation": validation,
        "html_path": html_path,
        "next_step": f"python cli.py learn adopt {session_id}  # 或 reject",
    }


def _run_validation_with_resume(
    session_id: str, holdout_exams: list[dict],
    staging_tree: dict, progress_cb=None,
) -> dict:
    """支持断点续跑的验证集回测。

    策略：
      - 先用旧配置跑完（追加到 old_results.jsonl，支持续跑）
      - 临时替换为新配置
      - 用新配置跑完（追加到 new_results.jsonl，支持续跑）
      - 恢复旧配置，比较新旧
    """
    from knowledge.learning_backtester import run_single_backtest, compute_stats
    from knowledge.learning_config import DECISION_TREE_PATH
    from knowledge.learning_optimizer import check_adoption_criteria
    import shutil, time

    # Phase 1: 旧配置
    done_old = validation_completed_codes(session_id, "old")
    pending_old = [e for e in holdout_exams if e["ts_code"] not in done_old]
    if pending_old:
        if progress_cb:
            progress_cb(f"  旧配置: 已完成 {len(done_old)}，继续跑 {len(pending_old)} 只")
        for i, exam in enumerate(pending_old):
            if progress_cb:
                progress_cb(f"  [旧 {i+1}/{len(pending_old)}] {exam['stock_name']}")
            r = run_single_backtest(exam, progress_cb)
            if r:
                append_validation_result(session_id, r, "old")
            if i < len(pending_old) - 1:
                time.sleep(30)

    # Phase 2: 临时切换到新配置
    backup_path = DECISION_TREE_PATH.with_suffix(".json.bak")
    shutil.copy2(DECISION_TREE_PATH, backup_path)
    try:
        DECISION_TREE_PATH.write_text(
            json.dumps(staging_tree, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        try:
            from services.decision_tree import reload_tree
            reload_tree()
        except Exception:
            pass

        done_new = validation_completed_codes(session_id, "new")
        pending_new = [e for e in holdout_exams if e["ts_code"] not in done_new]
        if pending_new:
            if progress_cb:
                progress_cb(f"  新配置: 已完成 {len(done_new)}，继续跑 {len(pending_new)} 只")
            for i, exam in enumerate(pending_new):
                if progress_cb:
                    progress_cb(f"  [新 {i+1}/{len(pending_new)}] {exam['stock_name']}")
                r = run_single_backtest(exam, progress_cb)
                if r:
                    append_validation_result(session_id, r, "new")
                if i < len(pending_new) - 1:
                    time.sleep(30)
    finally:
        # 恢复旧配置
        shutil.copy2(backup_path, DECISION_TREE_PATH)
        backup_path.unlink(missing_ok=True)
        try:
            from services.decision_tree import reload_tree
            reload_tree()
        except Exception:
            pass

    # 计算 new/old stats + 校准度
    old_results = load_validation_results(session_id, "old")
    new_results = load_validation_results(session_id, "new")
    old_stats_base = compute_stats(old_results)
    new_stats_base = compute_stats(new_results)

    def _calibration(results):
        high = [r["excess_return"] for r in results if r.get("weighted", 0) >= 70]
        low = [r["excess_return"] for r in results if r.get("weighted", 0) < 50]
        if high and low:
            return round((sum(high) / len(high)) - (sum(low) / len(low)), 2)
        return 0.0

    old_stats = {**old_stats_base, "calibration": _calibration(old_results)}
    new_stats = {**new_stats_base, "calibration": _calibration(new_results)}

    criteria = check_adoption_criteria(
        old_hit_rate=old_stats["hit_rate"],
        new_hit_rate=new_stats["hit_rate"],
        old_by_category=old_stats["by_category"],
        new_by_category=new_stats["by_category"],
        old_calibration=old_stats["calibration"],
        new_calibration=new_stats["calibration"],
    )

    return {
        "old_stats": old_stats, "new_stats": new_stats,
        "old_results": old_results, "new_results": new_results,
        "criteria_passed": criteria["adopted"],
        "criteria_reason": criteria["reason"],
        "criteria_details": criteria.get("details", {}),
    }


# ══════════════════════════════════════════════════════════════════
# Stage 4: 最终采纳或回退
# ══════════════════════════════════════════════════════════════════

def run_stage4_adopt(session_id: str, progress_cb=None) -> dict:
    """Stage 4 采纳: 将 staging 提升到生产。"""
    state = load_state(session_id)
    if not state:
        return {"status": "not_found", "message": f"Session {session_id} 不存在"}
    if state["stages"]["validate"] != STATE_DONE:
        return {"status": "precondition_failed", "message": "Stage 3 未完成"}

    # 再次应用 staging 并 promote
    from knowledge.learning_config import (
        load_production_tree, save_staging_tree, promote_staging,
    )
    audit = load_audit(session_id)
    adopted_proposals = [p for p in audit.get("adopted", []) if p.get("type") != "prompt"]

    if adopted_proposals:
        from knowledge.learning_optimizer import apply_all_proposals
        r4 = apply_all_proposals(adopted_proposals, progress_cb)
        save_staging_tree(r4["staging_tree"])
        promote_staging()
        try:
            from services.decision_tree import reload_tree
            reload_tree()
        except Exception:
            pass
        if progress_cb:
            progress_cb(f"✅ 已采纳 {r4['applied_count']} 条配置变更")

    save_decision(session_id, "adopted", "用户手动采纳")
    update_stage(session_id, "final", STATE_ADOPTED)

    return {
        "session_id": session_id,
        "status": "adopted",
        "message": "新配置已提升到生产",
    }


def run_stage4_reject(session_id: str, reason: str = "", progress_cb=None) -> dict:
    """Stage 4 回退: 清除 staging，不改生产。"""
    state = load_state(session_id)
    if not state:
        return {"status": "not_found", "message": f"Session {session_id} 不存在"}

    clear_staging()
    save_decision(session_id, "rejected", reason or "用户手动回退")
    update_stage(session_id, "final", STATE_REJECTED)

    if progress_cb:
        progress_cb(f"❌ 已回退，staging 已清除")

    return {
        "session_id": session_id,
        "status": "rejected",
        "message": f"已回退: {reason}",
    }


# ══════════════════════════════════════════════════════════════════
# Prompt 审批（独立于 stages）
# ══════════════════════════════════════════════════════════════════

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

    for p in patches:
        p["approved_at"] = datetime.now().isoformat()
    STAGING_PROMPT_PATH.write_text(
        json.dumps(patches, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if progress_cb:
        progress_cb("✅ Prompt 变更已审批")

    return {"status": "approved", "count": len(patches)}


# ══════════════════════════════════════════════════════════════════
# 报告生成（拼装每 stage 的数据 → HTML）
# ══════════════════════════════════════════════════════════════════

def _generate_stage_summary(session_id: str, stage: str) -> str:
    """生成指定 stage 的 HTML 报告。"""
    from knowledge.learning_summary import save_summary_html, open_in_browser

    state = load_state(session_id)
    result = {
        "session_id": session_id,
        "mode": state.get("mode", "?"),
        "count": state.get("count", 0),
        "started_at": state.get("created_at", "?"),
        "stage": stage,
        "stages_status": state.get("stages", {}),
        "rounds": {},
    }

    # Stage 1 数据
    stats = load_backtest_stats(session_id)
    if stats:
        results = load_completed_results(session_id)
        result["rounds"]["round1"] = {**stats, "train_results": results}

    # Stage 2 数据
    proposals = load_proposals(session_id)
    if proposals:
        result["rounds"]["round2"] = {"proposals": proposals}
    audit = load_audit(session_id)
    if audit:
        result["rounds"]["round3"] = {
            "original_count": len(proposals),
            "adopted_count": len(audit.get("adopted", [])),
            "adopted_proposals": audit.get("adopted", []),
            "verdicts": audit.get("verdicts", []),
            "defenses": audit.get("defenses", []),
            "arbitrations": audit.get("arbitrations", []),
            "fast_path": audit.get("fast_path", False),
        }

    # Stage 3 数据
    diff, prompt_props = load_diff(session_id)
    if diff:
        result["rounds"]["round4"] = {
            "applied_count": sum(1 for d in diff if d.get("status") == "applied"),
            "errors": [{"proposal_id": d["proposal_id"], "errors": d["errors"]}
                       for d in diff if d.get("status") == "rejected_safety"],
            "has_prompt_changes": len(prompt_props) > 0,
            "diff": diff,
            "prompt_proposals": prompt_props,
        }
    validation = load_validation(session_id)
    if validation and not validation.get("skipped"):
        result["rounds"]["round5"] = {
            "adopted": validation.get("criteria_passed", False),
            "reason": validation.get("criteria_reason", ""),
            "old_stats": validation.get("old_stats", {}),
            "new_stats": validation.get("new_stats", {}),
            "old_results": validation.get("old_results", []),
            "new_results": validation.get("new_results", []),
        }

    # Stage 4 决定
    decision = load_decision(session_id)
    if decision:
        result["final_decision"] = decision

    # 状态摘要
    stage_map = {"stage1": "Stage 1 回测", "stage2": "Stage 2 反思+审视", "stage3": "Stage 3 验证", "stage4": "Stage 4 最终"}
    result["status"] = stage_map.get(stage, stage)
    result["summary"] = _build_stage_summary_text(result, stage)

    # 保存 + 弹浏览器
    mode = state.get("mode", "general")
    count = state.get("count", 0)
    html_path = save_summary_html(
        result, f"{mode}_{stage}_{session_id[:10]}", count,
    )
    logger.info("[learn] Stage %s summary: %s", stage, html_path)
    print(f"\n📊 {stage_map.get(stage, stage)} 报告: {html_path}")
    open_in_browser(html_path)
    print("🌐 已在浏览器中打开，请审查后决定是否进入下一 stage")
    return html_path


def _build_stage_summary_text(result: dict, stage: str) -> str:
    """生成各 stage 的一句话摘要。"""
    rounds = result.get("rounds", {})
    if stage == "stage1":
        r1 = rounds.get("round1", {})
        nxt = f"请审查回测结果，确认无误后运行: python cli.py learn reflect {result['session_id']}"
        return f"Stage 1 完成：{r1.get('total', 0)} 只回测，胜率 {r1.get('hit_rate', 0)}%。{nxt}"
    if stage == "stage2":
        r2 = rounds.get("round2", {}); r3 = rounds.get("round3", {})
        nxt = f"请审查 Opus 建议，确认可行后运行: python cli.py learn validate {result['session_id']}"
        return f"Stage 2 完成：Opus 产出 {len(r2.get('proposals', []))} 条建议，审视通过 {r3.get('adopted_count', 0)} 条。{nxt}"
    if stage == "stage3":
        r5 = rounds.get("round5", {})
        if r5:
            old = r5.get("old_stats", {}).get("hit_rate", 0)
            new = r5.get("new_stats", {}).get("hit_rate", 0)
            pass_criteria = "✅ 达标" if r5.get("adopted") else "⚠️ 未达标"
            nxt = f"请决定: python cli.py learn adopt {result['session_id']}  /  python cli.py learn reject {result['session_id']}"
            return f"Stage 3 完成：旧 {old}% → 新 {new}% ({pass_criteria})。{nxt}"
        return f"Stage 3 完成：仅 prompt 变更，请直接 approve-prompt。"
    if stage == "stage4":
        d = result.get("final_decision", {})
        return f"Stage 4 最终决定：{d.get('decision', '?')}。{d.get('reason', '')}"
    return ""
