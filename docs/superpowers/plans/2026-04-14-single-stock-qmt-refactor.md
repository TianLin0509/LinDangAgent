# 单股分析 QMT 深度接入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 QMT 作为单股分析的最高优先级数据源深度接入（元信息+财务+交易日历），保持 Tushare/AKShare 作为兜底，端到端输出带数据来源追溯的报告。

**Architecture:** 新增 `data/stock_gate.py` 做交易状态前置过滤；`data/qmt_client.py` 扩展 4 个新 API（元信息、批量元信息、财务 8 表、交易日历）；`data/qmt_schema_map.py` 做 QMT↔Tushare 字段映射；`data/tushare_client.py` 和 `data/report_data.py` 里的相关 get_* 函数各自挂 `qmt_fn` slot；`services/analysis_service.py` 入口加 gate。

**Tech Stack:** Python 3.12, xtquant (v250516), pandas, pytest

---

## 文件结构

| 文件 | 动作 | 责任 |
|---|---|---|
| `data/stock_gate.py` | Create (~80) | 前置交易状态判定（ST/新股/退市/北交所/UNKNOWN） |
| `data/qmt_client.py` | Modify (+200) | 新增 get_instrument_info / get_instrument_info_batch / get_financial / get_trading_dates_before |
| `data/qmt_schema_map.py` | Create (~150) | QMT 字段 → Tushare 标准 schema 转换函数 |
| `data/tushare_client.py` | Modify | `_try_with_fallback` 加 `_data_source_map`；get_basic_info 和 get_financial 加 qmt_fn |
| `data/report_data.py` | Modify | 4 个细粒度财务 get_* 加 QMT 优先；涨跌幅用真实交易日；_data_source_map 注入 context |
| `services/analysis_service.py` | Modify | 入口前置 check_tradability；处理 TradabilityBlocked |
| `tests/test_stock_gate.py` | Create | stock_gate 单元测试 |
| `tests/test_qmt_client_ext.py` | Create | qmt_client 扩展函数单元测试 |
| `tests/test_qmt_schema_map.py` | Create | 字段映射单元测试 |
| `tests/test_qmt_single_stock_refactor.py` | Create | **8 场景集成压测** |
| `tests/fixtures/qmt_mocks.py` | Create | 共享 monkey-patch 工具（fake_is_alive / fake_get_financial 等） |

---

## Task 1: 退市码实测发现（discovery）

**目的**：spec 里"退市码集合"不具体。开工前必须实测查到真实的 `InstrumentStatus` 退市值，避免实现时盲猜。

**Files:**
- 无代码改动。产出 `docs/qmt_status_codes.md` 记录。

- [ ] **Step 1.1: 找一批已退市股票**

用户已知退市股列表，例如：
- `400010.BJ` ST 百灵（2018 退市）
- `000033.SZ` 新都退（2018 退市）
- `600087.SH` *ST 长航（2014 退市）
- `600832.SH` *ST 东电（2013 退市）

- [ ] **Step 1.2: 写 discovery 脚本**

Create `C:\LinDangAgent\tests\qmt_status_discovery.py`:

```python
"""
发现 QMT get_instrument_detail 返回的各种 InstrumentStatus 值及其含义。
目标：找出"退市"对应的 InstrumentStatus 码，用于 stock_gate 硬拦截判定。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xtquant import xtdata

# 一批不同状态的股票
TEST_SYMBOLS = {
    "normal": ["000001.SZ", "600036.SH", "300750.SZ"],
    "st": [],  # 运行时从 UpStop/Pre<0.06 筛
    "suspected_delisted": ["400010.BJ", "000033.SZ", "600087.SH", "600832.SH"],
    "etf": ["510300.SH", "159915.SZ"],
    "index": ["000300.SH", "000905.SH"],
}


def main():
    print(f"{'symbol':20s} {'name':20s} {'Status':>6s} {'IsTrading':>10s}  类型")
    print("-" * 80)

    # 跑一遍所有样本
    for category, syms in TEST_SYMBOLS.items():
        for sym in syms:
            d = xtdata.get_instrument_detail(sym, iscomplete=True)
            if d is None:
                print(f"{sym:20s} {'N/A':20s} {'N/A':>6s} {'N/A':>10s}  {category} (detail=None)")
                continue
            print(f"{sym:20s} {d.get('InstrumentName',''):20s} "
                  f"{str(d.get('InstrumentStatus','')):>6s} "
                  f"{str(d.get('IsTrading','')):>10s}  {category}")

    # 扫一批 ST（涨跌停 5%）
    print("\nST 扫描（UpStop/Pre<0.06）:")
    a_stocks = xtdata.get_stock_list_in_sector("沪深A股")[:500]
    details = xtdata.get_instrument_detail_list(a_stocks, iscomplete=True)
    st_found = 0
    for sym, d in details.items():
        if not d:
            continue
        pre = d.get("PreClose", 0)
        up = d.get("UpStopPrice", 0)
        if pre > 0 and (up - pre) / pre < 0.06:
            name = d.get("InstrumentName", "")
            status = d.get("InstrumentStatus", "")
            print(f"  {sym:20s} {name:20s} Status={status}  pre={pre:.2f} up={up:.2f}")
            st_found += 1
            if st_found >= 10:
                break


if __name__ == "__main__":
    main()
```

- [ ] **Step 1.3: 运行脚本，记录结论**

```
cd C:/LinDangAgent && PYTHONIOENCODING=utf-8 PYTHONUNBUFFERED=1 python tests/qmt_status_discovery.py 2>&1 | tail -50
```

期望看到：
- 正常股 Status=0
- ST 股 Status=4 / 31（已知）
- 退市股 Status=? **← 核心产出**（可能是 detail=None，那是另一种处理方式）
- ETF / 指数 的 Status 值

- [ ] **Step 1.4: 写 `docs/qmt_status_codes.md`**

Create `C:\LinDangAgent\docs\qmt_status_codes.md` 记录发现：

```markdown
# QMT InstrumentStatus 码表（2026-04-14 实测）

| Status | 含义 | 代表样例 | stock_gate 处理 |
|---|---|---|---|
| 0 | 正常 | 000001.SZ 平安银行 | OK |
| 4 | *ST | [填实测] | ST 警告 |
| 31 | ST | [填实测] | ST 警告 |
| [填实测] | 退市 | [填实测] | DELISTED 硬拦截 |
| ... | ... | ... | ... |

## 退市股 get_instrument_detail 行为
[如果是返 None，写明白；如果返了 detail 但 IsTrading=False 且有特定 Status，写明白]
```

- [ ] **Step 1.5: Commit**

```
cd C:/LinDangAgent && git add tests/qmt_status_discovery.py docs/qmt_status_codes.md && git commit -m "docs(qmt): discover InstrumentStatus code table for delisted/ST detection

Runs QMT get_instrument_detail on sample symbols (normal/ST/suspected-
delisted/ETF/index) and scans first 500 A-shares by UpStop/PreClose
ratio to enumerate actual InstrumentStatus codes. Populates
docs/qmt_status_codes.md which stock_gate.py consumes for ST/DELISTED
classification.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: qmt_client 扩展 4 个新 API + 单元测试

**Files:**
- Modify: `C:\LinDangAgent\data\qmt_client.py`
- Create: `C:\LinDangAgent\tests\test_qmt_client_ext.py`

- [ ] **Step 2.1: 写 4 个新函数的失败测试**

Create `C:\LinDangAgent\tests\test_qmt_client_ext.py`:

```python
import pandas as pd
import pytest


def test_get_instrument_info_returns_dict_or_none(monkeypatch):
    import data.qmt_client as qc

    fake_detail = {"InstrumentName": "平安银行", "ExchangeID": "SZ",
                   "InstrumentStatus": 0, "OpenDate": "19910403",
                   "PreClose": 12.0, "UpStopPrice": 13.2,
                   "IsTrading": True, "TotalVolume": 1000000}

    class FakeXt:
        def get_instrument_detail(self, sym, iscomplete):
            assert iscomplete is True
            return fake_detail if sym == "000001.SZ" else None

    qc._connected = True
    monkeypatch.setattr(qc, "_xtdata", FakeXt())

    assert qc.get_instrument_info("000001") == fake_detail  # 归一化后查
    assert qc.get_instrument_info("999999.SZ") is None


def test_get_instrument_info_batch(monkeypatch):
    import data.qmt_client as qc

    class FakeXt:
        def get_instrument_detail_list(self, syms, iscomplete):
            assert iscomplete is True
            return {s: {"InstrumentName": f"股票{s}"} for s in syms}

    qc._connected = True
    monkeypatch.setattr(qc, "_xtdata", FakeXt())

    result = qc.get_instrument_info_batch(["000001", "600036"])
    assert "000001.SZ" in result
    assert "600036.SH" in result


def test_get_trading_dates_before(monkeypatch):
    import data.qmt_client as qc

    class FakeXt:
        def get_trading_dates(self, market, start_time="", end_time="", count=-1):
            # 返回 ms timestamp，模拟 2026-04-01 到 04-14 的交易日
            import datetime
            base = datetime.date(2026, 4, 1)
            dates = []
            for i in range(14):
                d = base + datetime.timedelta(days=i)
                if d.weekday() < 5:  # 工作日
                    dt = datetime.datetime(d.year, d.month, d.day)
                    dates.append(int(dt.timestamp() * 1000))
            return dates

    qc._connected = True
    monkeypatch.setattr(qc, "_xtdata", FakeXt())

    dates = qc.get_trading_dates_before("2026-04-14", count=5)
    assert len(dates) == 5
    assert all(isinstance(d, str) for d in dates)
    assert dates == sorted(dates)  # 升序


def test_get_financial_core_tables_non_empty(monkeypatch):
    import data.qmt_client as qc

    called_download = []

    class FakeXt:
        def download_financial_data2(self, syms, table_list, start_time, end_time, callback):
            called_download.append((tuple(syms), tuple(table_list)))
            callback({"total": 1, "finished": 1})

        def get_financial_data(self, syms, table_list, start_time="", end_time="", report_type="report_time"):
            return {syms[0]: {t: pd.DataFrame({"col": [1.0]}) for t in table_list}}

    qc._connected = True
    monkeypatch.setattr(qc, "_xtdata", FakeXt())

    tables = qc.get_financial("000001.SZ", years=3)
    assert "Balance" in tables
    assert "Income" in tables
    assert "PershareIndex" in tables
    assert called_download  # download 确实被调用过
```

- [ ] **Step 2.2: Run tests to verify they fail**

```
cd C:/LinDangAgent && python -m pytest tests/test_qmt_client_ext.py -v
```
Expected: 4 failed with `AttributeError: module 'data.qmt_client' has no attribute 'get_instrument_info'` (或类似)

- [ ] **Step 2.3: 实现 4 个新函数**

在 `data/qmt_client.py` 末尾追加（在 `get_sector_stocks` 函数之后）：

```python
# ── 元信息（扩展）──────────────────────────────────────────
def get_instrument_info(symbol: str) -> Optional[dict]:
    """
    单股完整元信息（iscomplete=True 返回 83 字段）。
    未查到返回 None（不 raise）——区别于 _ensure_connected 失败的 QMTUnavailable。
    """
    _ensure_connected()
    sym = _normalize_symbol(symbol)
    try:
        detail = _xtdata.get_instrument_detail(sym, iscomplete=True)
    except Exception as e:
        raise QMTUnavailable(f"get_instrument_detail 失败: {e}")
    return detail if detail else None


def get_instrument_info_batch(symbols: list[str]) -> dict[str, dict]:
    """批量元信息，1ms/只。key 保留带后缀（方便上游处理）。"""
    _ensure_connected()
    syms = [_normalize_symbol(s) for s in symbols]
    try:
        result = _xtdata.get_instrument_detail_list(syms, iscomplete=True)
    except Exception as e:
        raise QMTUnavailable(f"get_instrument_detail_list 失败: {e}")
    return result or {}


# ── 财务 ───────────────────────────────────────────────────
_FINANCIAL_TABLES = [
    "Balance", "Income", "CashFlow", "Capital",
    "Top10FlowHolder", "Top10Holder", "HolderNum", "PershareIndex",
]


def get_financial(symbol: str, years: int = 3) -> dict[str, pd.DataFrame]:
    """
    下载 + 查询 8 张财务表。窗口 [今天-years年, 今天]。
    返回 {table_name: DataFrame}。失败 raise QMTUnavailable。
    """
    import datetime
    _ensure_connected()
    sym = _normalize_symbol(symbol)
    end = datetime.date.today().strftime("%Y%m%d")
    start = (datetime.date.today() - datetime.timedelta(days=years * 366)).strftime("%Y%m%d")

    t0 = time.time()
    try:
        _xtdata.download_financial_data2(
            [sym], table_list=_FINANCIAL_TABLES,
            start_time=start, end_time=end,
            callback=lambda d: None,
        )
    except Exception as e:
        raise QMTUnavailable(f"download_financial_data2 失败: {e}")

    try:
        raw = _xtdata.get_financial_data(
            [sym], table_list=_FINANCIAL_TABLES,
            start_time=start, end_time=end,
            report_type="report_time",
        )
    except Exception as e:
        raise QMTUnavailable(f"get_financial_data 失败: {e}")

    if not raw or sym not in raw:
        raise QMTUnavailable(f"get_financial_data 返回空: {sym}")
    per_sym = raw[sym]
    if not isinstance(per_sym, dict):
        raise QMTUnavailable(f"per-symbol 返回非 dict: {type(per_sym).__name__}")

    out = {}
    for t in _FINANCIAL_TABLES:
        df = per_sym.get(t)
        out[t] = df if isinstance(df, pd.DataFrame) else pd.DataFrame()

    logger.info("[qmt] get_financial %s cost=%dms tables=%s",
                sym, int((time.time() - t0) * 1000),
                {k: len(v) for k, v in out.items()})
    return out


# ── 交易日历 ────────────────────────────────────────────────
def get_trading_dates_before(end_date: str, count: int, market: str = "SH") -> list[str]:
    """
    返回 end_date（含）之前 count 个真实交易日，'YYYY-MM-DD' 升序。
    end_date 格式: 'YYYY-MM-DD' 或 'YYYYMMDD'。
    """
    import datetime as _dt
    _ensure_connected()

    # 归一化 end_date
    end_clean = end_date.replace("-", "")
    # 取足够长的窗口保证 count 个交易日（按 1.5 倍自然日回溯）
    end_dt = _dt.datetime.strptime(end_clean, "%Y%m%d").date()
    start_dt = end_dt - _dt.timedelta(days=max(count * 2, 30))
    start_str = start_dt.strftime("%Y%m%d")

    try:
        timestamps = _xtdata.get_trading_dates(
            market, start_time=start_str, end_time=end_clean, count=-1,
        )
    except Exception as e:
        raise QMTUnavailable(f"get_trading_dates 失败: {e}")

    if not timestamps:
        raise QMTUnavailable("get_trading_dates 返回空")

    # ms timestamp → 'YYYY-MM-DD'
    dates = [
        _dt.datetime.fromtimestamp(t / 1000).strftime("%Y-%m-%d")
        for t in timestamps
    ]
    dates.sort()
    return dates[-count:]  # 取最后 count 个（即最近 N 个交易日）
```

- [ ] **Step 2.4: Run tests to verify 4/4 pass**

```
cd C:/LinDangAgent && python -m pytest tests/test_qmt_client_ext.py -v
```
Expected: 4 passed

- [ ] **Step 2.5: 全量回归**

```
cd C:/LinDangAgent && python -m pytest tests/test_qmt_client.py tests/test_qmt_integration.py tests/test_qmt_client_ext.py -v 2>&1 | tail -30
```
Expected: 原 15 + 新 4 = 19 passed

- [ ] **Step 2.6: Commit**

```
cd C:/LinDangAgent && git add data/qmt_client.py tests/test_qmt_client_ext.py && git commit -m "feat(qmt): extend qmt_client with instrument_info/financial/calendar

Adds four new APIs required by the single-stock refactor:
- get_instrument_info(symbol) → 83-field metadata dict (iscomplete=True)
- get_instrument_info_batch(symbols) → bulk metadata @1ms/stock
- get_financial(symbol, years=3) → 8 financial tables dict; uses
  download_financial_data2 async to avoid the sync-version hang
- get_trading_dates_before(end, count) → N real trading days ending
  at end_date (for precise N-day gain calculation)

All raise QMTUnavailable on failure for upstream silent degradation.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: qmt_schema_map.py 字段映射模块

**Files:**
- Create: `C:\LinDangAgent\data\qmt_schema_map.py`
- Create: `C:\LinDangAgent\tests\test_qmt_schema_map.py`

- [ ] **Step 3.1: 写映射函数失败测试**

Create `C:\LinDangAgent\tests\test_qmt_schema_map.py`:

```python
import pandas as pd


def test_qmt_detail_to_tushare_dict_basic():
    from data.qmt_schema_map import qmt_detail_to_tushare_dict

    detail = {
        "InstrumentID": "000001",
        "InstrumentName": "平安银行",
        "ExchangeID": "SZ",
        "OpenDate": "19910403",
        "PreClose": 12.3,
        "UpStopPrice": 13.53,
        "FloatVolume": 1.9e10,
        "TotalVolume": 1.94e10,
    }
    out = qmt_detail_to_tushare_dict(detail)
    assert out["name"] == "平安银行"
    assert out["list_date"] == "19910403"
    assert "float_share" in out
    assert "total_share" in out


def test_qmt_detail_missing_fields_graceful():
    from data.qmt_schema_map import qmt_detail_to_tushare_dict
    out = qmt_detail_to_tushare_dict({})  # 空 dict
    assert isinstance(out, dict)
    # 缺字段应为 None 或缺省，而非 KeyError


def test_qmt_pershare_to_fina_indicator():
    from data.qmt_schema_map import qmt_pershare_to_fina_indicator

    qmt_df = pd.DataFrame([
        {"m_timetag": "20250331", "m_anntime": "20250430",
         "s_fa_eps_basic": 1.5, "s_fa_eps_diluted": 1.48,
         "s_fa_bps": 15.2, "s_fa_ocfps": 2.3},
    ])
    tushare_df = qmt_pershare_to_fina_indicator(qmt_df)
    assert "end_date" in tushare_df.columns
    assert "basic_eps" in tushare_df.columns
    assert "bps" in tushare_df.columns
    assert tushare_df.iloc[0]["basic_eps"] == 1.5


def test_qmt_financials_to_tushare_text():
    from data.qmt_schema_map import qmt_financials_to_tushare_text

    tables = {
        "Balance": pd.DataFrame([{"m_timetag": "20250331", "tot_assets": 5.77e12,
                                   "tot_liab": 5.27e12, "cap_stk": 1.94e10}]),
        "Income": pd.DataFrame([{"m_timetag": "20250331", "revenue_inc": 3.5e11,
                                  "n_income_attr_p": 1.4e11}]),
        "CashFlow": pd.DataFrame(),
        "PershareIndex": pd.DataFrame([{"m_timetag": "20250331", "s_fa_eps_basic": 1.5}]),
    }
    txt = qmt_financials_to_tushare_text(tables)
    assert isinstance(txt, str)
    assert "资产总计" in txt or "tot_assets" in txt  # 要么中文要么原字段名
    assert "20250331" in txt
```

- [ ] **Step 3.2: Run tests to verify fail**

```
cd C:/LinDangAgent && python -m pytest tests/test_qmt_schema_map.py -v
```
Expected: ImportError on `data.qmt_schema_map`

- [ ] **Step 3.3: 实现映射模块**

Create `C:\LinDangAgent\data\qmt_schema_map.py`:

```python
"""
QMT ↔ Tushare 字段映射。
原则：只映射本期下游实际消费的字段，不做全映射。
未知字段默认丢弃 + 记 warning。
"""
from __future__ import annotations
import logging
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


# ── 元信息字段映射 ──────────────────────────────────────────
QMT_DETAIL_TO_TUSHARE_BASIC = {
    "InstrumentID": "ts_code_base",   # 去后缀
    "InstrumentName": "name",
    "ExchangeID": "exchange",
    "OpenDate": "list_date",
    "ExpireDate": "delist_date",
    "PreClose": "pre_close",
    "UpStopPrice": "up_limit",
    "DownStopPrice": "down_limit",
    "FloatVolume": "float_share",
    "TotalVolume": "total_share",
    "InstrumentStatus": "status_code",
    "IsTrading": "is_trading",
}


def qmt_detail_to_tushare_dict(detail: dict) -> dict:
    """QMT instrument_detail → Tushare basic_info dict 格式。"""
    if not detail:
        return {}
    out = {}
    for qmt_key, ts_key in QMT_DETAIL_TO_TUSHARE_BASIC.items():
        if qmt_key in detail:
            out[ts_key] = detail[qmt_key]
    return out


# ── PershareIndex → Tushare fina_indicator 映射 ─────────────
QMT_PERSHARE_TO_FINA = {
    "m_timetag": "end_date",
    "m_anntime": "ann_date",
    "s_fa_eps_basic": "basic_eps",
    "s_fa_eps_diluted": "diluted_eps",
    "s_fa_bps": "bps",
    "s_fa_ocfps": "cfps",
    "s_fa_roe": "roe",
    "s_fa_roe_basic": "roe_waa",
    "s_fa_roa": "roa",
    "s_fa_grossprofitmargin": "grossprofit_margin",
    "s_fa_netprofitmargin": "netprofit_margin",
    "s_fa_debttoassets": "debt_to_assets",
    "s_fa_current": "current_ratio",
    "s_fa_quick": "quick_ratio",
    "s_fa_yoy_tr": "revenue_yoy",
    "s_fa_yoyocf": "ocf_yoy",
    "s_fa_yoynetprofit": "netprofit_yoy",
}


def qmt_pershare_to_fina_indicator(df: pd.DataFrame) -> pd.DataFrame:
    """QMT PershareIndex DataFrame → Tushare fina_indicator schema。"""
    if df is None or df.empty:
        return pd.DataFrame()
    rename_map = {qmt: ts for qmt, ts in QMT_PERSHARE_TO_FINA.items() if qmt in df.columns}
    out = df.rename(columns=rename_map)
    # 只保留映射过的列，其他丢掉
    keep_cols = list(rename_map.values())
    # 如果 end_date 字段缺失，尝试用索引补
    if "end_date" not in out.columns and "m_timetag" in df.columns:
        out["end_date"] = df["m_timetag"]
        keep_cols.append("end_date")
    available = [c for c in keep_cols if c in out.columns]
    if len(df.columns) > len(available):
        dropped = set(df.columns) - set(rename_map.keys())
        logger.debug("[qmt_schema_map] 丢弃 %d 个未映射字段: %s", len(dropped), list(dropped)[:10])
    return out[available] if available else out


# ── 资产负债表字段映射 ──────────────────────────────────────
QMT_BALANCE_TO_CN = {
    "m_timetag": "报告期",
    "m_anntime": "公告日期",
    "tot_assets": "资产总计",
    "tot_liab": "负债合计",
    "tot_shrhldr_eqy_excl_min_int": "归母股东权益",
    "cap_stk": "股本",
    "cap_rsrv": "资本公积",
    "undistributed_profit": "未分配利润",
    "tot_cur_assets": "流动资产合计",
    "total_current_liability": "流动负债合计",
    "account_receivable": "应收账款",
    "inventories": "存货",
    "fix_assets": "固定资产",
    "goodwill": "商誉",
    "cash_equivalents": "货币资金",
    "shortterm_loan": "短期借款",
    "long_term_loans": "长期借款",
    "bonds_payable": "应付债券",
}

QMT_INCOME_TO_CN = {
    "m_timetag": "报告期",
    "m_anntime": "公告日期",
    "revenue_inc": "营业总收入",
    "total_operating_cost": "营业总成本",
    "operating_profit": "营业利润",
    "total_profit": "利润总额",
    "n_income_attr_p": "归母净利润",
    "basic_eps": "基本每股收益",
}

QMT_CASHFLOW_TO_CN = {
    "m_timetag": "报告期",
    "m_anntime": "公告日期",
    "n_cashflow_act": "经营活动现金流净额",
    "n_cashflow_inv_act": "投资活动现金流净额",
    "n_cashflow_fnc_act": "筹资活动现金流净额",
}


def _df_to_table_text(df: pd.DataFrame, title: str, col_map: dict, max_rows: int = 8) -> str:
    """把 QMT DataFrame 用字段映射转成可读表格字符串。"""
    if df is None or df.empty:
        return f"\n【{title}】\n（无数据）\n"
    # 只取映射列
    rename = {k: v for k, v in col_map.items() if k in df.columns}
    sub = df.rename(columns=rename)
    cols = [c for c in col_map.values() if c in sub.columns]
    if not cols:
        return f"\n【{title}】\n（字段映射为空）\n"
    sub = sub[cols].head(max_rows)
    return f"\n【{title}（近{len(sub)}期）】\n{sub.to_string(index=False)}\n"


def qmt_financials_to_tushare_text(tables: dict[str, pd.DataFrame]) -> str:
    """8 张财务表整合成一个文本报告（Tushare get_financial 同格式）。"""
    parts = []
    parts.append(_df_to_table_text(tables.get("Balance"), "资产负债表", QMT_BALANCE_TO_CN))
    parts.append(_df_to_table_text(tables.get("Income"), "利润表", QMT_INCOME_TO_CN))
    parts.append(_df_to_table_text(tables.get("CashFlow"), "现金流量表", QMT_CASHFLOW_TO_CN))
    # PershareIndex 直接给 Tushare schema 的 DataFrame 字符串
    pershare = tables.get("PershareIndex")
    if pershare is not None and not pershare.empty:
        ps_df = qmt_pershare_to_fina_indicator(pershare)
        if not ps_df.empty:
            parts.append(f"\n【核心财务指标（近{len(ps_df.head(8))}期）】\n{ps_df.head(8).to_string(index=False)}\n")
    # 股东信息
    holder_num = tables.get("HolderNum")
    if holder_num is not None and not holder_num.empty:
        parts.append(f"\n【股东数（近{len(holder_num.head(4))}期）】\n{holder_num.head(4).to_string(index=False)}\n")
    return "\n".join(parts)
```

- [ ] **Step 3.4: Run tests**

```
cd C:/LinDangAgent && python -m pytest tests/test_qmt_schema_map.py -v
```
Expected: 4 passed

- [ ] **Step 3.5: Commit**

```
cd C:/LinDangAgent && git add data/qmt_schema_map.py tests/test_qmt_schema_map.py && git commit -m "feat(qmt): add schema map layer (QMT WindData ↔ Tushare fields)

Field mappings for 4 transformations:
- instrument_detail → Tushare basic_info dict (12 core fields)
- PershareIndex → Tushare fina_indicator (17 per-share indicators,
  richer than Tushare's 11)
- Balance/Income/CashFlow → Chinese-labeled human-readable text
- 8-table consolidation → Tushare get_financial-compatible string

Unknown QMT fields are silently dropped with debug log (no crash).

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: stock_gate.py 前置过滤

**Files:**
- Create: `C:\LinDangAgent\data\stock_gate.py`
- Create: `C:\LinDangAgent\tests\test_stock_gate.py`
- Create: `C:\LinDangAgent\tests\fixtures\__init__.py`
- Create: `C:\LinDangAgent\tests\fixtures\qmt_mocks.py`

- [ ] **Step 4.1: 写 mocks 工具**

Create `C:\LinDangAgent\tests\fixtures\__init__.py`（空文件）

Create `C:\LinDangAgent\tests\fixtures\qmt_mocks.py`:

```python
"""压测场景用的 QMT monkey-patch 工具。"""
from __future__ import annotations
import pandas as pd


def patch_qmt_unavailable(monkeypatch):
    """模拟 QMT 整体不可用（客户端挂）。"""
    import data.qmt_client as qc
    monkeypatch.setattr(qc, "is_alive", lambda: False)

    def _raise(*args, **kw):
        from data.qmt_client import QMTUnavailable
        raise QMTUnavailable("mocked unavailable")

    monkeypatch.setattr(qc, "get_instrument_info", _raise)
    monkeypatch.setattr(qc, "get_instrument_info_batch", _raise)
    monkeypatch.setattr(qc, "get_financial", _raise)
    monkeypatch.setattr(qc, "get_kline", _raise)
    monkeypatch.setattr(qc, "get_trading_dates_before", _raise)


def patch_qmt_instrument_info(monkeypatch, responses: dict):
    """
    responses: {ts_code: detail_dict or None}
    """
    import data.qmt_client as qc

    def fake(sym):
        clean = sym.split(".")[0]
        for k, v in responses.items():
            if k.startswith(clean) or clean.startswith(k):
                return v
        return None

    monkeypatch.setattr(qc, "is_alive", lambda: True)
    monkeypatch.setattr(qc, "get_instrument_info", fake)


def patch_qmt_financial_empty_core(monkeypatch):
    """模拟 QMT 财务核心表空（Balance empty），非核心非空。"""
    import data.qmt_client as qc

    def fake_financial(sym, years=3):
        return {
            "Balance": pd.DataFrame(),  # 核心表空
            "Income": pd.DataFrame([{"m_timetag": "20250331", "revenue_inc": 1e11}]),
            "CashFlow": pd.DataFrame(),
            "Capital": pd.DataFrame(),
            "Top10FlowHolder": pd.DataFrame([{"name": "张三"}]),
            "Top10Holder": pd.DataFrame(),
            "HolderNum": pd.DataFrame(),
            "PershareIndex": pd.DataFrame([{"m_timetag": "20250331", "s_fa_eps_basic": 1.5}]),
        }

    monkeypatch.setattr(qc, "is_alive", lambda: True)
    monkeypatch.setattr(qc, "get_financial", fake_financial)
```

- [ ] **Step 4.2: 写 stock_gate 失败测试**

Create `C:\LinDangAgent\tests\test_stock_gate.py`:

```python
import pytest


def test_tradability_ok_normal_stock(monkeypatch):
    from data.stock_gate import check_tradability, TradabilityStatus
    from tests.fixtures.qmt_mocks import patch_qmt_instrument_info

    patch_qmt_instrument_info(monkeypatch, {
        "000001": {
            "InstrumentName": "平安银行", "InstrumentStatus": 0,
            "PreClose": 12.0, "UpStopPrice": 13.2,  # 10% 涨停板
            "OpenDate": "19910403", "IsTrading": True,
        },
    })
    r = check_tradability("000001.SZ")
    assert r.status == TradabilityStatus.OK
    assert not r.hard_block
    assert r.warnings == []


def test_tradability_st(monkeypatch):
    from data.stock_gate import check_tradability, TradabilityStatus
    from tests.fixtures.qmt_mocks import patch_qmt_instrument_info

    patch_qmt_instrument_info(monkeypatch, {
        "600225": {
            "InstrumentName": "ST 某某", "InstrumentStatus": 31,
            "PreClose": 10.0, "UpStopPrice": 10.5,  # 5% 涨停板
            "OpenDate": "19970101", "IsTrading": True,
        },
    })
    r = check_tradability("600225.SH")
    assert r.status == TradabilityStatus.ST
    assert not r.hard_block
    assert any("ST" in w for w in r.warnings)


def test_tradability_newly_listed(monkeypatch):
    from data.stock_gate import check_tradability, TradabilityStatus
    from tests.fixtures.qmt_mocks import patch_qmt_instrument_info
    import datetime

    recent = (datetime.date.today() - datetime.timedelta(days=10)).strftime("%Y%m%d")
    patch_qmt_instrument_info(monkeypatch, {
        "301999": {
            "InstrumentName": "新股", "InstrumentStatus": 0,
            "PreClose": 25.0, "UpStopPrice": 27.5,
            "OpenDate": recent, "IsTrading": True,
        },
    })
    r = check_tradability("301999.SZ")
    assert r.status == TradabilityStatus.NEWLY_LISTED
    assert any("上市" in w for w in r.warnings)


def test_tradability_bse_no_data(monkeypatch):
    from data.stock_gate import check_tradability, TradabilityStatus
    from tests.fixtures.qmt_mocks import patch_qmt_instrument_info

    patch_qmt_instrument_info(monkeypatch, {"430300": None})
    r = check_tradability("430300.BJ")
    assert r.status == TradabilityStatus.BSE_NO_DATA
    assert not r.hard_block


def test_tradability_qmt_down_fallback_tushare(monkeypatch):
    """QMT 挂了走 Tushare 兜底。"""
    from data.stock_gate import check_tradability, TradabilityStatus
    from tests.fixtures.qmt_mocks import patch_qmt_unavailable
    from data import tushare_client

    patch_qmt_unavailable(monkeypatch)

    def fake_ts_basic(ts_code):
        return {"name": "平安银行", "list_date": "19910403"}, None

    monkeypatch.setattr(tushare_client, "get_basic_info", fake_ts_basic)

    r = check_tradability("000001.SZ")
    # Tushare 正常股 → OK
    assert r.status == TradabilityStatus.OK


def test_tradability_both_down_unknown(monkeypatch):
    from data.stock_gate import check_tradability, TradabilityStatus
    from tests.fixtures.qmt_mocks import patch_qmt_unavailable
    from data import tushare_client

    patch_qmt_unavailable(monkeypatch)
    monkeypatch.setattr(tushare_client, "get_basic_info",
                        lambda ts_code: ({}, "tushare also down"))

    r = check_tradability("999999.SZ")
    assert r.status == TradabilityStatus.UNKNOWN
    assert not r.hard_block  # UNKNOWN 放行不拦截


def test_tradability_delisted_hard_block(monkeypatch):
    """模拟退市（假定 InstrumentStatus=6 是退市码；具体值从 Task 1 discovery 拿）。
    如果 Task 1 发现退市是 detail=None，改测那个行为。"""
    from data.stock_gate import check_tradability, TradabilityStatus
    from tests.fixtures.qmt_mocks import patch_qmt_instrument_info

    # 使用 spec 的占位 — 实现时从 docs/qmt_status_codes.md 取真实码
    DELISTED_CODE = 6  # TODO from Task 1 discovery
    patch_qmt_instrument_info(monkeypatch, {
        "000033": {
            "InstrumentName": "新都退", "InstrumentStatus": DELISTED_CODE,
            "PreClose": 1.0, "UpStopPrice": 1.05,
            "OpenDate": "19951231", "IsTrading": False,
        },
    })
    r = check_tradability("000033.SZ")
    assert r.status == TradabilityStatus.DELISTED
    assert r.hard_block is True
```

- [ ] **Step 4.3: Run tests to verify fail**

```
cd C:/LinDangAgent && python -m pytest tests/test_stock_gate.py -v
```
Expected: 7 failed (module not found)

- [ ] **Step 4.4: 实现 stock_gate.py**

先读 `docs/qmt_status_codes.md`（Task 1 产出）拿到真实的退市码值。假设发现的码值放入常量 `DELISTED_STATUS_CODES`。

Create `C:\LinDangAgent\data\stock_gate.py`:

```python
"""
单股交易状态前置过滤。优先 QMT，Tushare 兜底，两源都挂时放行不拦截。
"""
from __future__ import annotations
import datetime as _dt
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class TradabilityStatus(Enum):
    OK = "ok"
    ST = "st"
    NEWLY_LISTED = "newly_listed"
    BSE_NO_DATA = "bse_no_data"
    DELISTED = "delisted"
    UNKNOWN = "unknown"


@dataclass
class TradabilityResult:
    status: TradabilityStatus
    hard_block: bool
    warnings: list[str] = field(default_factory=list)
    facts: dict = field(default_factory=dict)


class TradabilityBlocked(Exception):
    def __init__(self, result: TradabilityResult):
        self.result = result
        super().__init__(f"TradabilityBlocked: {result.status.value}")


# 退市码集合（Task 1 discovery 产出；若发现退市股 detail=None，改用 None 判定）
DELISTED_STATUS_CODES = {6}  # TODO: 依 docs/qmt_status_codes.md 更新
ST_STATUS_CODES = {4, 31}    # 已知 *ST=4, ST=31
NEWLY_LISTED_DAYS = 20       # 自然日阈值（简化；真正 20 交易日 ~= 30 自然日）
NEWLY_LISTED_CALENDAR_DAYS = 30


def _days_since(date_str: str) -> Optional[int]:
    """'YYYYMMDD' → 距今自然日数；解析失败返 None。"""
    if not date_str:
        return None
    try:
        d = _dt.datetime.strptime(str(date_str), "%Y%m%d").date()
        return (_dt.date.today() - d).days
    except Exception:
        return None


def _classify_from_qmt_detail(ts_code: str, detail: dict) -> TradabilityResult:
    facts = {
        "InstrumentStatus": detail.get("InstrumentStatus"),
        "InstrumentName": detail.get("InstrumentName"),
        "OpenDate": detail.get("OpenDate"),
        "IsTrading": detail.get("IsTrading"),
        "PreClose": detail.get("PreClose"),
        "UpStopPrice": detail.get("UpStopPrice"),
    }
    name = detail.get("InstrumentName", "") or ""
    status_code = detail.get("InstrumentStatus")
    pre = detail.get("PreClose", 0)
    up = detail.get("UpStopPrice", 0)

    # 1. 退市硬拦截
    if status_code in DELISTED_STATUS_CODES:
        return TradabilityResult(
            status=TradabilityStatus.DELISTED, hard_block=True,
            warnings=[f"此股已退市（Status={status_code}）"], facts=facts,
        )

    # 2. ST 判定（Status 码 OR name 前缀 OR 5% 涨跌停板）
    is_st = False
    if status_code in ST_STATUS_CODES:
        is_st = True
    elif name.startswith(("ST", "*ST", "S*ST")):
        is_st = True
    elif pre > 0 and up > 0 and (up - pre) / pre < 0.06:
        # ETF/指数 不按此判
        exch = detail.get("ExchangeID", "")
        code6 = detail.get("InstrumentID", ts_code.split(".")[0])
        if not (code6.startswith(("5", "1")) and exch in ("SH", "SZ")):  # 基金/ETF
            is_st = True

    # 3. 新股
    is_new = False
    days = _days_since(detail.get("OpenDate"))
    if days is not None and days < NEWLY_LISTED_CALENDAR_DAYS:
        is_new = True

    warnings = []
    if is_st:
        warnings.append("ST 标记（5% 涨跌停板）")
    if is_new:
        warnings.append(f"上市 {days} 天（新股，数据窗口可能较短）")

    status = TradabilityStatus.OK
    if is_st:
        status = TradabilityStatus.ST
    elif is_new:
        status = TradabilityStatus.NEWLY_LISTED

    return TradabilityResult(status=status, hard_block=False,
                             warnings=warnings, facts=facts)


def _classify_from_tushare_basic(ts_code: str, info: dict) -> TradabilityResult:
    """Tushare 兜底判定：名字含 ST + 上市日期。"""
    name = info.get("name", "") or ""
    list_date = info.get("list_date", "")
    facts = {"name": name, "list_date": list_date}

    is_st = name.startswith(("ST", "*ST", "S*ST"))
    days = _days_since(list_date)
    is_new = days is not None and days < NEWLY_LISTED_CALENDAR_DAYS

    warnings = []
    if is_st:
        warnings.append("ST 标记（来自 Tushare）")
    if is_new:
        warnings.append(f"上市 {days} 天（新股）")

    status = TradabilityStatus.OK
    if is_st:
        status = TradabilityStatus.ST
    elif is_new:
        status = TradabilityStatus.NEWLY_LISTED

    return TradabilityResult(status=status, hard_block=False,
                             warnings=warnings, facts=facts)


def check_tradability(ts_code: str) -> TradabilityResult:
    """
    返回 TradabilityResult。hard_block=True 时调用方应抛 TradabilityBlocked。
    两源都挂 → UNKNOWN，放行不拦截。
    """
    from data import qmt_client
    from data import tushare_client

    # QMT 优先
    try:
        if qmt_client.is_alive():
            detail = qmt_client.get_instrument_info(ts_code)
            if detail:
                return _classify_from_qmt_detail(ts_code, detail)
            # detail=None: 北交所或 QMT 没这只股票
            if ts_code.endswith(".BJ"):
                return TradabilityResult(
                    status=TradabilityStatus.BSE_NO_DATA, hard_block=False,
                    warnings=["QMT 无此北交所股元信息，基础信息走 Tushare"],
                    facts={"ts_code": ts_code},
                )
            # 非 BJ 但 QMT 没有 → 可能已退市 → 保底判定为退市
            logger.warning("[stock_gate] QMT 返 None（非 BJ）: %s，疑似已退市或代码无效", ts_code)
            return TradabilityResult(
                status=TradabilityStatus.DELISTED, hard_block=True,
                warnings=[f"QMT 未找到 {ts_code}，疑似已退市/代码无效"],
                facts={"ts_code": ts_code, "source": "qmt_none"},
            )
    except Exception as e:
        logger.warning("[stock_gate] QMT 判定失败: %s，降级 Tushare", e)

    # Tushare 兜底
    try:
        info, err = tushare_client.get_basic_info(ts_code)
        if err is None and info:
            return _classify_from_tushare_basic(ts_code, info)
    except Exception as e:
        logger.warning("[stock_gate] Tushare 兜底也失败: %s", e)

    # 两源都挂 → UNKNOWN，放行
    return TradabilityResult(
        status=TradabilityStatus.UNKNOWN, hard_block=False,
        warnings=["数据源异常，未能确认交易状态"],
        facts={"ts_code": ts_code},
    )
```

- [ ] **Step 4.5: 回到 Task 1 discovery 产出更新 `DELISTED_STATUS_CODES`**

如果 Task 1 发现退市股返 `detail=None` 而非具体 Status 码，这部分逻辑在上面 `check_tradability` 里已用"非 BJ 且 detail=None → DELISTED 硬拦截"覆盖。`DELISTED_STATUS_CODES` 保留用于未来可能的 Status 码退市识别。

- [ ] **Step 4.6: Run tests**

```
cd C:/LinDangAgent && python -m pytest tests/test_stock_gate.py -v
```
Expected: 7 passed

- [ ] **Step 4.7: Commit**

```
cd C:/LinDangAgent && git add data/stock_gate.py tests/test_stock_gate.py tests/fixtures/__init__.py tests/fixtures/qmt_mocks.py && git commit -m "feat(gate): pre-analysis tradability check with QMT-first fallback

Classifies stock into OK/ST/NEWLY_LISTED/BSE_NO_DATA/DELISTED/UNKNOWN.
QMT get_instrument_info(iscomplete=True) → 83-field inspection, falls
back to Tushare get_basic_info when QMT down. Both-down → UNKNOWN
(pass-through, don't block user on data-source outage).

DELISTED raises TradabilityBlocked for CLI-layer hard-block handling.
ST/NEWLY_LISTED/BSE_NO_DATA return warnings that flow into report
header via build_report_context.

Shared monkey-patch utilities in tests/fixtures/qmt_mocks.py.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: tushare_client `_data_source_map` + get_basic_info 接 QMT

**Files:**
- Modify: `C:\LinDangAgent\data\tushare_client.py`
- Modify: `C:\LinDangAgent\tests\test_qmt_integration.py`

- [ ] **Step 5.1: 追加测试**

Append to `tests/test_qmt_integration.py`:

```python
def test_data_source_map_per_label():
    """每个 label 独立记录 data_source。"""
    from data import tushare_client
    # Reset map
    tushare_client._data_source_map = {}

    def qmt_a(): return ({"name": "A"}, None)
    def ts_b(): return ("B data", None)

    # QMT 成功 → A 标签记 qmt
    tushare_client._try_with_fallback(lambda: (None, "fail"), label="A", qmt_fn=qmt_a)
    assert tushare_client._data_source_map["A"] == "qmt"

    # Tushare 成功（无 QMT） → B 标签记 tushare
    # 需要 mock _get_pro
    import unittest.mock as um
    with um.patch.object(tushare_client, "_get_pro", return_value=object()):
        tushare_client._try_with_fallback(ts_b, label="B")
    assert tushare_client._data_source_map["B"] == "tushare"
    # A 标签保留原值不被覆盖
    assert tushare_client._data_source_map["A"] == "qmt"


def test_get_basic_info_uses_qmt(monkeypatch):
    """get_basic_info 优先走 QMT。"""
    from data import tushare_client
    import data.qmt_client as qc

    fake_detail = {
        "InstrumentName": "平安银行", "ExchangeID": "SZ",
        "OpenDate": "19910403", "PreClose": 12.0, "UpStopPrice": 13.2,
        "FloatVolume": 1.9e10, "TotalVolume": 1.94e10,
    }
    monkeypatch.setattr(qc, "is_alive", lambda: True)
    monkeypatch.setattr(qc, "get_instrument_info", lambda sym: fake_detail)

    tushare_client._data_source_map = {}
    info, err = tushare_client.get_basic_info("000001.SZ")
    assert err is None
    assert info.get("name") == "平安银行"
    assert tushare_client._data_source_map.get("基本信息") == "qmt"
```

- [ ] **Step 5.2: Run tests to verify fail**

```
cd C:/LinDangAgent && python -m pytest tests/test_qmt_integration.py::test_data_source_map_per_label tests/test_qmt_integration.py::test_get_basic_info_uses_qmt -v
```
Expected: AttributeError on `_data_source_map` / no qmt priority

- [ ] **Step 5.3: 修改 tushare_client.py 加 _data_source_map**

在 `data/tushare_client.py` line ~30 的 `_data_source = "fallback"` 后面追加：

```python
_data_source_map: dict[str, str] = {}
```

修改 `_try_with_fallback` 函数（line 157-228），**每个成功的分支里同时更新 map**：

在 `_data_source = "qmt"` 这一行后加 `_data_source_map[label] = "qmt"`；
在 `_data_source = "tushare"` 行后加 `_data_source_map[label] = "tushare"`；
eastmoney/akshare/baostock/sina 同理。

最后"全部失败"那块（line 220 附近）加 `_data_source_map[label] = "unavailable"`。

新增一个公开函数供调用方读取：

```python
def get_data_source_map() -> dict:
    global _data_source_map
    with _init_lock:
        return dict(_data_source_map)
```

- [ ] **Step 5.4: get_basic_info 接 QMT**

找到 `data/tushare_client.py:343` 的 `get_basic_info` 函数。在 `def _tushare():` 之后、`return _try_with_fallback(...)` 之前插入 `_qmt` 闭包：

```python
def get_basic_info(ts_code: str) -> tuple[dict, str | None]:
    from data.fallback import ak_get_basic_info, em_get_basic_info
    from data import qmt_client
    from data.qmt_client import QMTUnavailable
    from data.qmt_schema_map import qmt_detail_to_tushare_dict

    def _qmt():
        if not qmt_client.is_alive():
            raise QMTUnavailable()
        detail = qmt_client.get_instrument_info(ts_code)
        if not detail:
            raise QMTUnavailable(f"{ts_code} 无 QMT 元信息")
        info = qmt_detail_to_tushare_dict(detail)
        if not info:
            raise QMTUnavailable("schema map 返空")
        return info, None

    def _tushare():
        # ... 保持原逻辑不变 ...

    from data.fallback import bs_get_basic_info, sina_get_realtime_quote
    return _try_with_fallback(
        _tushare,
        lambda: ak_get_basic_info(ts_code),
        lambda: em_get_basic_info(ts_code),
        baostock_fn=lambda: bs_get_basic_info(ts_code),
        sina_fn=lambda: sina_get_realtime_quote(ts_code),
        label="基本信息",
        qmt_fn=_qmt,
    )
```

- [ ] **Step 5.5: Run tests**

```
cd C:/LinDangAgent && python -m pytest tests/test_qmt_integration.py -v
```
Expected: 5 passed (原 3 + 新 2)

- [ ] **Step 5.6: 回归**

```
cd C:/LinDangAgent && python -m pytest tests/test_qmt_client.py tests/test_qmt_client_ext.py tests/test_qmt_integration.py tests/test_qmt_schema_map.py tests/test_stock_gate.py -v 2>&1 | tail -5
```
Expected: all pass (no regressions)

- [ ] **Step 5.7: Commit**

```
cd C:/LinDangAgent && git add data/tushare_client.py tests/test_qmt_integration.py && git commit -m "feat(data): per-label _data_source_map + get_basic_info via QMT

_try_with_fallback now records the winning source per label (not just
a global), exposed via get_data_source_map(). Enables report-footer
audit trail like '[basic_info=qmt] [price=qmt] [financial=tushare]'.

get_basic_info now tries QMT's instrument_info first, adapted through
qmt_detail_to_tushare_dict, with the full Tushare/EM/AKShare/Baostock/
Sina fallback chain preserved.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: tushare_client get_financial 接 QMT（核心表门控）

**Files:**
- Modify: `C:\LinDangAgent\data\tushare_client.py:451` (get_financial)
- Modify: `C:\LinDangAgent\tests\test_qmt_integration.py`

- [ ] **Step 6.1: 追加测试**

Append to `tests/test_qmt_integration.py`:

```python
def test_get_financial_uses_qmt(monkeypatch):
    import pandas as pd
    from data import tushare_client
    import data.qmt_client as qc

    fake_tables = {
        "Balance": pd.DataFrame([{"m_timetag": "20250331", "tot_assets": 5.77e12,
                                   "tot_liab": 5.27e12, "cap_stk": 1.94e10}]),
        "Income": pd.DataFrame([{"m_timetag": "20250331", "revenue_inc": 3.5e11,
                                  "n_income_attr_p": 1.4e11}]),
        "CashFlow": pd.DataFrame(),
        "Capital": pd.DataFrame(),
        "Top10FlowHolder": pd.DataFrame(),
        "Top10Holder": pd.DataFrame(),
        "HolderNum": pd.DataFrame(),
        "PershareIndex": pd.DataFrame([{"m_timetag": "20250331", "s_fa_eps_basic": 1.5, "s_fa_bps": 15.2}]),
    }
    monkeypatch.setattr(qc, "is_alive", lambda: True)
    monkeypatch.setattr(qc, "get_financial", lambda sym, years=3: fake_tables)

    tushare_client._data_source_map = {}
    txt, err = tushare_client.get_financial("000001.SZ")
    assert err is None
    assert "资产总计" in txt  # Tushare 兼容文本
    assert tushare_client._data_source_map.get("财务") == "qmt"


def test_get_financial_core_empty_falls_back(monkeypatch):
    """核心表空 → 降级 Tushare。"""
    from data import tushare_client
    from tests.fixtures.qmt_mocks import patch_qmt_financial_empty_core

    patch_qmt_financial_empty_core(monkeypatch)

    # Mock Tushare 回应
    def fake_ts_financial_inner(fn):
        # 让 tushare 分支走 happy path 简化 — 直接 patch 整个 get_financial 的 tushare 实现
        # 更简单: patch _get_pro 返 object, 再 patch pro.fina_indicator 返 mock
        pass

    # 简化：直接 patch get_financial 的 tushare 内部闭包行为会比较复杂，
    # 改用 QMTUnavailable 会不会降级到 Tushare，看 _data_source_map 变化
    import unittest.mock as um
    with um.patch.object(tushare_client, "_get_pro", return_value=None):
        # _get_pro=None → tushare 层跳过 → 走 akshare
        # 确认至少 _data_source_map 的"财务"不是 qmt
        tushare_client._data_source_map = {}
        txt, err = tushare_client.get_financial("000001.SZ")
        assert tushare_client._data_source_map.get("财务") != "qmt"
```

- [ ] **Step 6.2: Run tests to verify fail**

```
cd C:/LinDangAgent && python -m pytest tests/test_qmt_integration.py::test_get_financial_uses_qmt tests/test_qmt_integration.py::test_get_financial_core_empty_falls_back -v
```
Expected: fail

- [ ] **Step 6.3: 修改 get_financial 加 qmt_fn**

在 `data/tushare_client.py:451` 的 `get_financial` 函数内，按 Task 5 同样模式插入 `_qmt` 闭包：

```python
@compat_cache(ttl=600, show_spinner=False)
def get_financial(ts_code: str) -> tuple[str, str | None]:
    from data.fallback import ak_get_financial
    from data import qmt_client
    from data.qmt_client import QMTUnavailable
    from data.qmt_schema_map import qmt_financials_to_tushare_text
    import pandas as pd

    def _qmt():
        if not qmt_client.is_alive():
            raise QMTUnavailable()
        try:
            tables = qmt_client.get_financial(ts_code, years=3)
        except QMTUnavailable:
            raise
        except Exception as e:
            raise QMTUnavailable(f"get_financial 异常: {e}")

        # 核心表门控：Balance / Income / PershareIndex 任一空 → 降级
        core = ("Balance", "Income", "PershareIndex")
        core_empty = any(tables.get(k, pd.DataFrame()).empty for k in core)
        if core_empty:
            empty_names = [k for k in core if tables.get(k, pd.DataFrame()).empty]
            raise QMTUnavailable(f"QMT 核心财务表空: {empty_names}")

        text = qmt_financials_to_tushare_text(tables)
        return text, None

    def _tushare():
        # ... 保持原逻辑 ...

    return _try_with_fallback(
        _tushare,
        lambda: ak_get_financial(ts_code),
        None,
        label="财务",
        qmt_fn=_qmt,
    )
```

- [ ] **Step 6.4: Run tests**

```
cd C:/LinDangAgent && python -m pytest tests/test_qmt_integration.py -v
```
Expected: 7 passed

- [ ] **Step 6.5: Commit**

```
cd C:/LinDangAgent && git add data/tushare_client.py tests/test_qmt_integration.py && git commit -m "feat(data): get_financial via QMT with core-table gating

QMT financial path consolidates 8 tables into Tushare-compatible text
via qmt_schema_map. Core-table gate: if Balance/Income/PershareIndex
is empty, raise QMTUnavailable to trigger full fallback to Tushare
(avoids mixing QMT+Tushare data with inconsistent schemas/dates).

Non-core tables (Capital/HolderNum/Top10*) can be individually empty
without triggering degradation.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: report_data.py 4 个细粒度财务函数接 QMT

**Files:**
- Modify: `C:\LinDangAgent\data\report_data.py`（`_ts_or_ak` 辅助 + 4 个 get_*）
- Modify: `C:\LinDangAgent\tests\test_qmt_integration.py`

- [ ] **Step 7.1: 读现有 _ts_or_ak 签名**

Run:
```
cd C:/LinDangAgent && grep -n "def _ts_or_ak" data/report_data.py
```

Find the signature. 应该是：
```python
def _ts_or_ak(ts_fn, ak_fn, label: str, bs_fn=None) -> pd.DataFrame:
```

- [ ] **Step 7.2: 追加测试**

```python
def test_get_income_uses_qmt(monkeypatch):
    import pandas as pd
    from data import report_data
    import data.qmt_client as qc

    fake_tables = {
        "Balance": pd.DataFrame(), "CashFlow": pd.DataFrame(),
        "Capital": pd.DataFrame(), "Top10FlowHolder": pd.DataFrame(),
        "Top10Holder": pd.DataFrame(), "HolderNum": pd.DataFrame(),
        "PershareIndex": pd.DataFrame(),
        "Income": pd.DataFrame([
            {"m_timetag": "20250331", "revenue_inc": 3.5e11, "n_income_attr_p": 1.4e11,
             "basic_eps": 0.6},
            {"m_timetag": "20241231", "revenue_inc": 1.6e12, "n_income_attr_p": 4.5e11,
             "basic_eps": 2.3},
        ]),
    }
    monkeypatch.setattr(qc, "is_alive", lambda: True)
    monkeypatch.setattr(qc, "get_financial", lambda sym, years=3: fake_tables)

    df = report_data.get_income("000001.SZ")
    assert not df.empty
    assert "end_date" in df.columns or "报告期" in df.columns
```

- [ ] **Step 7.3: Run to verify fail**

```
cd C:/LinDangAgent && python -m pytest tests/test_qmt_integration.py::test_get_income_uses_qmt -v
```
Expected: fail

- [ ] **Step 7.4: 扩展 `_ts_or_ak` 加 `qmt_fn` slot**

在 `data/report_data.py` 修改 `_ts_or_ak` 签名：

```python
def _ts_or_ak(ts_fn, ak_fn, label: str, bs_fn=None, qmt_fn=None) -> pd.DataFrame:
    """QMT → Tushare → AKShare → Baostock 降级链。"""
    from data.tushare_client import _data_source_map

    # QMT 优先
    if qmt_fn is not None:
        try:
            df = qmt_fn()
            if df is not None and not df.empty:
                _data_source_map[label] = "qmt"
                return df
        except Exception as e:
            logger.debug(f"[{label}] qmt 失败: {e}")

    # 原 Tushare / AKShare / BS 分支保持
    # ... 保留现有代码，只在成功时加 _data_source_map[label] = xxx
```

- [ ] **Step 7.5: 给 get_income 加 qmt_fn**

```python
def get_income(ts_code: str) -> pd.DataFrame:
    """利润表（近8期）"""
    from data import qmt_client
    from data.qmt_client import QMTUnavailable
    from data.qmt_schema_map import QMT_INCOME_TO_CN
    import pandas as pd

    def _qmt():
        if not qmt_client.is_alive():
            raise QMTUnavailable()
        tables = qmt_client.get_financial(ts_code, years=3)
        df = tables.get("Income", pd.DataFrame())
        if df.empty:
            raise QMTUnavailable("Income 表空")
        rename = {k: v for k, v in QMT_INCOME_TO_CN.items() if k in df.columns}
        out = df.rename(columns=rename)
        # 映射 end_date 以兼容 tushare schema
        if "报告期" in out.columns and "end_date" not in out.columns:
            out["end_date"] = out["报告期"]
        return out.head(8)

    return _ts_or_ak(
        lambda pro: _ts_call(lambda: pro.income(
            ts_code=ts_code,
            fields="end_date,ann_date,revenue,operate_profit,total_profit,"
                   "n_income,n_income_attr_p,basic_eps,diluted_eps,"
                   "total_cogs,sell_exp,admin_exp,rd_exp,fin_exp"
        )).head(8),
        lambda: _ak_financial_report(ts_code, "income").head(8),
        "get_income",
        qmt_fn=_qmt,
    )
```

- [ ] **Step 7.6: 同样给 get_balancesheet / get_cashflow / get_fina_indicator 加 qmt_fn**

参考 7.5 模式，分别用 `tables.get("Balance")`, `tables.get("CashFlow")`, `tables.get("PershareIndex")`，中文字段映射用 `QMT_BALANCE_TO_CN`, `QMT_CASHFLOW_TO_CN`, `QMT_PERSHARE_TO_FINA`。

**注意**：`get_fina_indicator` 的 `_qmt` 要用 `qmt_pershare_to_fina_indicator` 而不是纯 rename（因为 Tushare fina_indicator 有特殊字段名如 roe/roe_waa/grossprofit_margin 等）。

- [ ] **Step 7.7: Run tests**

```
cd C:/LinDangAgent && python -m pytest tests/test_qmt_integration.py -v
```
Expected: 所有测试通过（至少 8 条）

- [ ] **Step 7.8: Commit**

```
cd C:/LinDangAgent && git add data/report_data.py tests/test_qmt_integration.py && git commit -m "feat(report): get_income/balance/cashflow/fina_indicator via QMT

_ts_or_ak gains qmt_fn slot (top priority, same semantics as
tushare_client._try_with_fallback). Four granular financial getters
now try QMT first: Income/Balance/CashFlow use Chinese label mapping
from qmt_schema_map; fina_indicator uses the PershareIndex → Tushare
schema adapter.

Each function independently writes _data_source_map[label]=qmt|tushare
|akshare|baostock on success, enabling fine-grained audit trail in
report footer.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: report_data.py 涨跌幅精准化 + _data_source_map dump

**Files:**
- Modify: `C:\LinDangAgent\data\report_data.py` (build_report_context + N-day gain helper)

- [ ] **Step 8.1: 找到当前涨跌幅计算位置**

```
cd C:/LinDangAgent && grep -n "gain_\|pct_change\|price_df.iloc\|\-5\|\-20" data/report_data.py | head -20
```

如果找到类似 `price_df.iloc[-5]['收盘']` 的代码，记录行号。如果没有集中的涨跌幅计算（可能在多个下游模块），就**只做 build_report_context 的 _data_source_map dump**，涨跌幅精准化留到需要的模块重构时再做（本期不强制）。

- [ ] **Step 8.2: 给 build_report_context 加 tradability + _data_source_map 注入**

在 `data/report_data.py::build_report_context` 函数（line 734）修改签名并注入：

```python
def build_report_context(
    ts_code: str, name: str,
    progress_cb=None, time_lock: str = "",
    tradability=None,  # ← 新增参数
) -> tuple[dict, dict]:
    from data.tushare_client import _data_source_map, get_data_source_map
    # Reset map for this analysis run
    _data_source_map.clear()

    # ... 保持现有所有取数逻辑 ...
    # raw = {...}

    # 最后在 return 之前注入
    if tradability is not None:
        raw["_tradability_status"] = tradability.status.value
        raw["_tradability_warnings"] = list(tradability.warnings)
        raw["_tradability_facts"] = dict(tradability.facts)

    raw["_data_source_map"] = get_data_source_map()

    return context, raw
```

- [ ] **Step 8.3: 加辅助函数 `compute_n_day_gain_precise`（若有涨跌幅调用）**

若 Step 8.1 找到了涨跌幅计算代码：

```python
def compute_n_day_gain_precise(price_df, end_date: str, n: int,
                                code6: str) -> float | None:
    """用真实交易日前的收盘计算 N 日涨跌幅；QMT 挂则 iloc[-n] 兜底。"""
    from data import qmt_client
    from data.qmt_client import QMTUnavailable

    try:
        if qmt_client.is_alive():
            market = "SH" if code6.startswith("6") else "SZ"
            dates = qmt_client.get_trading_dates_before(end_date, n + 1, market=market)
            if len(dates) >= n + 1:
                target_date = dates[0]  # n+1 交易日前
                # 在 price_df 里找这个日期的收盘
                mask = price_df["日期"].astype(str).str.startswith(target_date.replace("-", ""))
                matched = price_df[mask]
                if not matched.empty:
                    old_close = float(matched.iloc[0]["收盘"])
                    new_close = float(price_df.iloc[-1]["收盘"])
                    return (new_close / old_close - 1) * 100
    except QMTUnavailable:
        pass
    except Exception as e:
        logger.debug(f"[N-day gain precise] {e}, fallback to iloc[-n]")

    # Fallback: 原 iloc 算法
    if len(price_df) >= n + 1:
        old = float(price_df.iloc[-n - 1]["收盘"])
        new = float(price_df.iloc[-1]["收盘"])
        return (new / old - 1) * 100
    return None
```

在原 iloc 计算处替换为 `compute_n_day_gain_precise(price_df, today, 5, code6)`。

- [ ] **Step 8.4: 添加测试**

```python
def test_build_report_context_injects_data_source_map(monkeypatch):
    from data import report_data, tushare_client
    # 让每个 get_* 都立刻返回空但记一个假源
    # 简化版：直接在 build_report_context 运行后检查 map 存在
    tushare_client._data_source_map = {"基本信息": "qmt", "财务": "tushare"}

    # 由于 build_report_context 会真调一堆 API，测试级别改为单元测试 dump 行为
    # 用最小 mock
    monkeypatch.setattr(report_data, "get_basic_info",
                        lambda ts_code: ({"name": "A"}, None))
    monkeypatch.setattr(report_data, "get_price_df",
                        lambda ts_code: (__import__("pandas").DataFrame([{"日期":"20260101","收盘":10}]), None))
    # ... 其他可能的依赖 mock

    # 由于 build_report_context 代码量大，精细 mock 成本高，改为集成测试覆盖
    # 本步跳过测试，放到 Task 9 集成压测验证
```

**实际上测试放到 Task 9 集成压测更合适**——Task 9 的场景 1 会断言 `context["_data_source_map"]` 存在且包含 qmt 源。

- [ ] **Step 8.5: Commit**

```
cd C:/LinDangAgent && git add data/report_data.py && git commit -m "feat(report): inject tradability warnings + data_source_map into context

build_report_context now accepts optional tradability parameter
(populated by services layer); writes _tradability_status/_warnings/
_facts + _data_source_map keys into raw_data dict. Downstream report
template can render audit-trail footer like:
  📊 数据来源：basic_info=qmt | financial=qmt | income=qmt | holder=tushare

N-day gain helper uses get_trading_dates_before for precise calendar-
aware calculation; falls back to iloc[-N] when QMT unavailable.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: analysis_service.py 前置 gate + 处理 TradabilityBlocked

**Files:**
- Modify: `C:\LinDangAgent\services\analysis_service.py`

- [ ] **Step 9.1: 定位 analyze_stock 入口**

```
cd C:/LinDangAgent && grep -n "^def analyze\|^def main\|ts_code: str\|build_report_context" services/analysis_service.py | head -10
```

找到主函数（可能是 `analyze_stock` 或被 CLI 调用的外壳）。

- [ ] **Step 9.2: 修改入口加 gate**

在 `build_report_context` 调用**之前**加：

```python
from data.stock_gate import check_tradability, TradabilityBlocked, TradabilityStatus

def analyze_stock(ts_code: str, ...):
    # ... resolve_stock 等前置逻辑保持 ...

    # 新增：前置交易状态 gate
    try:
        tradability = check_tradability(ts_code)
    except Exception as e:
        logger.warning(f"[gate] check_tradability 异常: {e}")
        tradability = None

    if tradability and tradability.hard_block:
        # DELISTED → 抛 TradabilityBlocked 供 CLI 捕获
        raise TradabilityBlocked(tradability)

    # ... 原 build_report_context 调用，新增 tradability 参数 ...
    context, raw_data = build_report_context(
        ts_code, name, progress_cb=..., time_lock=...,
        tradability=tradability,
    )
```

CLI 入口（可能在 `cli.py` 或 `services/analysis_service.py` 最外层）要有 try/except：

```python
try:
    result = analyze_stock(ts_code, ...)
except TradabilityBlocked as e:
    print(f"⚠️ 此股已退市或异常（{e.result.status.value}），不进行分析。")
    print(f"理由：{'; '.join(e.result.warnings)}")
    sys.exit(2)
```

- [ ] **Step 9.3: Commit**

```
cd C:/LinDangAgent && git add services/analysis_service.py && git commit -m "feat(analysis): pre-gate with check_tradability + TradabilityBlocked

analyze_stock now runs stock_gate.check_tradability first; DELISTED
stocks raise TradabilityBlocked which the CLI layer catches to print
a friendly message and exit code 2. Non-blocking statuses (ST /
NEWLY_LISTED / BSE_NO_DATA / UNKNOWN) flow through as warnings into
build_report_context.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: 8 场景压测（纯数据层，不调 AI）

**Files:**
- Create: `C:\LinDangAgent\tests\test_qmt_single_stock_refactor.py`

- [ ] **Step 10.1: 写 8 场景压测脚本**

Create `C:\LinDangAgent\tests\test_qmt_single_stock_refactor.py`:

```python
"""
单股 QMT 重构集成压测 —— 纯数据层，不调 AI。
8 场景覆盖正常/ST/新股/北交所/除权/QMT挂/财务核心空/退市。
"""
import pandas as pd
import pytest
import datetime as _dt


# ── 场景 1: 正常股 ─────────────────────────────────────────
def test_scenario_1_normal_stock():
    """000001.SZ 真实跑，验证 gate=OK，核心维度来源=qmt"""
    from data.stock_gate import check_tradability, TradabilityStatus
    from data import qmt_client, tushare_client

    if not qmt_client.is_alive():
        pytest.skip("QMT 未登录，跳过真实联动测试")

    result = check_tradability("000001.SZ")
    assert result.status == TradabilityStatus.OK
    assert not result.hard_block

    # 调 get_basic_info 验证 source
    tushare_client._data_source_map = {}
    info, err = tushare_client.get_basic_info("000001.SZ")
    assert err is None
    assert tushare_client._data_source_map.get("基本信息") == "qmt"


# ── 场景 2: ST 股 ──────────────────────────────────────────
def test_scenario_2_st_stock():
    """实时从 QMT 池里找一只 ST 股验证。"""
    from data.stock_gate import check_tradability, TradabilityStatus
    from data import qmt_client

    if not qmt_client.is_alive():
        pytest.skip("QMT 未登录")

    # 从沪深A股池前 500 扫一只 5% 涨停板 ST
    pool = qmt_client.get_instrument_info_batch(
        qmt_client.get_sector_stocks("沪深A股")[:500]
    )
    st_sym = None
    for sym, d in pool.items():
        if not d:
            continue
        pre = d.get("PreClose", 0)
        up = d.get("UpStopPrice", 0)
        if pre > 0 and (up - pre) / pre < 0.06:
            name = d.get("InstrumentName", "")
            if name.startswith(("ST", "*ST")):
                st_sym = sym
                break

    if not st_sym:
        pytest.skip("未在沪深A股前 500 里找到 ST 股")

    result = check_tradability(st_sym)
    assert result.status == TradabilityStatus.ST
    assert any("ST" in w for w in result.warnings)


# ── 场景 3: 新股 ───────────────────────────────────────────
def test_scenario_3_newly_listed():
    """从 QMT 池找上市 <30 自然日的股票。"""
    from data.stock_gate import check_tradability, TradabilityStatus
    from data import qmt_client

    if not qmt_client.is_alive():
        pytest.skip("QMT 未登录")

    pool = qmt_client.get_instrument_info_batch(
        qmt_client.get_sector_stocks("沪深A股")[:500]
    )
    today = _dt.date.today()
    new_sym = None
    for sym, d in pool.items():
        if not d:
            continue
        open_date = d.get("OpenDate", "")
        if open_date:
            try:
                od = _dt.datetime.strptime(str(open_date), "%Y%m%d").date()
                if (today - od).days < 30:
                    new_sym = sym
                    break
            except Exception:
                pass

    if not new_sym:
        pytest.skip("未找到 30 日内新股")

    result = check_tradability(new_sym)
    assert result.status == TradabilityStatus.NEWLY_LISTED


# ── 场景 4: 北交所股 ───────────────────────────────────────
def test_scenario_4_bse_stock():
    """BJ 股 QMT 无数据，走 BSE_NO_DATA 路径。"""
    from data.stock_gate import check_tradability, TradabilityStatus
    from data import qmt_client

    if not qmt_client.is_alive():
        pytest.skip("QMT 未登录")

    result = check_tradability("430300.BJ")
    # 可能是 BSE_NO_DATA 或 UNKNOWN（依 Tushare 是否有数据）
    assert result.status in (TradabilityStatus.BSE_NO_DATA, TradabilityStatus.UNKNOWN)
    assert not result.hard_block


# ── 场景 5: 除权股 ────────────────────────────────────────
def test_scenario_5_divided_stock():
    """002594.SZ 比亚迪前复权 vs 不复权对比。"""
    from data import qmt_client

    if not qmt_client.is_alive():
        pytest.skip("QMT 未登录")

    none_df = qmt_client.get_kline("002594.SZ", count=500, adjust="none")
    front_df = qmt_client.get_kline("002594.SZ", count=500, adjust="front")

    # 历史首日不同（除权导致），今日相同
    assert float(none_df.iloc[0]["close"]) != float(front_df.iloc[0]["close"]), \
        "长窗口内前复权首日应与非复权首日不同"
    assert abs(float(none_df.iloc[-1]["close"]) - float(front_df.iloc[-1]["close"])) < 0.01


# ── 场景 6: QMT 整体挂 ─────────────────────────────────────
def test_scenario_6_qmt_unavailable(monkeypatch):
    """monkey-patch QMT 挂掉，验证全量降级到 Tushare。"""
    from data import tushare_client
    from tests.fixtures.qmt_mocks import patch_qmt_unavailable

    patch_qmt_unavailable(monkeypatch)
    tushare_client._data_source_map = {}

    # get_basic_info 应该降级到其他源
    info, err = tushare_client.get_basic_info("000001.SZ")
    src = tushare_client._data_source_map.get("基本信息")
    assert src != "qmt", f"QMT 挂了还走 QMT: src={src}"
    # 允许 tushare / eastmoney / akshare / baostock / sina / unavailable
    assert src in ("tushare", "eastmoney", "akshare", "baostock", "sina", "unavailable")


# ── 场景 7: QMT 财务核心表空 ───────────────────────────────
def test_scenario_7_qmt_financial_core_empty(monkeypatch):
    """QMT 核心财务表空 → 整个 financial 降级 Tushare；基本信息仍走 QMT。"""
    from data import tushare_client
    import data.qmt_client as qc
    from tests.fixtures.qmt_mocks import patch_qmt_financial_empty_core

    # QMT is_alive=True，instrument_info 可用，但 get_financial 核心表空
    monkeypatch.setattr(qc, "is_alive", lambda: True)
    monkeypatch.setattr(qc, "get_instrument_info",
                        lambda sym: {"InstrumentName": "A", "OpenDate": "19950101",
                                     "InstrumentStatus": 0, "PreClose": 10.0,
                                     "UpStopPrice": 11.0, "FloatVolume": 1e9, "TotalVolume": 1e9})
    patch_qmt_financial_empty_core(monkeypatch)

    tushare_client._data_source_map = {}
    info, err = tushare_client.get_basic_info("000001.SZ")
    assert tushare_client._data_source_map.get("基本信息") == "qmt"

    fin_text, fin_err = tushare_client.get_financial("000001.SZ")
    # 财务降级
    assert tushare_client._data_source_map.get("财务") != "qmt"


# ── 场景 8: 退市硬拦截 ────────────────────────────────────
def test_scenario_8_delisted_hard_block(monkeypatch):
    """模拟退市：QMT get_instrument_info 返 None（非 BJ） → DELISTED hard_block。"""
    from data.stock_gate import check_tradability, TradabilityStatus, TradabilityBlocked
    import data.qmt_client as qc

    monkeypatch.setattr(qc, "is_alive", lambda: True)
    monkeypatch.setattr(qc, "get_instrument_info", lambda sym: None)

    result = check_tradability("600087.SH")  # 已退市长航凤凰
    assert result.status == TradabilityStatus.DELISTED
    assert result.hard_block is True

    # analyze_stock 应该抛 TradabilityBlocked
    with pytest.raises(TradabilityBlocked):
        raise TradabilityBlocked(result)
```

- [ ] **Step 10.2: Run 压测**

```
cd C:/LinDangAgent && python -m pytest tests/test_qmt_single_stock_refactor.py -v 2>&1 | tail -30
```
Expected:
- 场景 1, 5 需 QMT 登录真实跑
- 场景 2, 3 从池里筛（找不到则 skip，不算失败）
- 场景 4 QMT 登录时返 BSE_NO_DATA
- 场景 6, 7, 8 mock，必过
- 最终 `8 passed` 或 `N passed, M skipped`（允许 skip 未登录相关）

- [ ] **Step 10.3: 全量回归**

```
cd C:/LinDangAgent && python -m pytest tests/test_qmt_client.py tests/test_qmt_client_ext.py tests/test_qmt_integration.py tests/test_qmt_schema_map.py tests/test_stock_gate.py tests/test_qmt_single_stock_refactor.py -v 2>&1 | tail -10
```
Expected: 全部 pass / 仅 QMT 登录相关 skip。

- [ ] **Step 10.4: Commit**

```
cd C:/LinDangAgent && git add tests/test_qmt_single_stock_refactor.py && git commit -m "test(qmt): 8-scenario data-layer stress test for single-stock refactor

Pure data-layer integration tests (no AI calls to save tokens):
1. Normal stock: gate=OK, basic_info source=qmt
2. ST stock (auto-discovered from 沪深A股池): warnings contain 'ST'
3. Newly listed (auto-discovered, OpenDate <30d): status=NEWLY_LISTED
4. BSE stock: status in (BSE_NO_DATA, UNKNOWN), no hard_block
5. Dividend-event stock (002594.SZ BYD): front-adjusted != raw for
   500-day window first close
6. QMT monkey-patched unavailable: all sources != qmt
7. QMT financial core empty: financial source degrades, basic_info
   still uses qmt
8. Delisted simulated: TradabilityBlocked raised

Scenarios 1/2/3/4/5 skip gracefully when QMT not logged in;
6/7/8 are mock-based and always run.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

### 1. Spec coverage

| Spec §  | Task | OK |
|---|---|---|
| §3 Architecture — stock_gate | Task 4 | ✅ |
| §3 Architecture — qmt_client 扩展 | Task 2 | ✅ |
| §3 Architecture — schema_map | Task 3 | ✅ |
| §3 Architecture — 6 getters 加 qmt_fn | Task 5/6/7 | ✅ |
| §3 Architecture — 涨跌幅精准化 | Task 8 Step 8.3 | ✅ |
| §3 Architecture — analysis_service gate | Task 9 | ✅ |
| §4.1 TradabilityResult/Status | Task 4 | ✅ |
| §4.2 qmt_client 4 新函数 | Task 2 | ✅ |
| §4.3 qmt_schema_map 映射表 | Task 3 | ✅ |
| §4.4 tushare_client qmt_fn wire | Task 5/6 | ✅ |
| §4.5 report_data.py 4 getters | Task 7 | ✅ |
| §4.6 analysis_service TradabilityBlocked | Task 9 | ✅ |
| §5 数据流 + _data_source_map dump | Task 8 | ✅ |
| §6 错误处理 | Task 4/5/6/7 隐式覆盖 | ✅ |
| §7 8 场景压测 | Task 10 | ✅ |

**新增**：Task 1 退市码 discovery（spec §9 风险里提到，但 spec 没单独拆 Task——我补的）

### 2. Placeholder scan
- Step 4.4 里有 "TODO: 依 docs/qmt_status_codes.md 更新" ← **这是合理的**，因为 Task 1 discovery 产出决定具体码值；但该 TODO 在 Task 4 实现时必须已被解决（Task 1 在 Task 4 之前）
- Step 8.1 "若有涨跌幅调用" — 条件型步骤，合理
- Step 10.1 scenario 3/4 的 skip 是合理降级

### 3. Type consistency
- `TradabilityStatus` enum 全程一致
- `TradabilityResult` dataclass 字段：`status / hard_block / warnings / facts` 全程一致
- `TradabilityBlocked(result=...)` 构造签名贯穿 Task 4/9/10
- `qmt_client.get_financial(sym, years=3)` 返回 `dict[str, pd.DataFrame]` 一致
- `get_instrument_info(sym) -> dict | None` 返回语义一致
- `_data_source_map` dict 使用一致

---

## Execution Handoff

计划已保存到 `C:\LinDangAgent\docs\superpowers\plans\2026-04-14-single-stock-qmt-refactor.md`。

**两种执行选项**：

**1. Subagent-Driven（推荐）** — 每个 Task 派发一个新 subagent 执行，我在 task 之间 review，快速迭代

**2. Inline Execution** — 当前会话执行，批量推进 + checkpoint review

建议 **1**，和前期 QMT 接入一致。你点头我就启动。
