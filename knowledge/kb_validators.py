# -*- coding: utf-8 -*-
"""数据验证工具 — 知识库输入校验

为知识库各模块提供统一的输入验证函数，
在系统边界处拦截非法数据，避免垃圾数据入库。
"""

import re
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# 股票代码正则：6位数字 + 后缀
_STOCK_CODE_RE = re.compile(
    r"^(\d{6})\.(SH|SZ|BJ)$",
    re.IGNORECASE,
)

# 合法后缀映射（统一大写）
_SUFFIX_MAP = {
    "sh": "SH",
    "sz": "SZ",
    "bj": "BJ",
}


def validate_stock_code(code: str) -> str | None:
    """验证并标准化股票代码。

    合法格式：600000.SH, 000001.SZ, 830799.BJ
    返回标准化后的代码（大写后缀），非法则返回 None。
    """
    if not code or not isinstance(code, str):
        return None
    code = code.strip()
    m = _STOCK_CODE_RE.match(code)
    if not m:
        return None
    digits, suffix = m.group(1), m.group(2).lower()
    return f"{digits}.{_SUFFIX_MAP[suffix]}"


def validate_score(score, min_val: float = 0.0, max_val: float = 100.0) -> float:
    """验证并钳位评分值。

    - None / 非数值 → 返回 0.0
    - 超出范围 → 钳位到 [min_val, max_val]
    """
    if score is None:
        return 0.0
    try:
        val = float(score)
    except (TypeError, ValueError):
        return 0.0
    return max(min_val, min(val, max_val))


def validate_date_str(date_str: str, fmt: str = "%Y-%m-%d") -> str | None:
    """验证日期字符串格式。

    返回标准化后的日期字符串，非法则返回 None。
    """
    if not date_str or not isinstance(date_str, str):
        return None
    date_str = date_str.strip()[:10]  # 截取日期部分（去掉时间）
    try:
        dt = datetime.strptime(date_str, fmt)
        return dt.strftime(fmt)
    except ValueError:
        return None


def validate_case_id(case_id: str) -> bool:
    """验证 case_id 非空。"""
    return bool(case_id and isinstance(case_id, str) and case_id.strip())


def validate_direction(direction: str) -> str:
    """验证并标准化方向。

    合法值：bullish, bearish, neutral
    非法值 → 返回 "neutral"
    """
    if not direction or not isinstance(direction, str):
        return "neutral"
    d = direction.strip().lower()
    if d in ("bullish", "bearish", "neutral"):
        return d
    return "neutral"


def validate_regime(regime: str) -> str:
    """验证并标准化市场环境。

    合法值：bull, bear, shock, rotation
    非法值 → 返回 "shock"
    """
    if not regime or not isinstance(regime, str):
        return "shock"
    r = regime.strip().lower()
    if r in ("bull", "bear", "shock", "rotation"):
        return r
    return "shock"


def validate_sentiment(sentiment: str) -> str:
    """验证情感分类。

    合法值：bullish, bearish, neutral
    非法值 → 返回 "neutral"
    """
    return validate_direction(sentiment)


def validate_confidence(confidence, default: float = 0.5) -> float:
    """验证置信度 [0.0, 1.0]。"""
    if confidence is None:
        return default
    try:
        val = float(confidence)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, val))
