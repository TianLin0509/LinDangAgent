# knowledge/learning_optimizer.py
# -*- coding: utf-8 -*-
"""统一学习引擎 — Round 4-5: 配置生成 + 验证 + 采纳/回退。"""

import json
import logging
from datetime import datetime
from pathlib import Path

from knowledge.learning_config import (
    SAFETY_BOUNDS, DIMENSIONS, STAGING_DIR,
    ADOPTION_HIT_RATE_IMPROVEMENT, ADOPTION_NO_CLIFF_DROP,
    validate_weights, validate_rule_thresholds,
    ensure_staging, clear_staging, load_production_tree,
    save_staging_tree, promote_staging, save_learning_log,
    STAGING_PROMPT_PATH, STAGING_CHANGELOG, STAGING_RULES_PATH,
)

logger = logging.getLogger(__name__)


# ── Round 4: 应用 Proposals ──────────────────────────────────────

def apply_proposal(tree: dict, proposal: dict) -> tuple[dict, list[str]]:
    """将单条 proposal 应用到决策树副本上。

    返回 (modified_tree, errors)。errors 非空表示安全边界违规。
    """
    import copy
    new_tree = copy.deepcopy(tree)
    errors = []
    p_type = proposal.get("type")
    target = proposal.get("target", "")
    value = proposal.get("proposed_value", "")

    if p_type == "weight":
        # 找到目标维度
        dim = None
        for d in DIMENSIONS:
            if d in target:
                dim = d
                break
        if not dim:
            errors.append(f"未识别的权重目标: {target}")
            return new_tree, errors

        try:
            new_w = float(value)
        except (ValueError, TypeError):
            errors.append(f"权重值无效: {value}")
            return new_tree, errors

        new_tree.setdefault("weights", {})[dim] = new_w
        # Check only per-dimension bounds (not total sum) for a single weight proposal
        if new_w > SAFETY_BOUNDS["weight_max"]:
            errors.append(f"{dim} 权重 {new_w:.0%} 超过上限 50%")
        if new_w < SAFETY_BOUNDS["weight_min"]:
            errors.append(f"{dim} 权重 {new_w:.0%} 低于下限 5%")

    elif p_type == "rule":
        try:
            new_val = float(value)
        except (ValueError, TypeError):
            errors.append(f"规则阈值无效: {value}")
            return new_tree, errors

        # 映射到验证键名
        rule_map = {
            "fundamental_breaker": "fundamental_breaker",
            "熔断": "fundamental_breaker",
            "bucket": "bucket_cap",
            "木桶": "bucket_cap",
            "premortem": "premortem_cap",
            "预mortem": "premortem_cap",
        }
        rule_key = None
        for keyword, key in rule_map.items():
            if keyword in target.lower():
                rule_key = key
                break

        if rule_key:
            errs = validate_rule_thresholds({rule_key: new_val})
            if errs:
                errors.extend(errs)
        # Apply to tree's correction_rules
        rules = new_tree.get("correction_rules", {})
        for rname, rdata in rules.items():
            if any(k in rname.lower() for k in target.lower().split()):
                if "condition" in rdata:
                    for dim_key, cond in rdata["condition"].items():
                        for op in cond:
                            rdata["condition"][dim_key][op] = new_val
                elif "action" in rdata and "cap" in rdata["action"]:
                    rdata["action"]["cap"] = new_val

    elif p_type == "tree":
        # 决策树结构变更 — 存储为 JSON patch 格式，人工+验证后生效
        logger.info("[learn] tree structure proposal: %s", proposal.get("id"))
        # 基本验证: 不能删除整个维度
        for dim in DIMENSIONS:
            if dim in target and "删除" in str(value):
                errors.append(f"不允许删除整个维度: {dim}")

    elif p_type == "prompt":
        # Prompt 变更不改 tree，单独存储
        logger.info("[learn] prompt proposal stored for human review: %s", proposal.get("id"))

    return new_tree, errors


def _snapshot_value(tree: dict, proposal: dict) -> str:
    """提取与 proposal 相关的当前配置片段，用于 before/after diff。"""
    ptype = proposal.get("type")
    target = proposal.get("target", "")
    if ptype == "weight":
        return json.dumps(tree.get("weights", {}), ensure_ascii=False)
    elif ptype == "rule":
        rules = tree.get("correction_rules", {})
        # 找相关的规则
        relevant = {k: v for k, v in rules.items() if any(kw in k.lower() for kw in target.lower().split())}
        return json.dumps(relevant if relevant else rules, ensure_ascii=False)[:300]
    elif ptype == "tree":
        return f"决策树(当前维度数: {len(tree.get('trees', {}))})"
    return str(target)[:200]


def apply_all_proposals(proposals: list[dict], progress_cb=None) -> dict:
    """Round 4: 将所有采纳的 proposals 应用到 staging 区。

    返回: {staging_tree, prompt_proposals, errors, applied_count, diff}
    """
    ensure_staging()
    import copy
    original_tree = load_production_tree()
    tree = copy.deepcopy(original_tree)
    prompt_proposals = []
    all_errors = []
    applied = 0
    diff_entries = []

    for p in proposals:
        if p.get("type") == "prompt":
            prompt_proposals.append(p)
            applied += 1
            diff_entries.append({
                "proposal_id": p.get("id"),
                "type": "prompt",
                "target": p.get("target"),
                "status": "pending_human_approval",
                "before": "（原 prompt，待审批后才替换）",
                "after": str(p.get("proposed_value", ""))[:300],
            })
            continue

        before_snap = _snapshot_value(tree, p)
        new_tree, errors = apply_proposal(tree, p)
        if errors:
            all_errors.append({"proposal_id": p.get("id"), "errors": errors})
            if progress_cb:
                progress_cb(f"⚠️ {p.get('id')} 安全边界违规: {errors}")
            diff_entries.append({
                "proposal_id": p.get("id"),
                "type": p.get("type"),
                "target": p.get("target"),
                "status": "rejected_safety",
                "before": before_snap,
                "after": f"建议值: {p.get('proposed_value')}",
                "errors": errors,
            })
        else:
            tree = new_tree
            applied += 1
            after_snap = _snapshot_value(tree, p)
            diff_entries.append({
                "proposal_id": p.get("id"),
                "type": p.get("type"),
                "target": p.get("target"),
                "status": "applied",
                "before": before_snap,
                "after": after_snap,
            })

    # 保存到 staging
    save_staging_tree(tree)

    # 保存 prompt proposals (需人工审批)
    if prompt_proposals:
        STAGING_PROMPT_PATH.write_text(
            json.dumps(prompt_proposals, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # 写 changelog
    changelog_lines = [
        f"# 学习引擎配置变更 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"\n已应用 {applied} 条建议，{len(all_errors)} 条因安全边界被拒绝。",
    ]
    for p in proposals:
        if p.get("type") != "prompt" and p.get("id") not in {e["proposal_id"] for e in all_errors}:
            changelog_lines.append(f"\n- [{p.get('id')}] {p.get('type')}: {p.get('target')} → {p.get('proposed_value')}")
    if prompt_proposals:
        changelog_lines.append(f"\n⏳ {len(prompt_proposals)} 条 prompt 变更待人工审批")
    STAGING_CHANGELOG.write_text("\n".join(changelog_lines), encoding="utf-8")

    if progress_cb:
        progress_cb(f"Round 4 完成: {applied} 条应用, {len(all_errors)} 条拒绝, {len(prompt_proposals)} 条待审批")

    return {
        "staging_tree": tree,
        "original_tree": original_tree,
        "prompt_proposals": prompt_proposals,
        "errors": all_errors,
        "applied_count": applied,
        "diff": diff_entries,
    }


# ── Round 5: 验证 ────────────────────────────────────────────────

def check_adoption_criteria(
    old_hit_rate: float,
    new_hit_rate: float,
    old_by_category: dict,
    new_by_category: dict,
    old_calibration: float,
    new_calibration: float,
) -> dict:
    """检查是否满足采纳条件。

    返回: {adopted: bool, reason: str, details: dict}
    """
    reasons = []

    # 条件 1: 胜率提升 >= 3%
    improvement = new_hit_rate - old_hit_rate
    if improvement < ADOPTION_HIT_RATE_IMPROVEMENT:
        reasons.append(f"胜率提升 {improvement:.1f}% < {ADOPTION_HIT_RATE_IMPROVEMENT}% 门槛")

    # 条件 2: 无断崖下跌
    for cat in set(list(old_by_category.keys()) + list(new_by_category.keys())):
        old_hr = old_by_category.get(cat, {}).get("hit_rate", 0)
        new_hr = new_by_category.get(cat, {}).get("hit_rate", 0)
        if old_hr - new_hr > ADOPTION_NO_CLIFF_DROP:
            reasons.append(f"{cat} 类胜率断崖下跌: {old_hr:.1f}% → {new_hr:.1f}%")

    # 条件 3: 评分校准度改善
    if new_calibration < old_calibration:
        reasons.append(f"评分校准度退步: {old_calibration:.1f} → {new_calibration:.1f}")

    adopted = len(reasons) == 0
    return {
        "adopted": adopted,
        "reason": "; ".join(reasons) if reasons else "全部达标",
        "details": {
            "old_hit_rate": old_hit_rate,
            "new_hit_rate": new_hit_rate,
            "improvement": round(improvement, 1),
            "calibration_old": old_calibration,
            "calibration_new": new_calibration,
        },
    }


def run_validation(
    holdout_exams: list[dict],
    staging_tree: dict,
    delay_between: int = 30,
    progress_cb=None,
) -> dict:
    """Round 5: 在验证集上对比新旧配置。

    返回: {adopted, old_stats, new_stats, reason}
    """
    from knowledge.learning_backtester import run_single_backtest
    from knowledge.learning_config import (
        DECISION_TREE_PATH, STAGING_TREE_PATH,
        load_production_tree, save_staging_tree,
    )
    import shutil
    import time

    if progress_cb:
        progress_cb(f"Round 5: 验证集 {len(holdout_exams)} 只股票...")

    old_tree_backup = DECISION_TREE_PATH.with_suffix(".json.bak")

    results_new = []
    results_old_projected = []

    # 先用旧配置跑（当前生产配置）
    if progress_cb:
        progress_cb("Round 5: 旧配置回测...")

    for i, exam in enumerate(holdout_exams):
        r = run_single_backtest(exam, progress_cb)
        if r:
            results_old_projected.append(r)
        if i < len(holdout_exams) - 1 and delay_between > 0:
            time.sleep(delay_between)

    # 临时替换为新配置
    shutil.copy2(DECISION_TREE_PATH, old_tree_backup)
    try:
        save_staging_tree(staging_tree)
        # 将 staging tree 复制到生产位置
        shutil.copy2(STAGING_TREE_PATH, DECISION_TREE_PATH)

        # 清除 decision_tree 模块缓存
        try:
            from services.decision_tree import reload_tree
            reload_tree()
        except Exception:
            pass

        if progress_cb:
            progress_cb("Round 5: 新配置回测...")

        for i, exam in enumerate(holdout_exams):
            r = run_single_backtest(exam, progress_cb)
            if r:
                results_new.append(r)
            if i < len(holdout_exams) - 1 and delay_between > 0:
                time.sleep(delay_between)
    finally:
        # 恢复旧配置
        shutil.copy2(old_tree_backup, DECISION_TREE_PATH)
        old_tree_backup.unlink(missing_ok=True)
        try:
            from services.decision_tree import reload_tree
            reload_tree()
        except Exception:
            pass

    # 统计对比
    def calc_stats(results):
        total = len(results)
        hits = sum(1 for r in results if r["verdict"] == "hit")
        hit_rate = hits / total * 100 if total > 0 else 0
        by_cat = {}
        for r in results:
            c = r["category"]
            by_cat.setdefault(c, {"total": 0, "hits": 0})
            by_cat[c]["total"] += 1
            if r["verdict"] == "hit":
                by_cat[c]["hits"] += 1
        for v in by_cat.values():
            v["hit_rate"] = round(v["hits"] / v["total"] * 100, 1) if v["total"] > 0 else 0
        # 校准度: 高分股均α - 低分股均α
        high = [r["excess_return"] for r in results if r.get("weighted", 0) >= 70]
        low = [r["excess_return"] for r in results if r.get("weighted", 0) < 50]
        calibration = 0.0
        if high and low:
            calibration = (sum(high) / len(high)) - (sum(low) / len(low))
        return {"hit_rate": round(hit_rate, 1), "by_category": by_cat, "calibration": round(calibration, 2)}

    old_stats = calc_stats(results_old_projected)
    new_stats = calc_stats(results_new)

    criteria = check_adoption_criteria(
        old_hit_rate=old_stats["hit_rate"],
        new_hit_rate=new_stats["hit_rate"],
        old_by_category=old_stats["by_category"],
        new_by_category=new_stats["by_category"],
        old_calibration=old_stats["calibration"],
        new_calibration=new_stats["calibration"],
    )

    if criteria["adopted"]:
        promote_staging()
        try:
            from services.decision_tree import reload_tree
            reload_tree()
        except Exception:
            pass
        if progress_cb:
            progress_cb(f"✅ Round 5: 新配置采纳！胜率 {old_stats['hit_rate']}% → {new_stats['hit_rate']}%")
    else:
        clear_staging()
        if progress_cb:
            progress_cb(f"❌ Round 5: 未达标，回退。原因: {criteria['reason']}")

    return {
        "adopted": criteria["adopted"],
        "reason": criteria["reason"],
        "old_stats": old_stats,
        "new_stats": new_stats,
        "old_results": results_old_projected,  # 每只验证股票的详情（旧配置）
        "new_results": results_new,            # 每只验证股票的详情（新配置）
        "criteria_details": criteria.get("details", {}),
    }
