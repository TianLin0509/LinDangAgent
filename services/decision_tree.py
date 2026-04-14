"""
Decision Tree Scoring Engine
Loads the decision_tree.json config, computes weighted composite scores,
applies correction rules, and formats trees for prompt injection.
"""

import json
from pathlib import Path
from typing import Optional

_TREE_PATH = Path(__file__).resolve().parent.parent / "data" / "knowledge" / "decision_tree.json"
_cache: Optional[dict] = None

_DIMS = ("基本面", "预期差", "资金面", "技术面")


def load_tree(path=None) -> dict:
    """Load and cache the JSON config. Returns the full config dict."""
    global _cache
    if _cache is None or path is not None:
        target = Path(path) if path else _TREE_PATH
        with open(target, encoding="utf-8") as f:
            data = json.load(f)
        if path is None:
            _cache = data
        return data
    return _cache


def reload_tree() -> dict:
    """Force reload after evolution engine updates."""
    global _cache
    _cache = None
    return load_tree()


def compute_weighted(scores: dict, weights: dict) -> float:
    """
    Weighted composite: sum(score * weight) / sum(weights), rounded to 1 decimal.
    Only dimensions present in both scores and weights are included.
    """
    total_weight = 0.0
    total_score = 0.0
    for dim, weight in weights.items():
        if dim in scores:
            total_score += scores[dim] * weight
            total_weight += weight
    if total_weight == 0:
        return 0.0
    return round(total_score / total_weight, 1)


def apply_corrections(scores: dict, rules: dict, high_prob_fatal_count: int = 0) -> dict:
    """
    Apply correction rules in order:
    1. resonance bonus (+3): 预期差>=75 AND 资金面>=70
    2. divergence penalty (-5): 预期差>=75 AND 资金面<=45
    3. fundamental circuit breaker: 基本面<=25 → cap composite at 30
    4. bucket effect: any dim <=30 → cap composite at 60
    5. premortem cap: high_prob_fatal>=1 → cap composite at 70

    Returns modified copy with flag fields:
      - _composite: weighted composite before corrections
      - _final: final composite after corrections
      - _resonance_bonus: True if resonance rule fired
      - _divergence_penalty: True if divergence rule fired
      - _fundamental_breaker: True if fundamental circuit breaker fired
      - _bucket_capped: True if bucket effect fired
      - _premortem_cap: True if premortem cap fired
    """
    config = load_tree()
    weights = config["weights"]

    # Extract cap values from rules dict (fall back to spec defaults)
    fundamental_cap = rules.get("fundamental_circuit_breaker", {}).get("cap", 30)
    bucket_cap = rules.get("bucket_effect", {}).get("cap", 60)
    premortem_cap_val = rules.get("premortem_cap", {}).get("cap", 70)

    # Compute base composite
    composite = compute_weighted(scores, weights)

    result = dict(scores)
    result["_composite"] = composite

    # Rule 1: resonance bonus
    if scores.get("预期差", 0) >= 75 and scores.get("资金面", 0) >= 70:
        composite += 3
        result["_resonance_bonus"] = True

    # Rule 2: divergence penalty
    if scores.get("预期差", 0) >= 75 and scores.get("资金面", 0) <= 45:
        composite -= 5
        result["_divergence_penalty"] = True

    # Rule 3: fundamental circuit breaker — strongest override, check first among caps
    if scores.get("基本面", 100) <= 25:
        composite = min(composite, fundamental_cap)
        result["_fundamental_breaker"] = True
    # Rule 4: bucket effect — only if fundamental breaker not triggered
    elif any(scores.get(d, 100) <= 30 for d in _DIMS):
        composite = min(composite, bucket_cap)
        result["_bucket_capped"] = True

    # Rule 5: premortem cap
    if high_prob_fatal_count >= 1:
        composite = min(composite, premortem_cap_val)
        result["_premortem_cap"] = True

    composite = max(0.0, min(100.0, composite))
    result["_final"] = round(composite, 1)
    return result


def format_tree_for_prompt(trees: dict) -> str:
    """Format decision trees as readable text for prompt injection."""
    lines = []
    for dim, nodes in trees.items():
        lines.append(f"【{dim}决策树】")
        for q_id, node in nodes.items():
            lines.append(f"  {q_id}: {node['question']}")
            for branch, outcome in node["branches"].items():
                if outcome.get("terminal"):
                    if "score_range" in outcome:
                        detail = f"得分区间 {outcome['score_range'][0]}-{outcome['score_range'][1]}"
                    elif "score_cap" in outcome:
                        detail = f"得分上限 {outcome['score_cap']}"
                    elif "modifier_range" in outcome:
                        detail = f"修正 {outcome['modifier_range'][0]}~{outcome['modifier_range'][1]}"
                    elif "modifier" in outcome:
                        detail = f"修正 {outcome['modifier']:+d}" if outcome["modifier"] != 0 else "修正 0"
                    else:
                        detail = "终止"
                    lines.append(f"    → {branch}: {detail} [终止]")
                else:
                    mods = []
                    if "next" in outcome:
                        mods.append(f"→{outcome['next']}")
                    if "modifier" in outcome:
                        mods.append(f"修正{outcome['modifier']:+d}")
                    if "modifier_range" in outcome:
                        mods.append(f"修正{outcome['modifier_range'][0]}~{outcome['modifier_range'][1]}")
                    if "base_score" in outcome:
                        mods.append(f"基础分{outcome['base_score']}")
                    lines.append(f"    → {branch}: {' '.join(mods)}")
        lines.append("")
    return "\n".join(lines)


def record_tree_path(dim: str, steps: list, final_score: int) -> str:
    """
    Format traversal path like:
    "预期差: 是→A类→30天内→未定价→单季超预期→75分"
    """
    path_str = "→".join(str(s) for s in steps)
    return f"{dim}: {path_str}→{final_score}分"
