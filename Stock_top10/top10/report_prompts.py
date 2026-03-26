"""Reuse the single-stock report prompt from Stock_lite."""

from __future__ import annotations

import sys
from pathlib import Path


_CANDIDATE_ROOTS = [
    Path(__file__).resolve().parents[2] / "Stock_lite",
    Path(__file__).resolve().parents[2] / "app",
]
for _stock_lite_root in _CANDIDATE_ROOTS:
    if _stock_lite_root.exists() and str(_stock_lite_root) not in sys.path:
        sys.path.insert(0, str(_stock_lite_root))

from ai.prompts_report import REPORT_SYSTEM as REPORT_SYSTEM  # noqa: E402
from ai.prompts_report import build_report_prompt as _build_stock_lite_report_prompt  # noqa: E402


def build_report_prompt(
    name: str,
    stock_code: str,
    context: dict,
    price_snapshot: str,
    indicators_section: str,
) -> tuple[str, str]:
    """Delegate to Stock_lite so Top10 and single-stock analysis stay in sync."""
    return _build_stock_lite_report_prompt(
        name=name,
        ts_code=stock_code,
        context=context,
        price_snapshot=price_snapshot,
        indicators_section=indicators_section,
    )
