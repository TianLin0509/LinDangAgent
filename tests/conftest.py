"""Shared fixtures for LinDangAgent test suite."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 注册功能性测试 fixtures（conftest_functional.py）
pytest_plugins = ["tests.conftest_functional"]


# ── Mock Tushare ──────────────────────────────────────────────────


@pytest.fixture()
def mock_tushare():
    """Patch tushare_client so tests never hit real API."""
    with patch("data.tushare_client.get_pro") as mock_pro:
        mock_pro.return_value = MagicMock()
        yield mock_pro


# ── Mock AI Client ────────────────────────────────────────────────


@pytest.fixture()
def mock_ai():
    """Patch ai.client so tests never call real LLM."""
    with patch("ai.client.call_ai_stream") as mock_stream, \
         patch("ai.client.call_ai") as mock_call, \
         patch("ai.client.get_ai_client") as mock_get:
        mock_get.return_value = (MagicMock(), {"model": "test"}, None)
        mock_call.return_value = ("mock response", None)
        mock_stream.return_value = iter(["mock ", "response"])
        yield {
            "call_ai": mock_call,
            "call_ai_stream": mock_stream,
            "get_ai_client": mock_get,
        }


# ── Sample Score Texts ────────────────────────────────────────────


@pytest.fixture()
def score_block_100():
    """Standard score block with /100 format."""
    return """一些分析文字...

<<<SCORES>>>
基本面: 72/100
预期差: 85/100
资金面: 68/100
技术面: 60/100
S级豁免: 否
致命缺陷: 无
操作评级: 侦察待命
<<<END_SCORES>>>

<<<REPORT_END>>>
核心摘要内容"""


@pytest.fixture()
def score_block_10():
    """Old 10-point scale format."""
    return """<<<SCORES>>>
基本面: 7/10
预期差: 8/10
资金面: 6/10
技术面: 5/10
<<<END_SCORES>>>"""


@pytest.fixture()
def score_block_plain():
    """Plain number format (no /N suffix)."""
    return """<<<SCORES>>>
基本面: 72
预期差: 85
资金面: 68
技术面: 60
<<<END_SCORES>>>"""


@pytest.fixture()
def score_block_plain_small():
    """Plain numbers <=10, should auto-detect as 10-point scale."""
    return """<<<SCORES>>>
基本面: 7
预期差: 8
资金面: 6
技术面: 5
<<<END_SCORES>>>"""


@pytest.fixture()
def score_no_block():
    """No SCORES markers — parser should fallback to full-text scan."""
    return """## 评分
基本面: 65/100
预期差: 70/100
资金面: 55/100
技术面: 80/100
S级豁免: 否
致命缺陷: 无"""


@pytest.fixture()
def score_fatal():
    """Score with fatal flaw and S-exempt."""
    return """<<<SCORES>>>
基本面: 15/100
预期差: 60/100
资金面: 45/100
技术面: 50/100
S级豁免: 否
致命缺陷: 有
<<<END_SCORES>>>"""


@pytest.fixture()
def score_s_exempt():
    """Low fundamentals but S-tier exempt."""
    return """<<<SCORES>>>
基本面: 18/100
预期差: 90/100
资金面: 75/100
技术面: 70/100
S级豁免: 是
致命缺陷: 无
<<<END_SCORES>>>"""


@pytest.fixture()
def score_empty():
    """Text with no parseable scores."""
    return """这是一段没有评分的纯文本分析。"""
