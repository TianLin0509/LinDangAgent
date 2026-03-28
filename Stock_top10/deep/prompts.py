"""Prompt 构建器 — 复用主项目 ai/prompts.py 的完整版本
Stock_top10 深度分析模块直接委托给主项目 prompt builder，
避免维护两套 prompt 系统导致质量不一致。
"""

import sys
import os

# 确保主项目根目录在 sys.path 中
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# 直接复用主项目的 prompt builder
from ai.prompts import (  # noqa: E402
    build_expectation_prompt,
    build_trend_prompt,
    build_fundamentals_prompt,
    build_sentiment_prompt,
    build_sector_prompt,
    build_holders_prompt,
)

__all__ = [
    "build_expectation_prompt",
    "build_trend_prompt",
    "build_fundamentals_prompt",
    "build_sentiment_prompt",
    "build_sector_prompt",
    "build_holders_prompt",
]
