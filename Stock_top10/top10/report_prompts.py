"""复用主项目 LinDangAgent ���研报 prompt（含林彪军事风格）。"""

from __future__ import annotations

import sys
from pathlib import Path

# 确保主项目在 sys.path 中（优先于 Stock_lite）
_MAIN_PROJECT = Path(__file__).resolve().parents[2]
if str(_MAIN_PROJECT) not in sys.path:
    sys.path.insert(0, str(_MAIN_PROJECT))

from ai.prompts_report import REPORT_SYSTEM as REPORT_SYSTEM  # noqa: E402
from ai.prompts_report import build_report_prompt as _main_build_report_prompt  # noqa: E402


def build_report_prompt(
    name: str,
    stock_code: str,
    context: dict,
    price_snapshot: str,
    indicators_section: str,
) -> tuple[str, str]:
    """Delegate to main project prompt（林彪军事风格 + 财报情报 + 研报融合）."""
    return _main_build_report_prompt(
        name=name,
        ts_code=stock_code,
        context=context,
        price_snapshot=price_snapshot,
        indicators_section=indicators_section,
    )
