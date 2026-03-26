"""
智能上下文摘要构建器
用于 MoE 辩论等需要引用已有分析结果的场景
"""

import re


def _extract_conclusions(text: str, max_lines: int = 40) -> str:
    if not text:
        return ""

    lines = text.strip().split("\n")
    indexed = [(i, line) for i, line in enumerate(lines) if line.strip()]

    if not indexed:
        return ""

    if len(indexed) <= max_lines:
        return "\n".join(line for _, line in indexed)

    priority_keywords = [
        "结论", "评分", "评级", "裁决", "判断", "建议", "目标价",
        "止损", "支撑", "压力", "置信度", "核心",
        "乐观", "中性", "悲观", "通过", "不通过", "谨慎",
        "看多", "看空", "震荡", "买入", "减持", "回避",
        "预期差", "超预期", "低预期", "催化", "催化剂",
        "风险", "概率", "评分",
    ]

    _num_pattern = re.compile(
        r'[\d]+\.?\d*\s*[%％倍万亿元]|[+-]?\d+\.?\d*%|¥\d|￥\d'
    )

    priority_indices = set()
    for idx, line in indexed:
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("**"):
            priority_indices.add(idx)
            continue
        if "|" in stripped and stripped.count("|") >= 2:
            priority_indices.add(idx)
            continue
        if any(kw in stripped for kw in priority_keywords):
            priority_indices.add(idx)
            continue
        if _num_pattern.search(stripped):
            priority_indices.add(idx)
            continue

    keep_indices = set()
    for idx, _ in indexed[:5]:
        keep_indices.add(idx)
    keep_indices.update(priority_indices)
    for idx, _ in indexed[-10:]:
        keep_indices.add(idx)

    kept_indexed = sorted(
        [(idx, line) for idx, line in indexed if idx in keep_indices],
        key=lambda x: x[0],
    )

    if len(kept_indexed) > max_lines:
        head_set = {idx for idx, _ in indexed[:5]}
        tail_set = {idx for idx, _ in indexed[-10:]}
        must_keep = head_set | tail_set
        remaining_budget = max_lines - len(must_keep)
        priority_candidates = [
            (idx, line) for idx, line in kept_indexed
            if idx not in must_keep
        ]
        priority_candidates = priority_candidates[:remaining_budget]
        final_indices = must_keep | {idx for idx, _ in priority_candidates}
        kept_indexed = sorted(
            [(idx, line) for idx, line in indexed if idx in final_indices],
            key=lambda x: x[0],
        )

    result = []
    prev_idx = -1
    for idx, line in kept_indexed:
        if prev_idx >= 0 and idx - prev_idx > 1:
            result.append("...")
        result.append(line.strip())
        prev_idx = idx

    return "\n".join(result)


def build_analysis_context(analyses: dict, max_per_module: int = 40,
                           max_total_chars: int = 8000) -> str:
    parts = []
    module_map = {
        "expectation":   "预期差分析",
        "trend":         "趋势研判",
        "fundamentals":  "基本面剖析",
        "sentiment":     "舆情情绪",
        "sector":        "板块联动",
        "holders":       "股东/机构",
    }

    for key, label in module_map.items():
        text = analyses.get(key, "")
        if not text or text.startswith("⚠️"):
            continue
        summary = _extract_conclusions(text, max_lines=max_per_module)
        if summary:
            parts.append(f"【{label}摘要】\n{summary}")

    if not parts:
        return "暂无已完成的分析结果"

    result = "\n\n".join(parts)

    if len(result) > max_total_chars and len(parts) > 1:
        ratio = max_total_chars / len(result)
        reduced_max = max(15, int(max_per_module * ratio))
        parts = []
        for key, label in module_map.items():
            text = analyses.get(key, "")
            if not text or text.startswith("⚠️"):
                continue
            summary = _extract_conclusions(text, max_lines=reduced_max)
            if summary:
                parts.append(f"【{label}摘要】\n{summary}")
        result = "\n\n".join(parts)
        if len(result) > max_total_chars:
            result = result[:max_total_chars] + "\n...(上下文已截断)"

    return result
