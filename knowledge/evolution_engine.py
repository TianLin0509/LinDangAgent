# -*- coding: utf-8 -*-
"""进化引擎 — Channel B: 夜间回测 + 权重自动调节

功能：
  1. run_nightly_backtest() — 加载已有实际收益的经验，计算每个维度与 T+20 收益的
     Pearson 相关系数，分析评分区间胜率，生成健康报告。
  2. apply_weight_change() — 应用用户审批后的权重变更，写入 decision_tree.json。
  3. send_weight_proposal_email() — 将权重调节建议发邮件给用户。
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from statistics import mean, stdev
from typing import Optional

logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).resolve().parent.parent
_WEIGHT_HISTORY_PATH = _BASE_DIR / "data" / "knowledge" / "weight_history.json"
_EVOLUTION_REPORTS_DIR = _BASE_DIR / "data" / "knowledge" / "evolution_reports"


# ── 纯 Python Pearson 相关系数 ────────────────────────────────────────

def _pearson_r(x: list[float], y: list[float]) -> float:
    n = len(x)
    if n < 3:
        return 0.0
    mx, my = mean(x), mean(y)
    try:
        sx, sy = stdev(x), stdev(y)
    except Exception:
        return 0.0
    if sx == 0 or sy == 0:
        return 0.0
    return round(
        sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / ((n - 1) * sx * sy),
        3,
    )


# ── 评分区间分析 ──────────────────────────────────────────────────────

def _analyze_brackets(entries: list[dict]) -> dict:
    """返回三个评分区间（>=80 / 60-79 / <60）的胜率和平均收益。"""
    brackets = {
        "high": {"label": ">=80", "entries": []},
        "mid":  {"label": "60-79", "entries": []},
        "low":  {"label": "<60",  "entries": []},
    }
    for e in entries:
        score = e.get("prediction", {}).get("composite_score")
        ret = e.get("actual", {}).get("return_20d")
        if score is None or ret is None:
            continue
        if score >= 80:
            brackets["high"]["entries"].append(ret)
        elif score >= 60:
            brackets["mid"]["entries"].append(ret)
        else:
            brackets["low"]["entries"].append(ret)

    result = {}
    for key, b in brackets.items():
        rets = b["entries"]
        if rets:
            win_rate = round(sum(1 for r in rets if r > 0) / len(rets), 3)
            avg_ret = round(mean(rets), 4)
        else:
            win_rate = None
            avg_ret = None
        result[key] = {
            "label": b["label"],
            "count": len(rets),
            "win_rate": win_rate,
            "avg_return": avg_ret,
        }
    return result


# ── 维度相关系数 ──────────────────────────────────────────────────────

def _compute_dim_correlations(entries: list[dict]) -> dict[str, float]:
    """计算每个维度评分与 T+20 实际收益的 Pearson 相关系数。"""
    dims = ("基本面", "预期差", "资金面", "技术面")
    correlations: dict[str, float] = {}
    returns = [e["actual"]["return_20d"] for e in entries]

    for dim in dims:
        scores = []
        valid_rets = []
        for e, r in zip(entries, returns):
            dim_scores = e.get("prediction", {}).get("dim_scores", {})
            if dim in dim_scores and dim_scores[dim] is not None:
                scores.append(float(dim_scores[dim]))
                valid_rets.append(r)
        correlations[dim] = _pearson_r(scores, valid_rets) if len(scores) >= 3 else 0.0

    return correlations


# ── 权重调节建议 ──────────────────────────────────────────────────────

def _generate_proposal(
    correlations: dict[str, float],
    current_weights: dict[str, float],
) -> Optional[dict]:
    """
    触发条件：最高相关维度 > 0.30 AND 最低相关维度 < 0.15 AND 最高权重 < 0.50
    动作：最高维度 +5%，最低维度 -5%，其他维度不变。
    """
    if not correlations:
        return None

    sorted_dims = sorted(correlations.items(), key=lambda kv: kv[1], reverse=True)
    top_dim, top_corr = sorted_dims[0]
    bot_dim, bot_corr = sorted_dims[-1]

    if not (top_corr > 0.30 and bot_corr < 0.15 and current_weights.get(top_dim, 0) < 0.50):
        return None

    new_weights = {k: round(v, 4) for k, v in current_weights.items()}
    new_weights[top_dim] = round(new_weights.get(top_dim, 0) + 0.05, 4)
    new_weights[bot_dim] = round(new_weights.get(bot_dim, 0) - 0.05, 4)

    # 确保权重不为负
    if new_weights[bot_dim] < 0:
        new_weights[bot_dim] = 0.0

    return {
        "status": "pending_approval",
        "top_dim": top_dim,
        "top_corr": top_corr,
        "bot_dim": bot_dim,
        "bot_corr": bot_corr,
        "old_weights": {k: round(v, 4) for k, v in current_weights.items()},
        "new_weights": new_weights,
        "reason": f"{top_dim} 相关系数 {top_corr} > 0.30，{bot_dim} 相关系数 {bot_corr} < 0.15",
    }


def _simulate_impact(
    entries: list[dict],
    old_weights: dict[str, float],
    new_weights: dict[str, float],
) -> dict:
    """用历史数据对比新旧权重的胜率差异（使用复合分>=60为做多信号）。"""
    from services.decision_tree import compute_weighted

    old_wins, old_total = 0, 0
    new_wins, new_total = 0, 0

    for e in entries:
        dim_scores = e.get("prediction", {}).get("dim_scores", {})
        ret = e.get("actual", {}).get("return_20d")
        if not dim_scores or ret is None:
            continue
        old_score = compute_weighted(dim_scores, old_weights)
        new_score = compute_weighted(dim_scores, new_weights)
        if old_score >= 60:
            old_total += 1
            if ret > 0:
                old_wins += 1
        if new_score >= 60:
            new_total += 1
            if ret > 0:
                new_wins += 1

    return {
        "old_win_rate": round(old_wins / old_total, 3) if old_total else None,
        "old_signal_count": old_total,
        "new_win_rate": round(new_wins / new_total, 3) if new_total else None,
        "new_signal_count": new_total,
    }


# ── 主入口：夜间回测 ─────────────────────────────────────────────────

def run_nightly_backtest(sample_size: int = 60) -> dict:
    """Channel B: 夜间回测。

    加载已评估的经验，计算每个维度与 T+20 收益的 Pearson 相关系数，
    统计评分区间胜率，判断是否需要提出权重调节建议。
    返回健康报告 dict。
    """
    from knowledge.experience_db import load_db
    from services.decision_tree import load_tree, _TREE_PATH

    report: dict = {
        "date": date.today().isoformat(),
        "sample_size_requested": sample_size,
    }

    # 1. 加载有实际收益的经验
    all_entries = load_db()
    evaluated = [
        e for e in all_entries
        if e.get("actual", {}).get("return_20d") is not None
    ]
    evaluated = evaluated[-sample_size:]  # 取最近 N 条

    report["sample_size_actual"] = len(evaluated)

    if len(evaluated) < 5:
        report["status"] = "insufficient_data"
        report["message"] = f"已评估经验不足（{len(evaluated)} 条，需要至少 5 条）"
        _save_report(report)
        return report

    # 2. 评分区间分析
    report["brackets"] = _analyze_brackets(evaluated)

    # 3. 维度相关系数
    correlations = _compute_dim_correlations(evaluated)
    report["dim_correlations"] = correlations

    # 4. 是否提出权重调节建议
    try:
        tree = load_tree()
        current_weights = tree.get("weights", {})
        proposal = _generate_proposal(correlations, current_weights)
        if proposal:
            # 模拟影响
            proposal["simulated_impact"] = _simulate_impact(evaluated, proposal["old_weights"], proposal["new_weights"])
        report["weight_proposal"] = proposal
        report["current_weights"] = {k: round(v, 4) for k, v in current_weights.items()}
    except Exception as exc:
        logger.warning("[evolution_engine] 权重分析失败: %r", exc)
        report["weight_proposal"] = None

    report["status"] = "ok"

    _save_report(report)
    logger.info(
        "[evolution_engine] 回测完成: %d条样本, 相关系数=%s, 有建议=%s",
        len(evaluated),
        correlations,
        bool(report.get("weight_proposal")),
    )
    return report


def _save_report(report: dict) -> None:
    """将健康报告保存到 evolution_reports/ 目录。"""
    _EVOLUTION_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    today = report.get("date", date.today().isoformat())
    path = _EVOLUTION_REPORTS_DIR / f"{today}_health.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    logger.info("[evolution_engine] 健康报告保存至 %s", path)


# ── 应用权重变更 ──────────────────────────────────────────────────────

def apply_weight_change(new_weights: dict) -> None:
    """应用用户审批后的权重变更到 decision_tree.json，并记录历史。"""
    from services.decision_tree import load_tree, reload_tree, _TREE_PATH

    tree = load_tree()
    old_weights = {k: round(v, 4) for k, v in tree.get("weights", {}).items()}

    # 更新 tree
    tree["weights"] = {k: round(float(v), 4) for k, v in new_weights.items()}
    tree["updated_at"] = date.today().isoformat()

    _TREE_PATH.write_text(
        json.dumps(tree, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 记录到 weight_history.json
    _record_weight_history(old_weights, new_weights)

    # 重载缓存
    reload_tree()
    logger.info("[evolution_engine] 权重已更新: %s → %s", old_weights, new_weights)


def _record_weight_history(old_weights: dict, new_weights: dict) -> None:
    """追加一条权重变更记录到 weight_history.json。"""
    try:
        text = _WEIGHT_HISTORY_PATH.read_text(encoding="utf-8").strip()
        history: list = json.loads(text) if text else []
    except (FileNotFoundError, json.JSONDecodeError):
        history = []

    history.append({
        "date": date.today().isoformat(),
        "old_weights": old_weights,
        "new_weights": {k: round(float(v), 4) for k, v in new_weights.items()},
    })
    _WEIGHT_HISTORY_PATH.write_text(
        json.dumps(history, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ── 发送权重建议邮件 ──────────────────────────────────────────────────

def send_weight_proposal_email(proposal: dict) -> None:
    """将权重调节建议发邮件给用户。"""
    from utils.email_sender import send_text_email, smtp_configured

    if not smtp_configured():
        logger.warning("[evolution_engine] SMTP 未配置，跳过邮件发送")
        return

    top_dim = proposal.get("top_dim", "?")
    bot_dim = proposal.get("bot_dim", "?")
    old_w = proposal.get("old_weights", {})
    new_w = proposal.get("new_weights", {})
    sim = proposal.get("simulated_impact", {})

    lines = [
        f"[林铛进化引擎] 权重调节建议 — {date.today().isoformat()}",
        "",
        f"原因：{proposal.get('reason', '')}",
        "",
        "权重变更：",
    ]
    all_dims = sorted(set(list(old_w.keys()) + list(new_w.keys())))
    for dim in all_dims:
        o = old_w.get(dim, 0)
        n = new_w.get(dim, 0)
        arrow = "↑" if n > o else ("↓" if n < o else "→")
        lines.append(f"  {dim}: {o:.2%} {arrow} {n:.2%}")

    lines += [
        "",
        "历史回测模拟影响：",
        f"  旧权重信号数: {sim.get('old_signal_count', 'N/A')}，胜率: {sim.get('old_win_rate', 'N/A')}",
        f"  新权重信号数: {sim.get('new_signal_count', 'N/A')}，胜率: {sim.get('new_win_rate', 'N/A')}",
        "",
        "如需应用此建议，请执行：",
        "  python cli.py apply-weights",
        "",
        "如需拒绝，无需操作。",
    ]

    subject = f"[林铛] 权重调节建议: {top_dim}↑ {bot_dim}↓"
    body = "\n".join(lines)

    try:
        send_text_email(subject, body)
        logger.info("[evolution_engine] 权重建议邮件已发送")
    except Exception as exc:
        logger.warning("[evolution_engine] 邮件发送失败: %r", exc)
