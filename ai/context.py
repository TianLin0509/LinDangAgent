"""
智能上下文摘要构建器
用于 MoE 辩论和自由提问等需要引用已有分析结果的场景
不做硬切片，而是按段落结构提取关键结论
"""

import re


def _extract_conclusions(text: str, max_lines: int = 40) -> str:
    """
    从分析文本中智能提取结论性内容
    优先保留：标题行、数据密集行、结论段、评分行、表格行

    策略：当原文超过 max_lines 时，保留
      - 前 5 行（引言/概览）
      - 所有优先级行（标题、表格、关键词、含数字/百分比的行）
      - 最后 10 行（结论段）
    最终去重并按原始顺序排列，裁剪到 max_lines。
    """
    if not text:
        return ""

    lines = text.strip().split("\n")
    # 过滤纯空行但保留原始索引
    indexed = [(i, line) for i, line in enumerate(lines) if line.strip()]

    if not indexed:
        return ""

    # 如果总行数本就在限制内，直接返回
    if len(indexed) <= max_lines:
        return "\n".join(line for _, line in indexed)

    # 高优先级关键词——包含这些的行一定保留
    priority_keywords = [
        "结论", "评分", "评级", "裁决", "判断", "建议", "目标价",
        "止损", "支撑", "压力", "置信度", "核心",
        "乐观", "中性", "悲观", "通过", "不通过", "谨慎",
        "看多", "看空", "震荡", "买入", "减持", "回避",
        "预期差", "超预期", "低预期", "催化", "催化剂",
        "风险", "概率", "评分",
    ]

    # 数字/百分比模式：匹配含数据的行（如 12.5%, 3.2倍, ¥15.8, +2.3%）
    _num_pattern = re.compile(
        r'[\d]+\.?\d*\s*[%％倍万亿元]|[+-]?\d+\.?\d*%|¥\d|￥\d'
    )

    # 标记每行是否为优先行
    priority_indices = set()

    for idx, line in indexed:
        stripped = line.strip()
        # 标题行
        if stripped.startswith("#") or stripped.startswith("**"):
            priority_indices.add(idx)
            continue
        # 表格行
        if "|" in stripped and stripped.count("|") >= 2:
            priority_indices.add(idx)
            continue
        # 关键词行
        if any(kw in stripped for kw in priority_keywords):
            priority_indices.add(idx)
            continue
        # 数据密集行（含数字/百分比）
        if _num_pattern.search(stripped):
            priority_indices.add(idx)
            continue

    # 构建保留集合（按原始索引）
    keep_indices = set()

    # 1. 前 5 行（引言）
    for idx, _ in indexed[:5]:
        keep_indices.add(idx)

    # 2. 所有优先级行
    keep_indices.update(priority_indices)

    # 3. 最后 10 行（结论）
    for idx, _ in indexed[-10:]:
        keep_indices.add(idx)

    # 按原始顺序排列
    kept_indexed = sorted(
        [(idx, line) for idx, line in indexed if idx in keep_indices],
        key=lambda x: x[0],
    )

    # 如果仍超过 max_lines，按优先级裁剪
    if len(kept_indexed) > max_lines:
        # 必保：前5 + 后10 = 最多15行
        head_set = {idx for idx, _ in indexed[:5]}
        tail_set = {idx for idx, _ in indexed[-10:]}
        must_keep = head_set | tail_set

        # 优先级行按"关键词命中>表格>标题>数字"排序取前 (max_lines - len(must_keep))
        remaining_budget = max_lines - len(must_keep)
        priority_candidates = [
            (idx, line) for idx, line in kept_indexed
            if idx not in must_keep
        ]
        # 简单截断（已按原文顺序，保持逻辑连贯）
        priority_candidates = priority_candidates[:remaining_budget]

        final_indices = must_keep | {idx for idx, _ in priority_candidates}
        kept_indexed = sorted(
            [(idx, line) for idx, line in indexed if idx in final_indices],
            key=lambda x: x[0],
        )

    # 在不连续处插入省略标记
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
    """
    将分析模块的结果构建为结构化上下文摘要
    按模块提取关键结论，智能压缩

    Parameters:
        analyses: 各模块分析结果字典
        max_per_module: 每模块最多保留行数（默认 40）
        max_total_chars: 总字符数上限（默认 8000），防止超出 token 限制
    """
    parts = []

    module_map = {
        "comprehensive": "综合投研报告",
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

    # 如果超过总字符限制，按比例缩减每个模块
    if len(result) > max_total_chars and len(parts) > 1:
        # 按比例缩减 max_per_module 重新提取
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

        # 硬截断兜底
        if len(result) > max_total_chars:
            result = result[:max_total_chars] + "\n...(上下文已截断)"

    return result
