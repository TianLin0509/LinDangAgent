# knowledge/learning_config.py
# -*- coding: utf-8 -*-
"""统一学习引擎 — 配置、常量、安全边界、staging 管理。"""

import json
import shutil
from pathlib import Path

from knowledge.kb_config import BASE_DIR, KNOWLEDGE_DIR

# ── 路径 ──────────────────────────────────────────────────────────
STAGING_DIR = KNOWLEDGE_DIR / "staging"
LEARNING_LOG_DIR = KNOWLEDGE_DIR / "learning_log"
DECISION_TREE_PATH = KNOWLEDGE_DIR / "decision_tree.json"
STAGING_TREE_PATH = STAGING_DIR / "decision_tree.json"
STAGING_RULES_PATH = STAGING_DIR / "correction_rules.json"
STAGING_PROMPT_PATH = STAGING_DIR / "prompt_patches.json"
STAGING_CHANGELOG = STAGING_DIR / "changelog.md"

# ── 选题参数 ──────────────────────────────────────────────────────
FAMILIAR_RATIO = 0.70
EXPLORE_RATIO = 0.30
MIN_TURNOVER_20D = 5000_0000
MIN_VOLATILITY_20D = 1.0       # 振幅均值 >= 1%（蓝筹日均~1.3%）
MIN_ABS_CHANGE_20D = 5.0       # 或区间涨跌幅绝对值 >= 5%
EXAM_DATE_RANGE = (15, 90)
EXAM_DATE_SLOTS = 5
HOLDOUT_RATIO = 0.30

# ── 安全边界 ──────────────────────────────────────────────────────
SAFETY_BOUNDS = {
    "weight_max": 0.50,
    "weight_min": 0.05,
    "fundamental_breaker_range": (15, 35),
    "bucket_cap_range": (20, 40),
    "premortem_cap_range": (60, 80),
    "tree_questions_min": 3,
    "tree_questions_max": 8,
    "prompt_patch_max_ratio": 0.20,
}

DIMENSIONS = ["预期差", "技术面", "基本面", "资金面"]

# ── 采纳门槛 ──────────────────────────────────────────────────────
ADOPTION_HIT_RATE_IMPROVEMENT = 3.0
ADOPTION_NO_CLIFF_DROP = 15.0


# ── 验证函数 ──────────────────────────────────────────────────────

def validate_weights(weights: dict) -> list[str]:
    """验证权重是否在安全边界内。返回错误列表，空=通过。"""
    errors = []
    for dim in DIMENSIONS:
        w = weights.get(dim, 0)
        if w > SAFETY_BOUNDS["weight_max"]:
            errors.append(f"{dim} 权重 {w:.0%} 超过上限 50%")
        if w < SAFETY_BOUNDS["weight_min"]:
            errors.append(f"{dim} 权重 {w:.0%} 低于下限 5%")
    total = sum(weights.get(d, 0) for d in DIMENSIONS)
    if abs(total - 1.0) > 0.01:
        errors.append(f"权重总和 {total:.0%} != 100%")
    return errors


def validate_rule_thresholds(rules: dict) -> list[str]:
    """验证修正规则阈值。"""
    errors = []
    checks = [
        ("fundamental_breaker", "fundamental_breaker_range", "熔断线"),
        ("bucket_cap", "bucket_cap_range", "木桶线"),
        ("premortem_cap", "premortem_cap_range", "预mortem封顶"),
    ]
    for key, bound_key, label in checks:
        val = rules.get(key)
        if val is None:
            continue
        lo, hi = SAFETY_BOUNDS[bound_key]
        if not (lo <= val <= hi):
            errors.append(f"{label} {val} 不在 [{lo}, {hi}] 范围内")
    return errors


def validate_tree_structure(tree: dict) -> list[str]:
    """验证决策树结构。"""
    errors = []
    mn, mx = SAFETY_BOUNDS["tree_questions_min"], SAFETY_BOUNDS["tree_questions_max"]
    for dim in DIMENSIONS:
        if dim not in tree:
            continue
        qs = tree[dim].get("questions", [])
        if len(qs) > mx:
            errors.append(f"{dim} 问题数 {len(qs)} 超过上限 {mx}")
        if len(qs) < mn:
            errors.append(f"{dim} 问题数 {len(qs)} 低于下限 {mn}")
    return errors


def validate_prompt_patch(patch_content: str, original_prompt: str) -> list[str]:
    """验证 prompt 修改幅度不超过原 prompt 的 20%。"""
    errors = []
    ratio = SAFETY_BOUNDS["prompt_patch_max_ratio"]
    if len(patch_content) > len(original_prompt) * ratio:
        errors.append(f"Prompt 修改 {len(patch_content)} 字符超过原文 {len(original_prompt)} 的 {ratio:.0%} 上限")
    return errors


# ── Staging 管理 ──────────────────────────────────────────────────

def ensure_staging():
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    LEARNING_LOG_DIR.mkdir(parents=True, exist_ok=True)


def clear_staging():
    if STAGING_DIR.exists():
        shutil.rmtree(STAGING_DIR)
    STAGING_DIR.mkdir(parents=True, exist_ok=True)


def load_production_tree() -> dict:
    return json.loads(DECISION_TREE_PATH.read_text(encoding="utf-8"))


def save_staging_tree(tree: dict):
    ensure_staging()
    STAGING_TREE_PATH.write_text(
        json.dumps(tree, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def promote_staging():
    if STAGING_TREE_PATH.exists():
        shutil.copy2(STAGING_TREE_PATH, DECISION_TREE_PATH)
    if STAGING_RULES_PATH.exists():
        rules = json.loads(STAGING_RULES_PATH.read_text(encoding="utf-8"))
        tree = load_production_tree()
        tree["correction_rules"] = rules
        DECISION_TREE_PATH.write_text(
            json.dumps(tree, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def save_learning_log(log_data: dict, mode: str, count: int):
    ensure_staging()
    from datetime import date
    filename = f"{date.today().isoformat()}_{mode}_{count}.json"
    path = LEARNING_LOG_DIR / filename
    path.write_text(json.dumps(log_data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path
