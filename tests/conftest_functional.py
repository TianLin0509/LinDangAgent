"""Shared fixtures for functional tests — K线工厂、DB管理器、案例卡片工厂等。

pytest 自动发现 conftest*.py，无需 import。
"""
from __future__ import annotations

import json
import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest


# ══════════════════════════════════════════════════════════════════
# K 线工厂
# ══════════════════════════════════════════════════════════════════

def make_kline_df(trend="bull", days=60, base_price=15.0, start_date="2026-03-01"):
    """生成逼真 K线 DataFrame。

    trend: "bull"(日均+0.3%), "bear"(日均-0.3%), "shock"(随机±0.5%)
    返回格式匹配 get_price_df: 列=["日期","开盘","最高","最低","收盘","成交量","涨跌幅"]
    日期格式: "YYYYMMDD" 字符串, 升序排列
    """
    rng = random.Random(42)
    base_volume = 500_000

    rows = []
    close = base_price
    dt = datetime.strptime(start_date, "%Y-%m-%d")

    for i in range(days):
        # 日收益率
        if trend == "bull":
            daily_ret = 0.003 + rng.gauss(0, 0.005)
        elif trend == "bear":
            daily_ret = -0.003 + rng.gauss(0, 0.005)
        else:  # shock
            daily_ret = rng.gauss(0, 0.005)

        prev_close = close
        close = round(prev_close * (1 + daily_ret), 2)
        open_price = round(prev_close * (1 + rng.gauss(0, 0.002)), 2)
        spread = rng.uniform(0.01, 0.03)
        high = round(max(open_price, close) * (1 + spread), 2)
        low = round(min(open_price, close) * (1 - spread), 2)
        volume = int(base_volume * (1 + rng.uniform(-0.3, 0.3)))
        change_pct = round(daily_ret * 100, 2)

        date_str = dt.strftime("%Y%m%d")
        rows.append({
            "日期": date_str,
            "开盘": open_price,
            "最高": high,
            "最低": low,
            "收盘": close,
            "成交量": volume,
            "涨跌幅": change_pct,
        })
        dt += timedelta(days=1)
        # 跳过周末
        while dt.weekday() >= 5:
            dt += timedelta(days=1)

    return pd.DataFrame(rows)


# ── K 线 Fixtures ────────────────────────────────────────────────

@pytest.fixture()
def kline_bull():
    return make_kline_df("bull", 60, 15.0)


@pytest.fixture()
def kline_bear():
    return make_kline_df("bear", 60, 30.0)


@pytest.fixture()
def kline_shock():
    return make_kline_df("shock", 60, 20.0)


@pytest.fixture()
def kline_insufficient():
    return make_kline_df("shock", 3, 20.0)


# ══════════════════════════════════════════════════════════════════
# DB Manager（临时目录，隔离生产数据）
# ══════════════════════════════════════════════════════════════════

@pytest.fixture()
def func_db_manager(tmp_path):
    from knowledge.kb_db import (
        DBManager, CASE_MEMORY_SCHEMA, INTEL_MEMORY_SCHEMA,
        KLINE_DIARY_SCHEMA, THESIS_JOURNAL_SCHEMA, WISDOM_SCHEMA, OUTCOMES_SCHEMA,
    )
    mgr = DBManager()
    kd = tmp_path / "data" / "knowledge"
    kd.mkdir(parents=True)
    mgr.register("case_memory", kd / "case_memory.db", CASE_MEMORY_SCHEMA)
    mgr.register("intel_memory", kd / "intel_memory.db", INTEL_MEMORY_SCHEMA)
    mgr.register("kline_diary", kd / "kline_diary.db", KLINE_DIARY_SCHEMA)
    mgr.register("thesis_journal", kd / "thesis_journal.db", THESIS_JOURNAL_SCHEMA)
    mgr.register("wisdom", kd / "wisdom.db", WISDOM_SCHEMA)
    mgr.register("outcomes", kd / "outcomes.db", OUTCOMES_SCHEMA)
    yield mgr, tmp_path
    mgr.close()


# ══════════════════════════════════════════════════════════════════
# Reports DB with Markdown（模拟 reports.db + 报告文件）
# ══════════════════════════════════════════════════════════════════

@pytest.fixture()
def reports_db_with_markdown(tmp_path):
    """创建包含 5 个报告的 reports.db + 对应的 markdown 文件。"""
    reports_dir = tmp_path / "storage"
    reports_dir.mkdir(parents=True)
    md_dir = tmp_path / "reports"
    md_dir.mkdir(parents=True)

    db_path = reports_dir / "reports.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            report_id TEXT PRIMARY KEY,
            stock_name TEXT,
            stock_code TEXT,
            summary TEXT,
            markdown_path TEXT,
            created_at TEXT
        )
    """)

    report_date = (datetime.now() - timedelta(days=15)).strftime("%Y-%m-%d %H:%M:%S")

    # 5 个报告，分数分别用 /10 格式
    reports = [
        ("rpt_001", "兆易创新", "603986.SH", "高分报告",
         {"基本面": 8, "预期差": 9, "资金面": 7, "技术面": 7}),
        ("rpt_002", "宁德时代", "300750.SZ", "中分报告",
         {"基本面": 6, "预期差": 6, "资金面": 5, "技术面": 5}),
        ("rpt_003", "贵州茅台", "600519.SH", "低分报告",
         {"基本面": 3, "预期差": 3, "资金面": 4, "技术面": 3}),
        ("rpt_004", "比亚迪", "002594.SZ", "看多报告",
         {"基本面": 7, "预期差": 8, "资金面": 6, "技术面": 7}),
        ("rpt_005", "中芯国际", "688981.SH", "极高分报告",
         {"基本面": 9, "预期差": 9, "资金面": 8, "技术面": 8}),
    ]

    for rpt_id, name, code, summary, scores in reports:
        md_path = md_dir / f"{rpt_id}.md"
        score_lines = "\n".join(f"{dim}: {val}/10" for dim, val in scores.items())
        md_content = f"""# {name} 分析报告

{summary}

<<<SCORES>>>
{score_lines}
<<<END_SCORES>>>

收盘价：15.32
"""
        md_path.write_text(md_content, encoding="utf-8")

        conn.execute(
            "INSERT INTO reports VALUES (?,?,?,?,?,?)",
            (rpt_id, name, code, summary, str(md_path), report_date),
        )

    conn.commit()
    conn.close()
    return db_path, md_dir


# ══════════════════════════════════════════════════════════════════
# 案例卡片工厂
# ══════════════════════════════════════════════════════════════════

def make_case_cards(count=100, with_lessons=True):
    from knowledge.case_memory import CaseCard
    rng = random.Random(42)
    regimes = ["bull", "bear", "shock", "rotation"]
    sectors = ["AI算力", "半导体", "光伏", "新能源车", "白酒", "医药", "券商"]
    directions = ["bullish", "bearish", "neutral"]
    outcomes = ["win", "loss", "draw"]
    cases = []
    for i in range(count):
        regime = regimes[i % 4]
        sector_tags = [sectors[i % 7], sectors[(i + 3) % 7]]
        direction = directions[i % 3]
        outcome_type = outcomes[i % 3]
        return_10d = rng.uniform(-15, 20)
        cases.append(CaseCard(
            case_id=f"case_{i:04d}",
            report_date=(datetime.now() - timedelta(days=rng.randint(1, 180))).strftime("%Y-%m-%d"),
            stock_code=f"{600000 + i}.SH",
            stock_name=f"测试股票_{i}",
            regime=regime,
            regime_label={"bull": "牛市", "bear": "熊市", "shock": "震荡市", "rotation": "轮动市"}[regime],
            sector_tags=sector_tags,
            score_fundamental=rng.uniform(3, 9),
            score_expectation=rng.uniform(3, 9),
            score_capital=rng.uniform(3, 9),
            score_technical=rng.uniform(3, 9),
            score_weighted=rng.uniform(3, 9),
            direction=direction,
            return_5d=rng.uniform(-10, 15),
            return_10d=return_10d,
            return_20d=rng.uniform(-20, 25),
            hit_10d=(return_10d > 0) if direction == "bullish" else (return_10d < 0) if direction == "bearish" else None,
            outcome_type=outcome_type,
            lesson=f"教训_{i}: 在{regime}环境下{sector_tags[0]}板块需注意风控" if (with_lessons and i % 5 != 0) else "",
        ))
    return cases


@pytest.fixture()
def preloaded_cases(func_db_manager):
    mgr, tmp_path = func_db_manager
    cases = make_case_cards(120)
    from knowledge.case_memory import store_case
    with patch("knowledge.kb_db.get_manager", return_value=mgr):
        for case in cases:
            if case.case_id:
                store_case(case)
    return mgr, cases


# ══════════════════════════════════════════════════════════════════
# AI 响应 Fixtures
# ══════════════════════════════════════════════════════════════════

@pytest.fixture()
def mock_ai_curation():
    return "【历史知识库参考】\n▸ 该股历史：近3次看多，10日胜率66%\n▸ 校准警示：技术面偏乐观\n⚠️ 样本量有限时仅供辅助判断"


@pytest.fixture()
def mock_ai_reflection_single():
    return "我在技术面评分上高估了突破信号的可靠性。基本面支撑不足导致假突破。下次应下调1-2分。"


@pytest.fixture()
def mock_ai_reflection_batch():
    return json.dumps([
        {"id": "rpt_001", "lesson": "我在预期差维度高估了催化剂持续性。下次应更关注催化剂的持续性。"},
        {"id": "rpt_002", "lesson": "我在资金面判断上偏乐观。北向资金流入只是短期效应。下次需区分脉冲和趋势。"},
    ])


# ══════════════════════════════════════════════════════════════════
# Outcome Tracker 缓存重置
# ══════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=False)
def reset_outcome_tracker():
    import knowledge.outcome_tracker as ot
    ot._evaluated_ids = set()
    ot._evaluated_loaded = False
    ot._benchmark_cache = {}
    ot._war_room_tracker = None
    yield
    ot._evaluated_ids = set()
    ot._evaluated_loaded = False
    ot._benchmark_cache = {}
    ot._war_room_tracker = None
