# -*- coding: utf-8 -*-
"""知识库公共工具函数 — 通用计算、AI 输出解析

消除各模块间重复的胜率计算、JSON 解析等样板代码。
"""

import json
import logging
import re

logger = logging.getLogger(__name__)

# ── AI JSON 解析 ──────────────────────────────────────────────────

# 匹配 AI 输出中的 markdown 代码块包裹
_JSON_FENCE_START = re.compile(r"^```(?:json)?\s*", re.MULTILINE)
_JSON_FENCE_END = re.compile(r"\s*```\s*$", re.MULTILINE)


def parse_ai_json(raw: str) -> dict | list | None:
    """清理 AI 返回的 JSON（去 markdown 代码块包裹），解析后返回。

    Args:
        raw: AI 返回的原始文本，可能包含 ```json ... ``` 包裹

    Returns:
        解析后的 dict 或 list，解析失败返回 None
    """
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip()
    text = _JSON_FENCE_START.sub("", text)
    text = _JSON_FENCE_END.sub("", text)
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("[kb_utils] AI JSON 解析失败: %s", text[:200])
        return None


def parse_ai_json_strict(raw: str, expected_type: type = dict) -> dict | list:
    """严格解析 AI JSON，解析失败返回空容器而非 None。

    Args:
        raw: AI 返回的原始文本
        expected_type: 期望的类型（dict 或 list）

    Returns:
        解析后的对象，类型不匹配或解析失败返回空容器
    """
    result = parse_ai_json(raw)
    if result is None or not isinstance(result, expected_type):
        return expected_type()
    return result


# ── 胜率计算 ──────────────────────────────────────────────────────

def calc_hit_rate(
    items: list[dict],
    hit_field: str = "hit_10d",
    min_samples: int = 3,
) -> float | None:
    """通用胜率计算。

    Args:
        items: 记录列表
        hit_field: 布尔/整数命中字段名
        min_samples: 最小样本数，不足返回 None

    Returns:
        胜率百分比（0-100），样本不足返回 None
    """
    valid = [x for x in items if x.get(hit_field) is not None]
    if len(valid) < min_samples:
        return None
    hits = sum(1 for x in valid if x[hit_field])
    return round(hits / len(valid) * 100, 1)


def calc_directional_hit_rate(
    items: list[dict],
    direction_field: str = "direction",
    hit_field: str = "hit_10d",
    min_samples: int = 3,
) -> float | None:
    """计算有方向性判断的胜率（排除 neutral）。

    Args:
        items: 记录列表
        direction_field: 方向字段名
        hit_field: 命中字段名
        min_samples: 最小样本数

    Returns:
        胜率百分比，样本不足返回 None
    """
    directional = [
        x for x in items
        if x.get(direction_field) in ("bullish", "bearish")
        and x.get(hit_field) is not None
    ]
    if len(directional) < min_samples:
        return None
    hits = sum(1 for x in directional if x[hit_field])
    return round(hits / len(directional) * 100, 1)


def calc_bucket_stats(
    items: list[dict],
    hit_field: str = "hit_10d",
    return_field: str = "return_10d",
) -> dict:
    """通用分组统计（胜率 + 平均收益）。

    Returns:
        {
            "total": int,
            "directional": int,
            "hits": int,
            "hit_rate": float | None,
            "avg_return": float | None,
        }
    """
    directional = [
        x for x in items
        if x.get("direction") in ("bullish", "bearish")
    ]
    valid_hit = [x for x in directional if x.get(hit_field) is not None]
    valid_return = [x for x in items if x.get(return_field) is not None]

    hits = sum(1 for x in valid_hit if x[hit_field])
    hit_rate = round(hits / len(valid_hit) * 100, 1) if valid_hit else None
    avg_return = (
        round(sum(x[return_field] for x in valid_return) / len(valid_return), 2)
        if valid_return else None
    )

    return {
        "total": len(items),
        "directional": len(directional),
        "hits": hits,
        "hit_rate": hit_rate,
        "avg_return": avg_return,
    }


# ── 通用工具 ──────────────────────────────────────────────────────

def safe_json_loads(text: str, default=None):
    """安全的 JSON 解析，失败返回默认值。"""
    if not text:
        return default
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return default


def truncate_text(text: str, max_chars: int = 500, suffix: str = "...") -> str:
    """截断文本到指定长度。"""
    if not text or len(text) <= max_chars:
        return text or ""
    return text[:max_chars - len(suffix)] + suffix
