# Unified Learning Engine Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the unified learning engine skeleton with `general` mode — a five-round backtest-learn cycle that uses full war_room analysis on historical data, then lets Opus reflect and optimize the scoring system.

**Architecture:** Four new modules under `knowledge/` (engine, backtester, reflector, optimizer) + `time_lock` parameter threaded through war_room → report_data → injector. CLI entry point `python cli.py learn general N`.

**Tech Stack:** Python 3.12, SQLite (existing), Claude Opus/Sonnet via CLI (existing `ai/cli_providers.py`), Tushare/AkShare/Sina (existing data layer)

**Spec:** `docs/superpowers/specs/2026-04-12-unified-learning-engine-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `knowledge/learning_config.py` | Constants, safety bounds, staging dir management |
| Create | `knowledge/learning_backtester.py` | Round 1: stock selection, war_room dispatch, grading |
| Create | `knowledge/learning_reflector.py` | Round 2-3: Opus reflection + cross-review |
| Create | `knowledge/learning_optimizer.py` | Round 4-5: config generation, validation, adopt/rollback |
| Create | `knowledge/learning_engine.py` | Main orchestrator: five-round cycle + progress reporting |
| Create | `tests/test_learning_config.py` | Tests for safety bounds validation |
| Create | `tests/test_learning_backtester.py` | Tests for stock selection, grading, holdout split |
| Create | `tests/test_learning_optimizer.py` | Tests for proposal application + safety bounds enforcement |
| Modify | `services/war_room.py:361-380` | Add `time_lock`, `skip_report_save` params to `run_war_room` |
| Modify | `services/war_room.py:383-462` | Thread `time_lock` through `_run_war_room_v2` |
| Modify | `data/report_data.py:734-743` | Add `time_lock` param to `build_report_context` |
| Modify | `knowledge/injector.py:22-30` | Add `time_lock` param to `build_knowledge_context` |
| Modify | `cli.py:1782-1862` | Register `learn` command family |

---

## Task 1: learning_config.py — Constants & Safety Bounds

**Files:**
- Create: `knowledge/learning_config.py`
- Create: `tests/test_learning_config.py`

- [ ] **Step 1: Write the failing tests for safety bounds validation**

```python
# tests/test_learning_config.py
import pytest
from knowledge.learning_config import (
    validate_weights, validate_rule_thresholds, validate_tree_structure,
    validate_prompt_patch, SAFETY_BOUNDS, STAGING_DIR, LEARNING_LOG_DIR,
)


def test_validate_weights_valid():
    w = {"预期差": 0.35, "技术面": 0.35, "基本面": 0.20, "资金面": 0.10}
    assert validate_weights(w) == []


def test_validate_weights_exceeds_max():
    w = {"预期差": 0.55, "技术面": 0.25, "基本面": 0.15, "资金面": 0.05}
    errors = validate_weights(w)
    assert any("50%" in e for e in errors)


def test_validate_weights_below_min():
    w = {"预期差": 0.50, "技术面": 0.45, "基本面": 0.04, "资金面": 0.01}
    errors = validate_weights(w)
    assert any("5%" in e for e in errors)


def test_validate_weights_sum_not_100():
    w = {"预期差": 0.30, "技术面": 0.30, "基本面": 0.20, "资金面": 0.10}
    errors = validate_weights(w)
    assert any("100%" in e or "总和" in e for e in errors)


def test_validate_rule_thresholds_valid():
    rules = {"fundamental_breaker": 25, "bucket_cap": 30, "premortem_cap": 70}
    assert validate_rule_thresholds(rules) == []


def test_validate_rule_thresholds_out_of_range():
    rules = {"fundamental_breaker": 5, "bucket_cap": 50, "premortem_cap": 85}
    errors = validate_rule_thresholds(rules)
    assert len(errors) == 3


def test_validate_tree_structure_too_many_questions():
    tree = {"预期差": {"questions": [f"Q{i}" for i in range(10)]}}
    errors = validate_tree_structure(tree)
    assert any("8" in e for e in errors)


def test_validate_prompt_patch_too_large():
    original = "x" * 1000
    patch = "y" * 300  # 30% > 20% limit
    errors = validate_prompt_patch(patch, original)
    assert any("20%" in e for e in errors)


def test_staging_dir_constant():
    assert "staging" in str(STAGING_DIR)


def test_learning_log_dir_constant():
    assert "learning_log" in str(LEARNING_LOG_DIR)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/LinDangAgent && python -m pytest tests/test_learning_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'knowledge.learning_config'`

- [ ] **Step 3: Implement learning_config.py**

```python
# knowledge/learning_config.py
# -*- coding: utf-8 -*-
"""统一学习引擎 — 配置、常量、安全边界、staging 管理。"""

import json
import shutil
from pathlib import Path

from knowledge.kb_config import BASE_DIR, KNOWLEDGE_DIR

# ── 路径 ──────────────────────────────────────────────────────────
STAGING_DIR = KNOWLEDGE_DIR / "staging"
LEARNING_LOG_DIR = KNOWLEDGE_DIR / "learning_log"
DECISION_TREE_PATH = KNOWLEDGE_DIR / "decision_tree.json"
STAGING_TREE_PATH = STAGING_DIR / "decision_tree.json"
STAGING_RULES_PATH = STAGING_DIR / "correction_rules.json"
STAGING_PROMPT_PATH = STAGING_DIR / "prompt_patches.json"
STAGING_CHANGELOG = STAGING_DIR / "changelog.md"

# ── 选题参数 ──────────────────────────────────────────────────────
FAMILIAR_RATIO = 0.70          # 70% 已知领域
EXPLORE_RATIO = 0.30           # 30% 探索领域
MIN_TURNOVER_20D = 5000_0000   # 日均成交额 >= 5000 万
MIN_VOLATILITY_20D = 2.0       # 振幅均值 >= 2%
MIN_ABS_CHANGE_20D = 10.0      # 或区间涨跌幅绝对值 >= 10%
EXAM_DATE_RANGE = (15, 90)     # 考试日期范围: T-90 到 T-15
EXAM_DATE_SLOTS = 5            # 分散到 5 个日期
HOLDOUT_RATIO = 0.30           # 30% 验证集

# ── 安全边界 ──────────────────────────────────────────────────────
SAFETY_BOUNDS = {
    "weight_max": 0.50,
    "weight_min": 0.05,
    "fundamental_breaker_range": (15, 35),
    "bucket_cap_range": (20, 40),
    "premortem_cap_range": (60, 80),
    "tree_questions_min": 3,
    "tree_questions_max": 8,
    "prompt_patch_max_ratio": 0.20,
}

DIMENSIONS = ["预期差", "技术面", "基本面", "资金面"]

# ── 采纳门槛 ──────────────────────────────────────────────────────
ADOPTION_HIT_RATE_IMPROVEMENT = 3.0   # 胜率至少提升 3%
ADOPTION_NO_CLIFF_DROP = 15.0         # 任一类别胜率不得断崖下跌 >15%


# ── 验证函数 ──────────────────────────────────────────────────────

def validate_weights(weights: dict) -> list[str]:
    """验证权重是否在安全边界内。返回错误列表，空=通过。"""
    errors = []
    for dim in DIMENSIONS:
        w = weights.get(dim, 0)
        if w > SAFETY_BOUNDS["weight_max"]:
            errors.append(f"{dim} 权重 {w:.0%} 超过上限 50%")
        if w < SAFETY_BOUNDS["weight_min"]:
            errors.append(f"{dim} 权重 {w:.0%} 低于下限 5%")
    total = sum(weights.get(d, 0) for d in DIMENSIONS)
    if abs(total - 1.0) > 0.01:
        errors.append(f"权重总和 {total:.0%} != 100%")
    return errors


def validate_rule_thresholds(rules: dict) -> list[str]:
    """验证修正规则阈值。"""
    errors = []
    checks = [
        ("fundamental_breaker", "fundamental_breaker_range", "熔断线"),
        ("bucket_cap", "bucket_cap_range", "木桶线"),
        ("premortem_cap", "premortem_cap_range", "预mortem封顶"),
    ]
    for key, bound_key, label in checks:
        val = rules.get(key)
        if val is None:
            continue
        lo, hi = SAFETY_BOUNDS[bound_key]
        if not (lo <= val <= hi):
            errors.append(f"{label} {val} 不在 [{lo}, {hi}] 范围内")
    return errors


def validate_tree_structure(tree: dict) -> list[str]:
    """验证决策树结构。"""
    errors = []
    mn, mx = SAFETY_BOUNDS["tree_questions_min"], SAFETY_BOUNDS["tree_questions_max"]
    for dim in DIMENSIONS:
        if dim not in tree:
            errors.append(f"决策树缺少维度: {dim}")
            continue
        qs = tree[dim].get("questions", [])
        if len(qs) > mx:
            errors.append(f"{dim} 问题数 {len(qs)} 超过上限 {mx}")
        if len(qs) < mn:
            errors.append(f"{dim} 问题数 {len(qs)} 低于下限 {mn}")
    return errors


def validate_prompt_patch(patch_content: str, original_prompt: str) -> list[str]:
    """验证 prompt 修改幅度不超过原 prompt 的 20%。"""
    errors = []
    ratio = SAFETY_BOUNDS["prompt_patch_max_ratio"]
    if len(patch_content) > len(original_prompt) * ratio:
        errors.append(f"Prompt 修改 {len(patch_content)} 字符超过原文 {len(original_prompt)} 的 {ratio:.0%} 上限")
    return errors


# ── Staging 管理 ──────────────────────────────────────────────────

def ensure_staging():
    """确保 staging 目录存在。"""
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    LEARNING_LOG_DIR.mkdir(parents=True, exist_ok=True)


def clear_staging():
    """清除 staging 区。"""
    if STAGING_DIR.exists():
        shutil.rmtree(STAGING_DIR)
    STAGING_DIR.mkdir(parents=True, exist_ok=True)


def load_production_tree() -> dict:
    """加载生产环境的 decision_tree.json。"""
    return json.loads(DECISION_TREE_PATH.read_text(encoding="utf-8"))


def save_staging_tree(tree: dict):
    """保存候选决策树到 staging 区。"""
    ensure_staging()
    STAGING_TREE_PATH.write_text(
        json.dumps(tree, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def promote_staging():
    """将 staging 配置提升为生产配置。"""
    if STAGING_TREE_PATH.exists():
        shutil.copy2(STAGING_TREE_PATH, DECISION_TREE_PATH)
    if STAGING_RULES_PATH.exists():
        # 修正规则合并到 decision_tree.json
        rules = json.loads(STAGING_RULES_PATH.read_text(encoding="utf-8"))
        tree = load_production_tree()
        tree["correction_rules"] = rules
        DECISION_TREE_PATH.write_text(
            json.dumps(tree, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def save_learning_log(log_data: dict, mode: str, count: int):
    """保存学习日志。"""
    ensure_staging()
    from datetime import date
    filename = f"{date.today().isoformat()}_{mode}_{count}.json"
    path = LEARNING_LOG_DIR / filename
    path.write_text(json.dumps(log_data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/LinDangAgent && python -m pytest tests/test_learning_config.py -v`
Expected: All 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add knowledge/learning_config.py tests/test_learning_config.py
git commit -m "feat(learn): add learning_config with safety bounds and staging management"
```

---

## Task 2: Thread `time_lock` through war_room → report_data → injector

**Files:**
- Modify: `services/war_room.py:361-462`
- Modify: `data/report_data.py:734-743`
- Modify: `knowledge/injector.py:22-30`

- [ ] **Step 1: Modify `run_war_room` signature to accept `time_lock` and `skip_report_save`**

In `services/war_room.py`, find `run_war_room` function (line ~361):

```python
# OLD:
def run_war_room(
    stock_name: str,
    username: str = "cli",
    preset: str = DEFAULT_PRESET,
    skip_extra_recon: bool = False,
) -> WarRoomResult:

# NEW:
def run_war_room(
    stock_name: str,
    username: str = "cli",
    preset: str = DEFAULT_PRESET,
    skip_extra_recon: bool = False,
    time_lock: str = "",
    skip_report_save: bool = False,
) -> WarRoomResult:
```

Then thread `time_lock` and `skip_report_save` into the preset_cfg dict that gets passed to `_run_war_room_v2`:

```python
# After preset_cfg is built, add:
preset_cfg["time_lock"] = time_lock
preset_cfg["skip_report_save"] = skip_report_save
```

- [ ] **Step 2: Modify `_run_war_room_v2` to use `time_lock`**

In `_run_war_room_v2` (line ~383), extract and use the flag:

```python
time_lock = preset_cfg.get("time_lock", "")
skip_report_save = preset_cfg.get("skip_report_save", False)
```

Change the `build_report_context` call (line ~421):

```python
# OLD:
context, raw_data = build_report_context(ts_code, resolved_name)

# NEW:
context, raw_data = build_report_context(ts_code, resolved_name, time_lock=time_lock)
```

Change the `build_knowledge_context` call (line ~426):

```python
# Add time_lock parameter
knowledge_ctx = build_knowledge_context(
    stock_code=ts_code,
    stock_name=resolved_name,
    model_name=analyst_model,
    price_snapshot=price_snap,
    indicators=indicators,
    time_lock=time_lock,  # NEW
)
```

When `time_lock` is set, skip sentiment and news context (historical news can't be reliably reproduced):

```python
if not time_lock:
    # existing sentiment/news/macro fetch code
    sentiment_ctx = ...
    macro_ctx = ...
else:
    sentiment_ctx = ""
    macro_ctx = ""
```

When `skip_report_save` is True, skip the `save_report()` call and email dispatch at the end of the function.

- [ ] **Step 3: Modify `build_report_context` in `data/report_data.py`**

Find `build_report_context` (line ~734) and add `time_lock` parameter:

```python
# OLD:
def build_report_context(ts_code: str, name: str, progress_cb=None) -> tuple[dict, dict]:

# NEW:
def build_report_context(ts_code: str, name: str, progress_cb=None, time_lock: str = "") -> tuple[dict, dict]:
```

Inside the function, when `time_lock` is set, filter all DataFrames to only include data up to and including `time_lock` date. The key pattern for each data-fetching call:

```python
# For K-line / price data, after fetching:
if time_lock and df is not None and "trade_date" in df.columns:
    df = df[df["trade_date"] <= time_lock]
```

For financial data (income, balancesheet, cashflow), filter by `end_date` or `ann_date`:

```python
if time_lock and df is not None:
    date_col = "ann_date" if "ann_date" in df.columns else "end_date"
    if date_col in df.columns:
        df = df[df[date_col] <= time_lock]
```

- [ ] **Step 4: Modify `build_knowledge_context` in `knowledge/injector.py`**

Find `build_knowledge_context` (line ~22) and add `time_lock` parameter:

```python
# OLD:
def build_knowledge_context(
    stock_code: str = "",
    stock_name: str = "",
    scores: dict | None = None,
    model_name: str = "",
    max_chars: int = 4000,
    price_snapshot: str = "",
    indicators: dict | None = None,
) -> str:

# NEW:
def build_knowledge_context(
    stock_code: str = "",
    stock_name: str = "",
    scores: dict | None = None,
    model_name: str = "",
    max_chars: int = 4000,
    price_snapshot: str = "",
    indicators: dict | None = None,
    time_lock: str = "",
) -> str:
```

Inside `_collect_knowledge_candidates`, when `time_lock` is set, pass a `cutoff_date` to `retrieve_similar_cases` so it only returns cases from before the exam date:

```python
# In the "相似案例" section:
if time_lock:
    cutoff = f"{time_lock[:4]}-{time_lock[4:6]}-{time_lock[6:]}"
    cases = retrieve_similar_cases(..., cutoff_date=cutoff)
```

Also skip "近期情报" and "宏观简报" candidates when `time_lock` is set (they can't be reliably time-locked).

- [ ] **Step 5: Smoke test the time_lock threading**

Run: `cd /c/LinDangAgent && python -c "from services.war_room import run_war_room; print('import OK')"` 
Expected: `import OK` (no import errors)

- [ ] **Step 6: Commit**

```bash
git add services/war_room.py data/report_data.py knowledge/injector.py
git commit -m "feat(learn): thread time_lock through war_room → report_data → injector"
```

---

## Task 3: learning_backtester.py — Round 1 Stock Selection & Grading

**Files:**
- Create: `knowledge/learning_backtester.py`
- Create: `tests/test_learning_backtester.py`

- [ ] **Step 1: Write failing tests for grading and holdout split**

```python
# tests/test_learning_backtester.py
import pytest
from knowledge.learning_backtester import (
    grade_result, split_holdout, categorize_return,
)


def test_grade_bullish_positive_alpha():
    assert grade_result("bullish", excess_return=5.0) == "hit"


def test_grade_bullish_negative_alpha():
    assert grade_result("bullish", excess_return=-3.0) == "miss"


def test_grade_bearish_negative_alpha():
    assert grade_result("bearish", excess_return=-4.0) == "hit"


def test_grade_bearish_positive_alpha():
    assert grade_result("bearish", excess_return=2.0) == "miss"


def test_grade_neutral_within_threshold():
    assert grade_result("neutral", excess_return=1.5) == "hit"


def test_grade_neutral_outside_threshold():
    assert grade_result("neutral", excess_return=5.0) == "miss"


def test_split_holdout_ratio():
    items = list(range(100))
    train, holdout = split_holdout(items, ratio=0.30)
    assert len(holdout) == 30
    assert len(train) == 70
    assert set(train + holdout) == set(items)


def test_split_holdout_small_list():
    items = list(range(3))
    train, holdout = split_holdout(items, ratio=0.30)
    assert len(holdout) >= 1  # at least 1 for validation
    assert len(train) + len(holdout) == 3


def test_categorize_return():
    assert categorize_return(15.0) == "big_rise"
    assert categorize_return(5.0) == "rise"
    assert categorize_return(0.5) == "flat"
    assert categorize_return(-5.0) == "fall"
    assert categorize_return(-12.0) == "big_fall"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/LinDangAgent && python -m pytest tests/test_learning_backtester.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement learning_backtester.py**

```python
# knowledge/learning_backtester.py
# -*- coding: utf-8 -*-
"""统一学习引擎 — Round 1: 批量回测执行器。

选题(Z模式) → 时间锁定 war_room 分析 → 三级归因判卷 → holdout 分割。
"""

import json
import logging
import random
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from knowledge.kb_config import BASE_DIR, KNOWLEDGE_DIR, DIRECTION_CN
from knowledge.learning_config import (
    FAMILIAR_RATIO, EXPLORE_RATIO, MIN_TURNOVER_20D,
    MIN_VOLATILITY_20D, MIN_ABS_CHANGE_20D,
    EXAM_DATE_RANGE, EXAM_DATE_SLOTS, HOLDOUT_RATIO,
)

logger = logging.getLogger(__name__)


# ── 判卷 ──────────────────────────────────────────────────────────

def grade_result(direction: str, excess_return: float) -> str:
    """三级归因判卷。返回 'hit' 或 'miss'。"""
    if direction == "bullish" and excess_return > 0:
        return "hit"
    if direction == "bearish" and excess_return < 0:
        return "hit"
    if direction == "neutral" and abs(excess_return) < 3.0:
        return "hit"
    return "miss"


def categorize_return(ret: float) -> str:
    """分类实际收益率。"""
    if ret > 10:
        return "big_rise"
    if ret > 3:
        return "rise"
    if ret > -3:
        return "flat"
    if ret > -8:
        return "fall"
    return "big_fall"


def split_holdout(items: list, ratio: float = HOLDOUT_RATIO) -> tuple[list, list]:
    """随机分割训练集和验证集。返回 (train, holdout)。"""
    shuffled = list(items)
    random.shuffle(shuffled)
    n_holdout = max(1, int(len(shuffled) * ratio))
    return shuffled[n_holdout:], shuffled[:n_holdout]


# ── 选题 ──────────────────────────────────────────────────────────

def _generate_exam_dates(count: int) -> list[str]:
    """生成分散的考试日期列表。"""
    lo, hi = EXAM_DATE_RANGE
    slots = min(EXAM_DATE_SLOTS, count)
    dates = []
    for _ in range(slots):
        offset = random.randint(lo, hi)
        d = (datetime.now() - timedelta(days=offset)).strftime("%Y%m%d")
        dates.append(d)
    return dates


def _fetch_familiar_pool() -> list[dict]:
    """从 reports.db + case_memory 获取已知领域股票。"""
    pool = []
    try:
        from repositories.report_repo import list_reports
        reports = list_reports(limit=200)
        seen = set()
        for r in reports:
            code = r.get("stock_code", "")
            if code and code not in seen:
                seen.add(code)
                pool.append({
                    "ts_code": code,
                    "stock_name": r.get("stock_name", code),
                    "source": "reports",
                })
    except Exception as exc:
        logger.warning("[learn] failed to load reports pool: %s", exc)

    # 弱项板块加权
    try:
        from knowledge.simulation_training import get_simulation_stats
        stats = get_simulation_stats()
        weak = {w["sector"] for w in stats.get("weak_sectors", [])}
        for item in pool:
            item["_weak_boost"] = 2.0 if any(w in item["stock_name"] for w in weak) else 1.0
    except Exception:
        pass

    return pool


def _fetch_explore_pool(exclude_codes: set[str]) -> list[dict]:
    """从全市场随机抽样获取探索领域股票。"""
    pool = []
    try:
        from data.tushare_client import load_stock_list
        stock_list, _ = load_stock_list()
        if stock_list is None or stock_list.empty:
            return pool

        # 排除 ST 和已有的
        df = stock_list[~stock_list["name"].str.contains("ST|退", na=False)]
        df = df[~df["ts_code"].isin(exclude_codes)]

        # 按市值分层: 大盘/中盘/小盘 = 3:4:3
        n = len(df)
        if n < 30:
            sample = df
        else:
            sorted_df = df.sort_values("ts_code")  # proxy sort
            large = sorted_df.head(n // 3).sample(min(10, n // 3))
            mid = sorted_df.iloc[n // 3: 2 * n // 3].sample(min(13, n // 3))
            small = sorted_df.tail(n // 3).sample(min(10, n // 3))
            sample = pd.concat([large, mid, small])

        for _, row in sample.iterrows():
            pool.append({
                "ts_code": row["ts_code"],
                "stock_name": row.get("name", row["ts_code"]),
                "source": "explore",
                "_weak_boost": 1.0,
            })
    except Exception as exc:
        logger.warning("[learn] failed to load explore pool: %s", exc)

    return pool


def _apply_filters(stock: dict, exam_date: str) -> bool:
    """应用硬过滤门槛: 流动性、波动性、非ST、非停牌。"""
    from knowledge.simulation_training import _fetch_historical_kline

    ts_code = stock["ts_code"]
    try:
        df = _fetch_historical_kline(ts_code, exam_date, days=30)
        if df is None or len(df) < 10:
            return False

        # 日均成交额(简化: vol * close 近似)
        if "vol" in df.columns and "close" in df.columns:
            avg_turnover = (df["vol"] * df["close"]).mean()
            if avg_turnover < MIN_TURNOVER_20D / 100:  # vol 单位是手
                return False

        # 波动性
        if "pct_chg" in df.columns:
            avg_volatility = df["pct_chg"].abs().mean()
            total_change = abs(df["pct_chg"].sum())
            if avg_volatility < MIN_VOLATILITY_20D and total_change < MIN_ABS_CHANGE_20D:
                return False

        return True
    except Exception:
        return False


def select_exam_stocks(count: int) -> list[dict]:
    """Z 模式选题: 70% 已知 + 30% 探索。

    返回: [{ts_code, stock_name, exam_date, source}, ...]
    """
    n_familiar = int(count * FAMILIAR_RATIO)
    n_explore = count - n_familiar

    familiar = _fetch_familiar_pool()
    random.shuffle(familiar)

    # 弱项加权排序
    familiar.sort(key=lambda x: x.get("_weak_boost", 1.0), reverse=True)
    familiar = familiar[:n_familiar * 3]  # 取 3 倍候选

    familiar_codes = {s["ts_code"] for s in familiar}
    explore = _fetch_explore_pool(familiar_codes)
    random.shuffle(explore)
    explore = explore[:n_explore * 3]

    # 生成考试日期
    exam_dates = _generate_exam_dates(count)

    # 组合选题 + 过滤
    candidates = familiar + explore
    selected = []
    date_idx = 0

    for stock in candidates:
        if len(selected) >= count:
            break
        exam_date = exam_dates[date_idx % len(exam_dates)]
        if _apply_filters(stock, exam_date):
            stock["exam_date"] = exam_date
            selected.append(stock)
            date_idx += 1

    logger.info("[learn] selected %d exam stocks (%d familiar, %d explore)",
                len(selected),
                sum(1 for s in selected if s.get("source") == "reports"),
                sum(1 for s in selected if s.get("source") == "explore"))
    return selected


# ── 单只回测 ──────────────────────────────────────────────────────

def run_single_backtest(exam: dict, progress_cb=None) -> dict | None:
    """对单只股票执行完整 war_room 回测。

    返回含评分、方向、判卷结果的 dict，失败返回 None。
    """
    from services.war_room import run_war_room
    from knowledge.simulation_training import (
        _fetch_historical_kline, _get_market_return, _get_sector_return,
        _calc_return_from_kline,
    )

    ts_code = exam["ts_code"]
    stock_name = exam["stock_name"]
    exam_date = exam["exam_date"]

    if progress_cb:
        progress_cb(f"回测 {stock_name} ({exam_date})...")

    # 获取实际 T+10 收益
    try:
        df = _fetch_historical_kline(ts_code, datetime.now().strftime("%Y%m%d"), days=120)
        actual_return = _calc_return_from_kline(df, exam_date)
        if actual_return is None:
            logger.warning("[learn] no future data for %s %s", stock_name, exam_date)
            return None
    except Exception as exc:
        logger.warning("[learn] return calc failed for %s: %s", stock_name, exc)
        return None

    # 完整 war_room 分析（时间锁定）
    try:
        result = run_war_room(
            stock_name=stock_name,
            preset="opus",
            time_lock=exam_date,
            skip_report_save=True,
        )
    except Exception as exc:
        logger.warning("[learn] war_room failed for %s: %s", stock_name, exc)
        return None

    if not result or not result.final_scores:
        return None

    scores = result.final_scores
    weighted = scores.get("综合加权", 50)
    direction = "bullish" if weighted >= 55 else ("bearish" if weighted <= 45 else "neutral")

    # 三级归因
    market_return = _get_market_return(exam_date)
    sector_return, sector_name = _get_sector_return(ts_code, exam_date)
    benchmark = max(market_return, sector_return) if sector_return != 0 else market_return
    excess_return = actual_return - benchmark
    stock_alpha = actual_return - (sector_return if sector_return != 0 else market_return)

    verdict = grade_result(direction, excess_return)

    return {
        "ts_code": ts_code,
        "stock_name": stock_name,
        "exam_date": exam_date,
        "source": exam.get("source", ""),
        "category": categorize_return(actual_return),
        "scores": scores,
        "weighted": weighted,
        "direction": direction,
        "direction_cn": DIRECTION_CN.get(direction, "中性"),
        "actual_return_10d": round(actual_return, 2),
        "market_return_10d": round(market_return, 2),
        "sector_name": sector_name,
        "sector_return_10d": round(sector_return, 2),
        "stock_alpha": round(stock_alpha, 2),
        "excess_return": round(excess_return, 2),
        "verdict": verdict,
        "analysis_summary": result.final_summary[:300] if result.final_summary else "",
        "combined_markdown": result.combined_markdown[:2000] if result.combined_markdown else "",
    }


# ── 批量回测 ──────────────────────────────────────────────────────

def run_backtest_round(
    count: int = 50,
    delay_between: int = 30,
    progress_cb=None,
) -> dict:
    """Round 1: 批量回测 + holdout 分割。

    返回: {
        status, train_results, holdout_exams,
        stats: {total, hits, hit_rate, by_direction, by_sector, by_category}
    }
    """
    exams = select_exam_stocks(count)
    if not exams:
        return {"status": "no_exams", "message": "选题失败，请检查数据源"}

    train_exams, holdout_exams = split_holdout(exams, HOLDOUT_RATIO)

    if progress_cb:
        progress_cb(f"Round 1: {len(train_exams)} 只训练 + {len(holdout_exams)} 只验证")

    results = []
    for i, exam in enumerate(train_exams):
        if progress_cb:
            progress_cb(f"[{i+1}/{len(train_exams)}] {exam['stock_name']}")

        result = run_single_backtest(exam, progress_cb)
        if result:
            results.append(result)

        if i < len(train_exams) - 1 and delay_between > 0:
            time.sleep(delay_between)

    # 统计
    total = len(results)
    hits = sum(1 for r in results if r["verdict"] == "hit")
    hit_rate = hits / total * 100 if total > 0 else 0

    by_direction = {}
    for r in results:
        d = r["direction_cn"]
        by_direction.setdefault(d, {"total": 0, "hits": 0})
        by_direction[d]["total"] += 1
        if r["verdict"] == "hit":
            by_direction[d]["hits"] += 1
    for v in by_direction.values():
        v["hit_rate"] = round(v["hits"] / v["total"] * 100, 1) if v["total"] > 0 else 0

    by_sector = {}
    for r in results:
        s = r.get("sector_name", "未知") or "未知"
        by_sector.setdefault(s, {"total": 0, "hits": 0})
        by_sector[s]["total"] += 1
        if r["verdict"] == "hit":
            by_sector[s]["hits"] += 1
    for v in by_sector.values():
        v["hit_rate"] = round(v["hits"] / v["total"] * 100, 1) if v["total"] > 0 else 0

    by_category = {}
    for r in results:
        c = r["category"]
        by_category.setdefault(c, {"total": 0, "hits": 0})
        by_category[c]["total"] += 1
        if r["verdict"] == "hit":
            by_category[c]["hits"] += 1
    for v in by_category.values():
        v["hit_rate"] = round(v["hits"] / v["total"] * 100, 1) if v["total"] > 0 else 0

    return {
        "status": "ok",
        "train_results": results,
        "holdout_exams": holdout_exams,
        "stats": {
            "total": total,
            "hits": hits,
            "hit_rate": round(hit_rate, 1),
            "by_direction": by_direction,
            "by_sector": by_sector,
            "by_category": by_category,
        },
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/LinDangAgent && python -m pytest tests/test_learning_backtester.py -v`
Expected: All 10 tests PASS

- [ ] **Step 5: Commit**

```bash
git add knowledge/learning_backtester.py tests/test_learning_backtester.py
git commit -m "feat(learn): add learning_backtester with stock selection, grading, and backtest orchestration"
```

---

## Task 4: learning_reflector.py — Round 2-3 Opus Reflection & Cross-Review

**Files:**
- Create: `knowledge/learning_reflector.py`

- [ ] **Step 1: Implement learning_reflector.py**

```python
# knowledge/learning_reflector.py
# -*- coding: utf-8 -*-
"""统一学习引擎 — Round 2-3: Opus 反思 + 交叉审视。

Round 2: 统一反思 — Opus 分析回测结果，输出结构化调整建议
Round 3: 交叉审视 — 质疑→答辩→仲裁，多轮 Opus 审视
"""

import json
import logging
import re
from pathlib import Path

from knowledge.kb_config import KNOWLEDGE_DIR
from knowledge.learning_config import load_production_tree, DIMENSIONS

logger = logging.getLogger(__name__)


# ── Prompt 模板 ──────────────────────────────────────────────────

REFLECT_SYSTEM = """你是投研系统的首席策略官。以下是最近一批回测结果和当前系统配置。

请从结果中找出系统性问题，并提出改进建议。你可以调整以下任何一项或多项：
1. 四维权重分配 (type=weight)
2. 五条修正规则的阈值或逻辑 (type=rule)
3. 决策树的分支结构——增/删/改问题节点 (type=tree)
4. Round 1 或 Round 2 的 system prompt 措辞 (type=prompt)

要求：
- 每条建议必须有数据支撑（引用具体案例或统计数字）
- 说明预期效果（"这个调整预计能避免 X 类失误"）
- 标注风险（"但可能导致 Y 类场景误判"）
- 如果你认为当前配置已经足够好，可以不改——不要为了改而改
- confidence 用 high/medium/low 标注

输出格式（必须严格遵守）：
<<<PROPOSALS>>>
[
  {
    "id": "P1",
    "type": "weight|rule|tree|prompt",
    "target": "具体目标（如 '技术面权重' 或 'bucket_effect 阈值'）",
    "current_value": "当前值",
    "proposed_value": "建议值",
    "evidence": "支撑数据（引用案例编号或统计）",
    "expected_effect": "预期效果",
    "risk": "潜在风险",
    "confidence": "high|medium|low"
  }
]
<<<END_PROPOSALS>>>

如果没有需要调整的，输出空数组：
<<<PROPOSALS>>>
[]
<<<END_PROPOSALS>>>"""

CHALLENGE_SYSTEM = """你是投研系统的风控官。你的职责是审查以下调整建议，专门找过拟合风险和样本偏差。

对每条 proposal，给出评判：
- pass: 建议合理，可以采纳
- concern: 有疑虑，需要提议者补充证据
- reject: 建议有明显问题，不应采纳

评判标准：
1. 样本量是否足够支撑结论？（<10个案例的统计不可信）
2. 是否存在过拟合风险？（针对个别案例的调整可能损害整体）
3. 调整幅度是否合理？（大幅调整需要更强的证据）
4. 是否考虑了副作用？（改善A类场景可能恶化B类场景）

输出格式：
<<<VERDICTS>>>
[
  {
    "proposal_id": "P1",
    "verdict": "pass|concern|reject",
    "reason": "判断理由",
    "question": "如果 concern，向提议者提出的具体问题"
  }
]
<<<END_VERDICTS>>>"""

DEFENSE_SYSTEM = """你是投研系统的策略官。风控官对你的调整建议提出了质疑。请针对每条疑虑：
- 如果你有补充证据，给出证据并维持建议
- 如果质疑有理，承认问题并修改建议（或撤回）

输出格式：
<<<DEFENSE>>>
[
  {
    "proposal_id": "P1",
    "action": "maintain|revise|withdraw",
    "response": "回应内容",
    "revised_value": "如果 revise，修改后的值"
  }
]
<<<END_DEFENSE>>>"""

ARBITRATE_SYSTEM = """你是投研系统的最终裁决人。策略官和风控官对以下建议存在分歧。
请综合双方论点，做出最终决定。

输出格式：
<<<FINAL>>>
[
  {
    "proposal_id": "P1",
    "decision": "adopt|reject",
    "reason": "裁决理由"
  }
]
<<<END_FINAL>>>"""


# ── 材料构建 ──────────────────────────────────────────────────────

def _build_reflection_material(train_results: list, stats: dict) -> str:
    """构建给 Opus 的反思材料。"""
    tree = load_production_tree()

    # 排序找典型案例
    sorted_by_alpha = sorted(train_results, key=lambda r: r.get("excess_return", 0))
    failures = sorted_by_alpha[:10]  # 最差的 10 个
    successes = sorted_by_alpha[-5:]  # 最好的 5 个
    boundary = [r for r in train_results if 45 <= r.get("weighted", 50) <= 55][:5]

    def fmt_case(r, idx):
        return (
            f"案例#{idx}: {r['stock_name']}({r['ts_code']}) {r['exam_date']}\n"
            f"  评分: 基本面{r['scores'].get('基本面', '?')} 预期差{r['scores'].get('预期差', '?')} "
            f"资金面{r['scores'].get('资金面', '?')} 技术面{r['scores'].get('技术面', '?')} "
            f"综合{r['weighted']}\n"
            f"  方向: {r['direction_cn']} | 实际α: {r['excess_return']:+.1f}% | "
            f"判定: {'✅' if r['verdict'] == 'hit' else '❌'}\n"
            f"  摘要: {r.get('analysis_summary', '')[:200]}"
        )

    parts = [
        "# 回测反思材料\n",
        f"## 整体统计",
        f"- 总数: {stats['total']} | 命中: {stats['hits']} | 胜率: {stats['hit_rate']}%",
        f"- 分方向: {json.dumps(stats['by_direction'], ensure_ascii=False)}",
        f"- 分板块: {json.dumps(stats['by_sector'], ensure_ascii=False)}",
        f"- 分类别: {json.dumps(stats['by_category'], ensure_ascii=False)}",
        f"\n## 典型失败案例 (Top10 最差α)",
    ]
    for i, r in enumerate(failures):
        parts.append(fmt_case(r, i + 1))

    parts.append("\n## 典型成功案例 (Top5 最佳α)")
    for i, r in enumerate(successes):
        parts.append(fmt_case(r, i + 1))

    if boundary:
        parts.append("\n## 边界案例 (综合评分 45-55)")
        for i, r in enumerate(boundary):
            parts.append(fmt_case(r, i + 1))

    parts.append("\n## 当前配置快照")
    parts.append(f"```json\n{json.dumps(tree, ensure_ascii=False, indent=2)}\n```")

    # 历史学习记录
    from knowledge.learning_config import LEARNING_LOG_DIR
    log_files = sorted(LEARNING_LOG_DIR.glob("*.json"))[-3:] if LEARNING_LOG_DIR.exists() else []
    if log_files:
        parts.append("\n## 历史学习记录 (最近3次)")
        for f in log_files:
            try:
                log = json.loads(f.read_text(encoding="utf-8"))
                parts.append(f"- {f.stem}: {log.get('summary', '无摘要')}")
            except Exception:
                pass

    return "\n".join(parts)


def _parse_proposals(text: str) -> list[dict]:
    """从 Opus 输出中解析 proposals。"""
    m = re.search(r"<<<PROPOSALS>>>(.*?)<<<END_PROPOSALS>>>", text, re.DOTALL)
    if not m:
        logger.warning("[learn] no PROPOSALS block found")
        return []
    try:
        return json.loads(m.group(1).strip())
    except json.JSONDecodeError as exc:
        logger.warning("[learn] failed to parse proposals JSON: %s", exc)
        return []


def _parse_verdicts(text: str) -> list[dict]:
    m = re.search(r"<<<VERDICTS>>>(.*?)<<<END_VERDICTS>>>", text, re.DOTALL)
    if not m:
        return []
    try:
        return json.loads(m.group(1).strip())
    except json.JSONDecodeError:
        return []


def _parse_defense(text: str) -> list[dict]:
    m = re.search(r"<<<DEFENSE>>>(.*?)<<<END_DEFENSE>>>", text, re.DOTALL)
    if not m:
        return []
    try:
        return json.loads(m.group(1).strip())
    except json.JSONDecodeError:
        return []


def _parse_final(text: str) -> list[dict]:
    m = re.search(r"<<<FINAL>>>(.*?)<<<END_FINAL>>>", text, re.DOTALL)
    if not m:
        return []
    try:
        return json.loads(m.group(1).strip())
    except json.JSONDecodeError:
        return []


def _call_opus(prompt: str, system: str) -> str:
    """调用 Claude Opus CLI。"""
    from ai.client import call_ai, get_ai_client

    model_name = "🧠 Claude Opus（MAX）"
    client, cfg, err = get_ai_client(model_name)
    if err and not cfg:
        logger.error("[learn] Opus unavailable: %s", err)
        return ""

    cfg_no_search = {**cfg, "supports_search": False}
    text, call_err = call_ai(client, cfg_no_search, prompt, system=system, max_tokens=4000)
    if call_err:
        logger.error("[learn] Opus call failed: %s", call_err)
        return ""
    return text or ""


# ── Round 2: 统一反思 ────────────────────────────────────────────

def run_reflection(train_results: list, stats: dict,
                   progress_cb=None) -> list[dict]:
    """Round 2: Opus 分析回测结果，输出调整建议。

    返回: proposals 列表
    """
    if progress_cb:
        progress_cb("Round 2: Opus 统一反思...")

    material = _build_reflection_material(train_results, stats)
    text = _call_opus(material, REFLECT_SYSTEM)

    proposals = _parse_proposals(text)
    if progress_cb:
        progress_cb(f"Round 2 完成: {len(proposals)} 条建议")

    return proposals


# ── Round 3: 交叉审视 ────────────────────────────────────────────

def run_cross_review(
    proposals: list[dict],
    train_results: list,
    stats: dict,
    progress_cb=None,
) -> list[dict]:
    """Round 3: 质疑→答辩→仲裁。

    返回: 最终采纳的 proposals 列表（只含 adopt 的）。
    """
    if not proposals:
        if progress_cb:
            progress_cb("Round 3: 无建议需要审视")
        return []

    # Step 1: 质疑者
    if progress_cb:
        progress_cb("Round 3 Step 1: 质疑者审查...")

    challenge_prompt = (
        f"# 待审查的调整建议\n\n"
        f"```json\n{json.dumps(proposals, ensure_ascii=False, indent=2)}\n```\n\n"
        f"# 回测统计\n"
        f"总数: {stats['total']} | 胜率: {stats['hit_rate']}%\n"
        f"分方向: {json.dumps(stats['by_direction'], ensure_ascii=False)}\n"
        f"分板块: {json.dumps(stats['by_sector'], ensure_ascii=False)}"
    )
    challenge_text = _call_opus(challenge_prompt, CHALLENGE_SYSTEM)
    verdicts = _parse_verdicts(challenge_text)

    # 快速路径: 全部通过
    concerns = [v for v in verdicts if v.get("verdict") == "concern"]
    rejects = [v for v in verdicts if v.get("verdict") == "reject"]

    if not concerns and not rejects:
        if progress_cb:
            progress_cb("Round 3: 质疑者全部通过，跳过答辩")
        return proposals

    # 标记被否决的
    rejected_ids = {v["proposal_id"] for v in rejects}
    surviving = [p for p in proposals if p.get("id") not in rejected_ids]

    if not concerns:
        if progress_cb:
            progress_cb(f"Round 3: {len(rejects)} 条否决，{len(surviving)} 条通过")
        return surviving

    # Step 2: 答辩
    if progress_cb:
        progress_cb(f"Round 3 Step 2: 答辩 ({len(concerns)} 条疑虑)...")

    defense_prompt = (
        f"# 你的原始建议\n```json\n{json.dumps(proposals, ensure_ascii=False, indent=2)}\n```\n\n"
        f"# 风控官的质疑\n```json\n{json.dumps(concerns, ensure_ascii=False, indent=2)}\n```"
    )
    defense_text = _call_opus(defense_prompt, DEFENSE_SYSTEM)
    defenses = _parse_defense(defense_text)

    # 处理答辩结果
    withdrawn_ids = {d["proposal_id"] for d in defenses if d.get("action") == "withdraw"}
    revised = {d["proposal_id"]: d for d in defenses if d.get("action") == "revise"}

    # 更新 proposals
    final_proposals = []
    still_disputed = []
    for p in surviving:
        pid = p.get("id")
        if pid in withdrawn_ids:
            continue
        if pid in revised:
            p = {**p, "proposed_value": revised[pid].get("revised_value", p.get("proposed_value"))}
            # 如果修改了，不再争议
            final_proposals.append(p)
        elif any(c["proposal_id"] == pid for c in concerns):
            # 维持了但仍有疑虑 → 交仲裁
            still_disputed.append(p)
        else:
            final_proposals.append(p)

    if not still_disputed:
        if progress_cb:
            progress_cb(f"Round 3: 答辩完成，{len(final_proposals)} 条通过")
        return final_proposals

    # Step 3: 仲裁
    if progress_cb:
        progress_cb(f"Round 3 Step 3: 仲裁 ({len(still_disputed)} 条分歧)...")

    concern_map = {c["proposal_id"]: c for c in concerns}
    defense_map = {d["proposal_id"]: d for d in defenses if d.get("action") == "maintain"}

    arbitrate_prompt = "# 待仲裁的分歧\n\n"
    for p in still_disputed:
        pid = p.get("id")
        arbitrate_prompt += (
            f"## {pid}\n"
            f"建议: {json.dumps(p, ensure_ascii=False)}\n"
            f"质疑: {json.dumps(concern_map.get(pid, {}), ensure_ascii=False)}\n"
            f"答辩: {json.dumps(defense_map.get(pid, {}), ensure_ascii=False)}\n\n"
        )

    arbitrate_text = _call_opus(arbitrate_prompt, ARBITRATE_SYSTEM)
    finals = _parse_final(arbitrate_text)

    adopted_ids = {f["proposal_id"] for f in finals if f.get("decision") == "adopt"}
    for p in still_disputed:
        if p.get("id") in adopted_ids:
            final_proposals.append(p)

    if progress_cb:
        progress_cb(f"Round 3 完成: {len(final_proposals)} 条最终采纳")

    return final_proposals
```

- [ ] **Step 2: Smoke test imports**

Run: `cd /c/LinDangAgent && python -c "from knowledge.learning_reflector import run_reflection, run_cross_review; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add knowledge/learning_reflector.py
git commit -m "feat(learn): add learning_reflector with Opus reflection and cross-review"
```

---

## Task 5: learning_optimizer.py — Round 4-5 Config Generation & Validation

**Files:**
- Create: `knowledge/learning_optimizer.py`
- Create: `tests/test_learning_optimizer.py`

- [ ] **Step 1: Write failing tests for proposal application and safety enforcement**

```python
# tests/test_learning_optimizer.py
import json
import pytest
from knowledge.learning_optimizer import apply_proposal, check_adoption_criteria


def test_apply_weight_proposal():
    tree = {
        "weights": {"预期差": 0.30, "技术面": 0.40, "基本面": 0.20, "资金面": 0.10},
        "correction_rules": {},
        "trees": {},
    }
    proposal = {
        "id": "P1", "type": "weight",
        "target": "技术面", "proposed_value": "0.35",
    }
    new_tree, errors = apply_proposal(tree, proposal)
    assert not errors
    assert new_tree["weights"]["技术面"] == 0.35


def test_apply_weight_violates_bounds():
    tree = {
        "weights": {"预期差": 0.30, "技术面": 0.40, "基本面": 0.20, "资金面": 0.10},
        "correction_rules": {},
        "trees": {},
    }
    proposal = {
        "id": "P1", "type": "weight",
        "target": "技术面", "proposed_value": "0.60",
    }
    new_tree, errors = apply_proposal(tree, proposal)
    assert errors  # should have safety violation


def test_apply_rule_proposal():
    tree = {
        "weights": {},
        "correction_rules": {
            "fundamental_circuit_breaker": {"condition": {"基本面": {"<=": 25}}, "action": {"cap": 30}},
        },
        "trees": {},
    }
    proposal = {
        "id": "P2", "type": "rule",
        "target": "fundamental_breaker",
        "proposed_value": "20",
    }
    new_tree, errors = apply_proposal(tree, proposal)
    assert not errors


def test_check_adoption_criteria_pass():
    result = check_adoption_criteria(
        old_hit_rate=55.0, new_hit_rate=60.0,
        old_by_category={"big_rise": {"hit_rate": 70}},
        new_by_category={"big_rise": {"hit_rate": 65}},
        old_calibration=5.0, new_calibration=8.0,
    )
    assert result["adopted"]


def test_check_adoption_criteria_insufficient_improvement():
    result = check_adoption_criteria(
        old_hit_rate=55.0, new_hit_rate=56.0,
        old_by_category={}, new_by_category={},
        old_calibration=5.0, new_calibration=6.0,
    )
    assert not result["adopted"]


def test_check_adoption_criteria_cliff_drop():
    result = check_adoption_criteria(
        old_hit_rate=55.0, new_hit_rate=62.0,
        old_by_category={"big_rise": {"hit_rate": 70}},
        new_by_category={"big_rise": {"hit_rate": 50}},  # -20% drop
        old_calibration=5.0, new_calibration=8.0,
    )
    assert not result["adopted"]
    assert "断崖" in result["reason"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/LinDangAgent && python -m pytest tests/test_learning_optimizer.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement learning_optimizer.py**

```python
# knowledge/learning_optimizer.py
# -*- coding: utf-8 -*-
"""统一学习引擎 — Round 4-5: 配置生成 + 验证 + 采纳/回退。"""

import json
import logging
from datetime import datetime
from pathlib import Path

from knowledge.learning_config import (
    SAFETY_BOUNDS, DIMENSIONS, STAGING_DIR,
    ADOPTION_HIT_RATE_IMPROVEMENT, ADOPTION_NO_CLIFF_DROP,
    validate_weights, validate_rule_thresholds,
    ensure_staging, clear_staging, load_production_tree,
    save_staging_tree, promote_staging, save_learning_log,
    STAGING_PROMPT_PATH, STAGING_CHANGELOG, STAGING_RULES_PATH,
)

logger = logging.getLogger(__name__)


# ── Round 4: 应用 Proposals ──────────────────────────────────────

def apply_proposal(tree: dict, proposal: dict) -> tuple[dict, list[str]]:
    """将单条 proposal 应用到决策树副本上。

    返回 (modified_tree, errors)。errors 非空表示安全边界违规。
    """
    import copy
    new_tree = copy.deepcopy(tree)
    errors = []
    p_type = proposal.get("type")
    target = proposal.get("target", "")
    value = proposal.get("proposed_value", "")

    if p_type == "weight":
        # 找到目标维度
        dim = None
        for d in DIMENSIONS:
            if d in target:
                dim = d
                break
        if not dim:
            errors.append(f"未识别的权重目标: {target}")
            return new_tree, errors

        try:
            new_w = float(value)
        except (ValueError, TypeError):
            errors.append(f"权重值无效: {value}")
            return new_tree, errors

        new_tree.setdefault("weights", {})[dim] = new_w
        errs = validate_weights(new_tree["weights"])
        if errs:
            errors.extend(errs)

    elif p_type == "rule":
        try:
            new_val = float(value)
        except (ValueError, TypeError):
            errors.append(f"规则阈值无效: {value}")
            return new_tree, errors

        # 映射到验证键名
        rule_map = {
            "fundamental_breaker": "fundamental_breaker",
            "熔断": "fundamental_breaker",
            "bucket": "bucket_cap",
            "木桶": "bucket_cap",
            "premortem": "premortem_cap",
            "预mortem": "premortem_cap",
        }
        rule_key = None
        for keyword, key in rule_map.items():
            if keyword in target.lower():
                rule_key = key
                break

        if rule_key:
            errs = validate_rule_thresholds({rule_key: new_val})
            if errs:
                errors.extend(errs)
        # Apply to tree's correction_rules
        rules = new_tree.get("correction_rules", {})
        for rname, rdata in rules.items():
            if any(k in rname.lower() for k in target.lower().split()):
                if "condition" in rdata:
                    for dim_key, cond in rdata["condition"].items():
                        for op in cond:
                            rdata["condition"][dim_key][op] = new_val
                elif "action" in rdata and "cap" in rdata["action"]:
                    rdata["action"]["cap"] = new_val

    elif p_type == "tree":
        # 决策树结构变更 — 存储为 JSON patch 格式，人工+验证后生效
        logger.info("[learn] tree structure proposal: %s", proposal.get("id"))
        # 基本验证: 不能删除整个维度
        for dim in DIMENSIONS:
            if dim in target and "删除" in str(value):
                errors.append(f"不允许删除整个维度: {dim}")

    elif p_type == "prompt":
        # Prompt 变更不改 tree，单独存储
        logger.info("[learn] prompt proposal stored for human review: %s", proposal.get("id"))

    return new_tree, errors


def apply_all_proposals(proposals: list[dict], progress_cb=None) -> dict:
    """Round 4: 将所有采纳的 proposals 应用到 staging 区。

    返回: {staging_tree, prompt_proposals, errors, applied_count}
    """
    ensure_staging()
    tree = load_production_tree()
    prompt_proposals = []
    all_errors = []
    applied = 0

    for p in proposals:
        if p.get("type") == "prompt":
            prompt_proposals.append(p)
            applied += 1
            continue

        new_tree, errors = apply_proposal(tree, p)
        if errors:
            all_errors.append({"proposal_id": p.get("id"), "errors": errors})
            if progress_cb:
                progress_cb(f"⚠️ {p.get('id')} 安全边界违规: {errors}")
        else:
            tree = new_tree
            applied += 1

    # 保存到 staging
    save_staging_tree(tree)

    # 保存 prompt proposals (需人工审批)
    if prompt_proposals:
        STAGING_PROMPT_PATH.write_text(
            json.dumps(prompt_proposals, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # 写 changelog
    changelog_lines = [
        f"# 学习引擎配置变更 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"\n已应用 {applied} 条建议，{len(all_errors)} 条因安全边界被拒绝。",
    ]
    for p in proposals:
        if p.get("type") != "prompt" and p.get("id") not in {e["proposal_id"] for e in all_errors}:
            changelog_lines.append(f"\n- [{p.get('id')}] {p.get('type')}: {p.get('target')} → {p.get('proposed_value')}")
    if prompt_proposals:
        changelog_lines.append(f"\n⏳ {len(prompt_proposals)} 条 prompt 变更待人工审批")
    STAGING_CHANGELOG.write_text("\n".join(changelog_lines), encoding="utf-8")

    if progress_cb:
        progress_cb(f"Round 4 完成: {applied} 条应用, {len(all_errors)} 条拒绝, {len(prompt_proposals)} 条待审批")

    return {
        "staging_tree": tree,
        "prompt_proposals": prompt_proposals,
        "errors": all_errors,
        "applied_count": applied,
    }


# ── Round 5: 验证 ────────────────────────────────────────────────

def check_adoption_criteria(
    old_hit_rate: float,
    new_hit_rate: float,
    old_by_category: dict,
    new_by_category: dict,
    old_calibration: float,
    new_calibration: float,
) -> dict:
    """检查是否满足采纳条件。

    返回: {adopted: bool, reason: str, details: dict}
    """
    reasons = []

    # 条件 1: 胜率提升 >= 3%
    improvement = new_hit_rate - old_hit_rate
    if improvement < ADOPTION_HIT_RATE_IMPROVEMENT:
        reasons.append(f"胜率提升 {improvement:.1f}% < {ADOPTION_HIT_RATE_IMPROVEMENT}% 门槛")

    # 条件 2: 无断崖下跌
    for cat in set(list(old_by_category.keys()) + list(new_by_category.keys())):
        old_hr = old_by_category.get(cat, {}).get("hit_rate", 0)
        new_hr = new_by_category.get(cat, {}).get("hit_rate", 0)
        if old_hr - new_hr > ADOPTION_NO_CLIFF_DROP:
            reasons.append(f"{cat} 类胜率断崖下跌: {old_hr:.1f}% → {new_hr:.1f}%")

    # 条件 3: 评分校准度改善
    if new_calibration < old_calibration:
        reasons.append(f"评分校准度退步: {old_calibration:.1f} → {new_calibration:.1f}")

    adopted = len(reasons) == 0
    return {
        "adopted": adopted,
        "reason": "; ".join(reasons) if reasons else "全部达标",
        "details": {
            "old_hit_rate": old_hit_rate,
            "new_hit_rate": new_hit_rate,
            "improvement": round(improvement, 1),
            "calibration_old": old_calibration,
            "calibration_new": new_calibration,
        },
    }


def run_validation(
    holdout_exams: list[dict],
    staging_tree: dict,
    delay_between: int = 30,
    progress_cb=None,
) -> dict:
    """Round 5: 在验证集上对比新旧配置。

    返回: {adopted, old_stats, new_stats, reason}
    """
    from knowledge.learning_backtester import run_single_backtest, grade_result
    from knowledge.simulation_training import (
        _fetch_historical_kline, _get_market_return, _get_sector_return,
        _calc_return_from_kline,
    )
    import time

    if progress_cb:
        progress_cb(f"Round 5: 验证集 {len(holdout_exams)} 只股票...")

    # 用新配置跑 holdout（war_room 会从 staging 读取 tree —— 需要临时替换）
    # 策略: 直接用 run_single_backtest，因为 war_room 每次调用 load_tree() 会读文件
    # 先 promote staging，跑完再 rollback

    from knowledge.learning_config import (
        DECISION_TREE_PATH, STAGING_TREE_PATH,
        load_production_tree, save_staging_tree,
    )
    import shutil

    old_tree = load_production_tree()
    old_tree_backup = DECISION_TREE_PATH.with_suffix(".json.bak")

    results_new = []
    results_old_projected = []

    # 先用旧配置跑（当前生产配置）
    if progress_cb:
        progress_cb("Round 5: 旧配置回测...")

    for i, exam in enumerate(holdout_exams):
        r = run_single_backtest(exam, progress_cb)
        if r:
            results_old_projected.append(r)
        if i < len(holdout_exams) - 1 and delay_between > 0:
            time.sleep(delay_between)

    # 临时替换为新配置
    shutil.copy2(DECISION_TREE_PATH, old_tree_backup)
    try:
        save_staging_tree(staging_tree)
        # 将 staging tree 复制到生产位置
        shutil.copy2(STAGING_TREE_PATH, DECISION_TREE_PATH)

        # 清除 decision_tree 模块缓存
        try:
            from services.decision_tree import reload_tree
            reload_tree()
        except Exception:
            pass

        if progress_cb:
            progress_cb("Round 5: 新配置回测...")

        for i, exam in enumerate(holdout_exams):
            r = run_single_backtest(exam, progress_cb)
            if r:
                results_new.append(r)
            if i < len(holdout_exams) - 1 and delay_between > 0:
                time.sleep(delay_between)
    finally:
        # 恢复旧配置
        shutil.copy2(old_tree_backup, DECISION_TREE_PATH)
        old_tree_backup.unlink(missing_ok=True)
        try:
            from services.decision_tree import reload_tree
            reload_tree()
        except Exception:
            pass

    # 统计对比
    def calc_stats(results):
        total = len(results)
        hits = sum(1 for r in results if r["verdict"] == "hit")
        hit_rate = hits / total * 100 if total > 0 else 0
        by_cat = {}
        for r in results:
            c = r["category"]
            by_cat.setdefault(c, {"total": 0, "hits": 0})
            by_cat[c]["total"] += 1
            if r["verdict"] == "hit":
                by_cat[c]["hits"] += 1
        for v in by_cat.values():
            v["hit_rate"] = round(v["hits"] / v["total"] * 100, 1) if v["total"] > 0 else 0
        # 校准度: 高分股均α - 低分股均α
        high = [r["excess_return"] for r in results if r.get("weighted", 0) >= 70]
        low = [r["excess_return"] for r in results if r.get("weighted", 0) < 50]
        calibration = 0.0
        if high and low:
            calibration = (sum(high) / len(high)) - (sum(low) / len(low))
        return {"hit_rate": round(hit_rate, 1), "by_category": by_cat, "calibration": round(calibration, 2)}

    old_stats = calc_stats(results_old_projected)
    new_stats = calc_stats(results_new)

    criteria = check_adoption_criteria(
        old_hit_rate=old_stats["hit_rate"],
        new_hit_rate=new_stats["hit_rate"],
        old_by_category=old_stats["by_category"],
        new_by_category=new_stats["by_category"],
        old_calibration=old_stats["calibration"],
        new_calibration=new_stats["calibration"],
    )

    if criteria["adopted"]:
        promote_staging()
        try:
            from services.decision_tree import reload_tree
            reload_tree()
        except Exception:
            pass
        if progress_cb:
            progress_cb(f"✅ Round 5: 新配置采纳！胜率 {old_stats['hit_rate']}% → {new_stats['hit_rate']}%")
    else:
        clear_staging()
        if progress_cb:
            progress_cb(f"❌ Round 5: 未达标，回退。原因: {criteria['reason']}")

    return {
        "adopted": criteria["adopted"],
        "reason": criteria["reason"],
        "old_stats": old_stats,
        "new_stats": new_stats,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/LinDangAgent && python -m pytest tests/test_learning_optimizer.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add knowledge/learning_optimizer.py tests/test_learning_optimizer.py
git commit -m "feat(learn): add learning_optimizer with proposal application, safety enforcement, and validation"
```

---

## Task 6: learning_engine.py — Five-Round Orchestrator

**Files:**
- Create: `knowledge/learning_engine.py`

- [ ] **Step 1: Implement learning_engine.py**

```python
# knowledge/learning_engine.py
# -*- coding: utf-8 -*-
"""统一学习引擎 — 主入口，编排五轮循环。

用法:
  python cli.py learn general 50    # 通用分析回测
  python cli.py learn general 5     # 小规模测试
"""

import json
import logging
from datetime import datetime

from knowledge.learning_config import (
    ensure_staging, clear_staging, save_learning_log,
    STAGING_PROMPT_PATH,
)

logger = logging.getLogger(__name__)


def run_learning_cycle(
    mode: str = "general",
    count: int = 50,
    delay_between: int = 30,
    progress_cb=None,
) -> dict:
    """执行完整五轮学习循环。

    Args:
        mode: 学习模式 ("general" | "dragon" | "weights" | "full")
        count: 回测股票数量
        delay_between: 每只股票之间的间隔秒数
        progress_cb: 进度回调函数

    Returns:
        学习结果字典
    """
    if progress_cb:
        progress_cb(f"=== 统一学习引擎启动 [{mode}] count={count} ===")

    ensure_staging()
    clear_staging()

    result = {
        "mode": mode,
        "count": count,
        "started_at": datetime.now().isoformat(),
        "rounds": {},
    }

    # ── Round 1: 批量回测 ─────────────────────────────────────
    if progress_cb:
        progress_cb("=" * 50)
        progress_cb("Round 1: 批量回测")

    from knowledge.learning_backtester import run_backtest_round

    r1 = run_backtest_round(
        count=count,
        delay_between=delay_between,
        progress_cb=progress_cb,
    )

    if r1["status"] != "ok":
        result["status"] = "failed_round1"
        result["message"] = r1.get("message", "Round 1 失败")
        if progress_cb:
            progress_cb(f"Round 1 失败: {result['message']}")
        return result

    result["rounds"]["round1"] = r1["stats"]
    train_results = r1["train_results"]
    holdout_exams = r1["holdout_exams"]

    if progress_cb:
        s = r1["stats"]
        progress_cb(f"Round 1 完成: {s['total']} 只, 胜率 {s['hit_rate']}%")

    # ── Round 2: Opus 统一反思 ────────────────────────────────
    if progress_cb:
        progress_cb("=" * 50)
        progress_cb("Round 2: Opus 统一反思")

    from knowledge.learning_reflector import run_reflection

    proposals = run_reflection(train_results, r1["stats"], progress_cb)
    result["rounds"]["round2"] = {"proposals": proposals}

    if not proposals:
        result["status"] = "no_proposals"
        result["message"] = "Opus 认为当前配置已足够好，无需调整"
        result["summary"] = "本轮学习: Opus 审视后认为当前配置无需调整"
        save_learning_log(result, mode, count)
        if progress_cb:
            progress_cb("Opus 未提出调整建议，本轮结束")
        return result

    # ── Round 3: Opus 交叉审视 ────────────────────────────────
    if progress_cb:
        progress_cb("=" * 50)
        progress_cb("Round 3: Opus 交叉审视")

    from knowledge.learning_reflector import run_cross_review

    adopted_proposals = run_cross_review(proposals, train_results, r1["stats"], progress_cb)
    result["rounds"]["round3"] = {
        "original_count": len(proposals),
        "adopted_count": len(adopted_proposals),
        "adopted_proposals": adopted_proposals,
    }

    if not adopted_proposals:
        result["status"] = "all_rejected"
        result["message"] = "所有建议在交叉审视中被否决"
        result["summary"] = "本轮学习: 所有调整建议被风控审视否决"
        save_learning_log(result, mode, count)
        if progress_cb:
            progress_cb("所有建议被否决，本轮结束")
        return result

    # ── Round 4: 应用候选配置 ─────────────────────────────────
    if progress_cb:
        progress_cb("=" * 50)
        progress_cb("Round 4: 应用候选配置")

    from knowledge.learning_optimizer import apply_all_proposals

    r4 = apply_all_proposals(adopted_proposals, progress_cb)
    result["rounds"]["round4"] = {
        "applied_count": r4["applied_count"],
        "errors": r4["errors"],
        "has_prompt_changes": len(r4["prompt_proposals"]) > 0,
    }

    # Prompt 变更需要人工审批
    if r4["prompt_proposals"]:
        if progress_cb:
            progress_cb(f"⚠️ {len(r4['prompt_proposals'])} 条 prompt 变更需要你的审批")
            progress_cb(f"   运行: python cli.py learn approve-prompt")

        # 发邮件通知
        try:
            from utils.email_sender import send_text_email, smtp_configured
            if smtp_configured():
                body_lines = ["统一学习引擎 — Prompt 变更待审批\n"]
                for p in r4["prompt_proposals"]:
                    body_lines.append(f"[{p.get('id')}] {p.get('target')}")
                    body_lines.append(f"  理由: {p.get('evidence', '')}")
                    body_lines.append(f"  建议: {p.get('proposed_value', '')[:200]}")
                    body_lines.append("")
                body_lines.append("请运行 python cli.py learn approve-prompt 审批")
                send_text_email("学习引擎 Prompt 变更待审批", "\n".join(body_lines))
        except Exception as exc:
            logger.warning("[learn] prompt notification email failed: %s", exc)

    if r4["applied_count"] == 0 or (r4["applied_count"] == len(r4["prompt_proposals"])):
        # 只有 prompt 变更（需人工审批），无自动变更可验证
        result["status"] = "pending_prompt_approval"
        result["message"] = "非 prompt 变更为零，prompt 变更待审批"
        result["summary"] = f"本轮学习: {len(r4['prompt_proposals'])} 条 prompt 变更待审批"
        save_learning_log(result, mode, count)
        return result

    # ── Round 5: 验证集对比 ───────────────────────────────────
    if progress_cb:
        progress_cb("=" * 50)
        progress_cb("Round 5: 验证集对比")

    from knowledge.learning_optimizer import run_validation

    r5 = run_validation(
        holdout_exams=holdout_exams,
        staging_tree=r4["staging_tree"],
        delay_between=delay_between,
        progress_cb=progress_cb,
    )
    result["rounds"]["round5"] = r5

    if r5["adopted"]:
        result["status"] = "adopted"
        old_hr = r5["old_stats"]["hit_rate"]
        new_hr = r5["new_stats"]["hit_rate"]
        result["summary"] = f"本轮学习: 新配置采纳，胜率 {old_hr}% → {new_hr}%"
    else:
        result["status"] = "not_adopted"
        result["summary"] = f"本轮学习: 未达标回退。原因: {r5['reason']}"

    result["finished_at"] = datetime.now().isoformat()

    # 保存日志
    save_learning_log(result, mode, count)

    # 发送结果邮件
    _send_result_email(result)

    if progress_cb:
        progress_cb(f"=== 学习引擎完成: {result['status']} ===")
        progress_cb(result["summary"])

    return result


def approve_prompt_patches(progress_cb=None) -> dict:
    """审批 staging 中的 prompt 变更。"""
    if not STAGING_PROMPT_PATH.exists():
        return {"status": "no_pending", "message": "无待审批的 prompt 变更"}

    patches = json.loads(STAGING_PROMPT_PATH.read_text(encoding="utf-8"))
    if not patches:
        return {"status": "no_pending", "message": "无待审批的 prompt 变更"}

    if progress_cb:
        progress_cb(f"审批 {len(patches)} 条 prompt 变更:")
        for p in patches:
            progress_cb(f"  [{p.get('id')}] {p.get('target')}: {p.get('proposed_value', '')[:100]}")

    # 标记为已审批
    for p in patches:
        p["approved_at"] = datetime.now().isoformat()
    STAGING_PROMPT_PATH.write_text(
        json.dumps(patches, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if progress_cb:
        progress_cb("✅ Prompt 变更已审批，将在下次分析时生效")

    return {"status": "approved", "count": len(patches)}


def _send_result_email(result: dict):
    """发送学习结果邮件。"""
    try:
        from utils.email_sender import send_text_email, smtp_configured
        if not smtp_configured():
            return

        lines = [
            "统一学习引擎 — 学习报告",
            "=" * 40,
            f"\n状态: {result.get('status', '?')}",
            f"摘要: {result.get('summary', '?')}",
        ]

        r1 = result.get("rounds", {}).get("round1", {})
        if r1:
            lines.append(f"\nRound 1: {r1.get('total', 0)} 只, 胜率 {r1.get('hit_rate', 0)}%")

        r3 = result.get("rounds", {}).get("round3", {})
        if r3:
            lines.append(f"Round 3: {r3.get('original_count', 0)} 条建议 → {r3.get('adopted_count', 0)} 条采纳")

        r5 = result.get("rounds", {}).get("round5", {})
        if r5:
            lines.append(f"Round 5: 旧{r5.get('old_stats', {}).get('hit_rate', 0)}% → 新{r5.get('new_stats', {}).get('hit_rate', 0)}%")
            lines.append(f"结论: {'采纳' if r5.get('adopted') else '回退'}")

        send_text_email("学习引擎报告", "\n".join(lines))
    except Exception as exc:
        logger.warning("[learn] result email failed: %s", exc)
```

- [ ] **Step 2: Smoke test imports**

Run: `cd /c/LinDangAgent && python -c "from knowledge.learning_engine import run_learning_cycle, approve_prompt_patches; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add knowledge/learning_engine.py
git commit -m "feat(learn): add learning_engine five-round orchestrator"
```

---

## Task 7: Register CLI Commands in cli.py

**Files:**
- Modify: `cli.py:1782-1862` (COMMANDS dict)
- Modify: `cli.py` (add cmd_learn functions)

- [ ] **Step 1: Add command handler functions to cli.py**

Find the area near other `cmd_*` functions (around line 1530) and add:

```python
def cmd_learn(args: list[str]):
    """统一学习引擎: learn <mode> [count]"""
    if not args:
        _json_out({"error": "用法: learn <general|dragon|weights|full|approve-prompt> [count]"})
        return

    mode = args[0]

    if mode == "approve-prompt":
        from knowledge.learning_engine import approve_prompt_patches
        result = approve_prompt_patches(progress_cb=lambda msg: print(f"  {msg}"))
        _json_out(result)
        return

    count = int(args[1]) if len(args) > 1 else 50
    delay = int(args[2]) if len(args) > 2 else 30

    from knowledge.learning_engine import run_learning_cycle
    result = run_learning_cycle(
        mode=mode,
        count=count,
        delay_between=delay,
        progress_cb=lambda msg: print(f"  {msg}"),
    )
    _json_out(result)
```

- [ ] **Step 2: Register in COMMANDS dict**

Find the `COMMANDS = {` dict (line ~1782) and add the entry:

```python
"learn": lambda args: cmd_learn(args),
```

- [ ] **Step 3: Verify CLI registration**

Run: `cd /c/LinDangAgent && python cli.py learn 2>&1 | head -5`
Expected: JSON output with error message about usage (not a Python traceback)

- [ ] **Step 4: Commit**

```bash
git add cli.py
git commit -m "feat(learn): register 'learn' command family in CLI"
```

---

## Task 8: End-to-End Smoke Test (5 stocks)

- [ ] **Step 1: Run a minimal learning cycle**

Run: `cd /c/LinDangAgent && python cli.py learn general 5`

Expected: The five-round cycle runs (may take 15-30 minutes with 5 stocks × 2 Opus calls each + 2-4 reflection calls). Watch for:
- Round 1 completes with stats
- Round 2 produces proposals (or says "no changes needed")
- Round 3 reviews proposals
- Round 4 applies to staging
- Round 5 validates (or skips if no proposals)
- Final status is one of: `adopted`, `not_adopted`, `no_proposals`, `all_rejected`

If any round fails, debug the specific error before proceeding.

- [ ] **Step 2: Verify learning log was written**

Run: `ls -la /c/LinDangAgent/data/knowledge/learning_log/`
Expected: A JSON file like `2026-04-12_general_5.json`

- [ ] **Step 3: Verify staging was cleaned up**

Run: `ls /c/LinDangAgent/data/knowledge/staging/ 2>/dev/null || echo "staging cleaned"`
Expected: Either empty (if adopted and promoted) or cleaned (if not adopted)

- [ ] **Step 4: Commit any fixes from smoke test**

```bash
git add -A
git commit -m "fix(learn): fixes from end-to-end smoke test"
```

---

## Task 9: Mark simulation_training.py as deprecated

**Files:**
- Modify: `knowledge/simulation_training.py:1-17`

- [ ] **Step 1: Add deprecation notice to module docstring**

```python
# OLD (line 1-17):
"""模拟训练 — AlphaGo 式自我对弈
...
"""

# NEW:
"""模拟训练 — AlphaGo 式自我对弈 [DEPRECATED]

⚠️ 此模块已被统一学习引擎 (knowledge/learning_engine.py) 取代。
新入口: python cli.py learn general <count>
保留原因: Phase 2-4 迁移完成前，sim-train CLI 命令仍可用。

原始说明:
用历史数据模拟分析 → 对比已知结果 → 生成教训 → 存入 case_memory。
...
"""
```

- [ ] **Step 2: Commit**

```bash
git add knowledge/simulation_training.py
git commit -m "docs: mark simulation_training.py as deprecated in favor of unified learning engine"
```
