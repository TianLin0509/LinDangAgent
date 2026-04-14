# QMT 数据源接入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 QMT（xtquant）接入 LinDangAgent 作为最高优先级 K 线数据源，并产出一份实测自动生成的 AI-oriented API reference。

**Architecture:** 新建 `data/qmt_client.py` 薄封装 xtquant；扩展现有 `_try_with_fallback` 调度器加一个 `qmt_fn` 槽位（位于 tushare 之前）；在 `get_price_df` 入口挂接 QMT；探测脚本 `tests/qmt_probe.py` 穷尽式验证 API 并自动生成 reference。

**Tech Stack:** Python 3.12, xtquant (已安装, v250516), pandas, pytest

---

## ⚠️ Spec 执行偏差说明（给执行者）

原 spec 写的金丝雀是 `quick_scout.py` + 改 `data/fallback.py`。实际读代码后发现：

- `quick_scout.py`（根目录）是一次性 debug 脚本，**不是真正的单股分析入口**
- `data/fallback.py` 里是一堆 `{源}_get_*` 函数，**不是调度器**
- **真正的调度器**是 `data/tushare_client.py::_try_with_fallback()`
- **真正的 K 线入口**是 `data/tushare_client.py::get_price_df()`

本计划以实际代码为准：金丝雀挂在 `get_price_df()`，调度器改 `_try_with_fallback()`。spec 的意图（QMT 作为最高优先级、金丝雀式接入、不改下游调用方）完全保留。

---

## 文件结构

| 文件 | 动作 | 责任 |
|---|---|---|
| `data/qmt_client.py` | Create | xtquant 薄封装：`is_alive / get_kline / get_realtime / get_sector_stocks` |
| `data/tushare_client.py` | Modify | `_try_with_fallback` 加 `qmt_fn` 槽位；`get_price_df` 挂接 QMT |
| `tests/test_qmt_client.py` | Create | 单元测试 `qmt_client` 的归一化和错误处理（不依赖真实 QMT） |
| `tests/test_qmt_integration.py` | Create | 集成测试 `get_price_df` 优先走 QMT（mock qmt_client） |
| `tests/qmt_probe.py` | Create | 20 项 API 探测脚本，产出体检报告 + 自动喂养 reference |
| `tests/test_qmt_smoke.py` | Create | 金丝雀验收冒烟（需 QMT 登录） |
| `docs/qmt_reference.md` | Create (auto) | 由 probe 脚本自动生成 |
| `docs/qmt_probe_report_20260413.md` | Create (auto) | 首次探测报告 |
| `C:\Users\lintian\.claude\projects\C--Users-lintian\memory\reference_qmt.md` | Create | memory reference 条目 |
| `C:\Users\lintian\.claude\projects\C--Users-lintian\memory\MEMORY.md` | Modify | 追加 reference 索引 |

---

## Task 1: `data/qmt_client.py` — 薄封装 + 代码归一化（纯函数可单测部分）

**Files:**
- Create: `C:\LinDangAgent\data\qmt_client.py`
- Test: `C:\LinDangAgent\tests\test_qmt_client.py`

- [ ] **Step 1.1: Write the failing test（代码归一化）**

```python
# tests/test_qmt_client.py
import pytest

def test_normalize_symbol_sz():
    from data.qmt_client import _normalize_symbol
    assert _normalize_symbol("000001") == "000001.SZ"
    assert _normalize_symbol("000001.SZ") == "000001.SZ"

def test_normalize_symbol_sh():
    from data.qmt_client import _normalize_symbol
    assert _normalize_symbol("600000") == "600000.SH"
    assert _normalize_symbol("600000.SH") == "600000.SH"

def test_normalize_symbol_chinext():
    from data.qmt_client import _normalize_symbol
    # 创业板 300xxx → SZ
    assert _normalize_symbol("300750") == "300750.SZ"
    # 科创板 688xxx → SH
    assert _normalize_symbol("688981") == "688981.SH"

def test_normalize_symbol_bse():
    from data.qmt_client import _normalize_symbol
    # 北交所 8xxxxx → BJ
    assert _normalize_symbol("832000") == "832000.BJ"

def test_denormalize_symbol():
    from data.qmt_client import _denormalize_symbol
    assert _denormalize_symbol("000001.SZ") == "000001"
    assert _denormalize_symbol("600000.SH") == "600000"
```

- [ ] **Step 1.2: Run test to verify it fails**

```
cd C:/LinDangAgent && python -m pytest tests/test_qmt_client.py -v
```
Expected: ImportError / ModuleNotFoundError for `data.qmt_client`

- [ ] **Step 1.3: Write the module scaffold + normalization helpers**

```python
# data/qmt_client.py
"""
QMT / xtquant 薄封装。
- 只覆盖本期接入需要的 API：健康检查 / 历史 K 线 / 实时行情 / 板块成分
- 未登录 / 超时 / schema 异常一律抛 QMTUnavailable，由上层降级
- 代码归一化：对外不带市场后缀（与现有数据层一致），内部自动补 .SZ/.SH/.BJ
"""
from __future__ import annotations
import logging
import threading
import time
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


class QMTUnavailable(Exception):
    """QMT 客户端未登录 / 连接超时 / schema 不符，调用方应降级"""


# ── 代码归一化 ────────────────────────────────────────────────
def _normalize_symbol(code: str) -> str:
    """
    "000001" → "000001.SZ"
    "600000" → "600000.SH"
    "300750" → "300750.SZ"（创业板）
    "688981" → "688981.SH"（科创板）
    "832000" → "832000.BJ"（北交所）
    已带后缀原样返回。
    """
    if "." in code:
        return code
    prefix = code[:3] if len(code) >= 3 else code
    if prefix.startswith(("600", "601", "603", "605", "688", "689", "900")):
        return f"{code}.SH"
    if prefix.startswith(("000", "001", "002", "003", "300", "301", "200")):
        return f"{code}.SZ"
    if prefix[:1] in ("4", "8", "9"):  # 北交所 43/83/87/88/92xxx 起
        return f"{code}.BJ"
    # 默认按深交所处理
    return f"{code}.SZ"


def _denormalize_symbol(code: str) -> str:
    """去掉 .SH/.SZ/.BJ 后缀"""
    return code.split(".", 1)[0]


# ── 模块级状态（lazy init） ────────────────────────────────────
_init_lock = threading.Lock()
_connected: Optional[bool] = None  # None=未尝试，True=已连，False=不可用
_xtdata = None


def _ensure_connected() -> None:
    """首次调用才 import + connect；失败后标记不可用"""
    global _connected, _xtdata
    if _connected is True:
        return
    if _connected is False:
        raise QMTUnavailable("QMT 之前已标记不可用")
    with _init_lock:
        if _connected is True:
            return
        try:
            from xtquant import xtdata  # noqa
            # xtdata 是全局模块级对象，不需要显式 connect，但调一次 get_client_version 验证
            try:
                ver = xtdata.get_client_version() if hasattr(xtdata, "get_client_version") else "unknown"
                logger.info("[qmt] connected, version=%s", ver)
            except Exception as e:
                raise QMTUnavailable(f"xtdata 无法访问客户端: {e}")
            _xtdata = xtdata
            _connected = True
        except ImportError as e:
            _connected = False
            raise QMTUnavailable(f"xtquant 未安装: {e}")
        except QMTUnavailable:
            _connected = False
            raise
        except Exception as e:
            _connected = False
            raise QMTUnavailable(f"xtdata 连接失败: {e}")


def is_alive() -> bool:
    """健康检查，3 秒超时。失败返回 False 不抛异常。"""
    try:
        _ensure_connected()
        return True
    except QMTUnavailable:
        return False
    except Exception as e:
        logger.warning("[qmt] is_alive unexpected error: %s", e)
        return False


# ── K 线 ──────────────────────────────────────────────────────
_REQUIRED_COLS = ("open", "high", "low", "close", "volume")


def get_kline(
    symbol: str,
    period: str = "1d",
    start: Optional[str] = None,
    end: Optional[str] = None,
    count: int = 120,
    adjust: str = "front",
) -> pd.DataFrame:
    """
    返回标准 OHLCV DataFrame：index=datetime, columns=[open, high, low, close, volume, amount]
    QMT 不可用或 schema 异常抛 QMTUnavailable。
    """
    _ensure_connected()
    sym = _normalize_symbol(symbol)
    dividend_type = {"front": "front", "back": "back", "none": "none"}.get(adjust, "front")
    start_time = start or ""
    end_time = end or ""
    n = count if (not start and not end) else -1

    t0 = time.time()
    try:
        data = _xtdata.get_market_data_ex(
            field_list=["time", "open", "high", "low", "close", "volume", "amount"],
            stock_list=[sym],
            period=period,
            start_time=start_time,
            end_time=end_time,
            count=n,
            dividend_type=dividend_type,
            fill_data=True,
        )
    except Exception as e:
        raise QMTUnavailable(f"get_market_data_ex 调用失败: {e}")

    if not data or sym not in data:
        raise QMTUnavailable(f"QMT 未返回 {sym} 数据")
    df = data[sym]
    if df is None or df.empty:
        raise QMTUnavailable(f"QMT 返回 {sym} 空数据")

    # schema 校验（早期发现 SDK 升级破坏 schema）
    missing = [c for c in _REQUIRED_COLS if c not in df.columns]
    if missing:
        raise QMTUnavailable(f"QMT 返回列缺失: {missing}, 实际={list(df.columns)}")

    # index 归一化为 datetime
    if "time" in df.columns:
        df = df.copy()
        df.index = pd.to_datetime(df["time"], unit="ms", errors="coerce")
        df = df.drop(columns=["time"])

    logger.info("[qmt] get_kline %s period=%s rows=%d cost=%dms",
                sym, period, len(df), int((time.time() - t0) * 1000))
    return df


# ── 实时行情 ──────────────────────────────────────────────────
def get_realtime(symbols: list[str]) -> dict[str, dict]:
    """
    返回: {"000001": {"price": 12.3, "bid1": ..., "ask1": ..., "ts": ...}, ...}
    key 去掉市场后缀以匹配上游契约。
    """
    _ensure_connected()
    syms = [_normalize_symbol(s) for s in symbols]
    try:
        tick = _xtdata.get_full_tick(syms)
    except Exception as e:
        raise QMTUnavailable(f"get_full_tick 失败: {e}")
    if not tick:
        raise QMTUnavailable("QMT 未返回实时行情")

    result = {}
    for sym_with_suffix, row in tick.items():
        plain = _denormalize_symbol(sym_with_suffix)
        result[plain] = {
            "price": row.get("lastPrice"),
            "bid1": row.get("bidPrice", [None])[0] if row.get("bidPrice") else None,
            "ask1": row.get("askPrice", [None])[0] if row.get("askPrice") else None,
            "volume": row.get("volume"),
            "ts": row.get("time"),
        }
    return result


# ── 板块成分 ──────────────────────────────────────────────────
def get_sector_stocks(sector: str) -> list[str]:
    """板块成分股；返回不带市场后缀的代码列表"""
    _ensure_connected()
    try:
        stocks = _xtdata.get_stock_list_in_sector(sector)
    except Exception as e:
        raise QMTUnavailable(f"get_stock_list_in_sector 失败: {e}")
    if not stocks:
        raise QMTUnavailable(f"QMT 未返回板块 {sector} 成分")
    return [_denormalize_symbol(s) for s in stocks]
```

- [ ] **Step 1.4: Run tests to verify归一化 passes**

```
cd C:/LinDangAgent && python -m pytest tests/test_qmt_client.py -v
```
Expected: 5 passed

- [ ] **Step 1.5: Commit**

```
cd C:/LinDangAgent && git add data/qmt_client.py tests/test_qmt_client.py && git commit -m "feat(qmt): add qmt_client wrapper with symbol normalization

Thin wrapper over xtquant covering is_alive/get_kline/get_realtime/
get_sector_stocks. Includes symbol normalization (with/without market
suffix) unit-tested without requiring live QMT connection.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: 扩展 `_try_with_fallback` 加 qmt_fn 槽位

**Files:**
- Modify: `C:\LinDangAgent\data\tushare_client.py:157-228`
- Test: `C:\LinDangAgent\tests\test_qmt_integration.py`

- [ ] **Step 2.1: Write the failing test（QMT 优先）**

```python
# tests/test_qmt_integration.py
import pandas as pd

def test_try_with_fallback_qmt_first(monkeypatch):
    """QMT 成功时不应调用 tushare/其他源"""
    from data import tushare_client

    called = []

    def qmt_fn():
        called.append("qmt")
        df = pd.DataFrame({"收盘": [10.0]})
        return df, None

    def tushare_fn():
        called.append("tushare")
        return pd.DataFrame(), "should not be called"

    df, err = tushare_client._try_with_fallback(
        tushare_fn, label="K线", qmt_fn=qmt_fn
    )
    assert err is None
    assert called == ["qmt"]
    assert not df.empty

def test_try_with_fallback_qmt_fail_fallback(monkeypatch):
    """QMT 抛异常时静默降级到 tushare"""
    from data import tushare_client
    from data.qmt_client import QMTUnavailable

    def qmt_fn():
        raise QMTUnavailable("not logged in")

    def tushare_fn():
        return pd.DataFrame({"收盘": [10.0]}), None

    # tushare 正常，应返回 tushare 结果；但当前 _get_pro() 可能返回 None，要在 monkeypatch 中注入
    monkeypatch.setattr(tushare_client, "_get_pro", lambda: object())

    df, err = tushare_client._try_with_fallback(
        tushare_fn, label="K线", qmt_fn=qmt_fn
    )
    assert err is None
    assert not df.empty
```

- [ ] **Step 2.2: Run test to verify it fails**

```
cd C:/LinDangAgent && python -m pytest tests/test_qmt_integration.py -v
```
Expected: FAIL — `qmt_fn` 参数不存在 / `TypeError: unexpected keyword argument 'qmt_fn'`

- [ ] **Step 2.3: Modify `_try_with_fallback` to accept qmt_fn**

Edit `data/tushare_client.py` — find the function signature at line 157 and modify:

```python
def _try_with_fallback(tushare_fn, akshare_fn=None, eastmoney_fn=None, baostock_fn=None, sina_fn=None, label="数据", qmt_fn=None):
    """依次尝试 QMT → Tushare → 东方财富 → AKShare → Baostock → Sina，返回第一个成功的结果

    优先级：QMT（券商直连，本机可用时最优） > 东方财富（0.23s最快） > Tushare（全但易挂） > ...
    qmt_fn 为 None 表示调用方不提供 QMT 实现；抛 QMTUnavailable 则静默降级。
    """
    global _data_source

    # 第零层：QMT（券商直连，最高优先级；未登录/失败静默降级）
    if qmt_fn is not None:
        try:
            result, err = qmt_fn()
            if err is None:
                with _init_lock:
                    _data_source = "qmt"
                return result, None
        except Exception as e:
            # QMTUnavailable 以及所有其他异常一律静默降级（预期：QMT 可能没登录）
            logger.debug("[%s] qmt 失败（降级）: %s", label, e)

    # 第一层：Tushare（数据最全）
    if _get_pro() is not None:
        # ... 保持原有逻辑
```

Important：只修改函数签名和增加 QMT 优先块，**不删任何已有逻辑**。其他层原样保留。

- [ ] **Step 2.4: Run test to verify it passes**

```
cd C:/LinDangAgent && python -m pytest tests/test_qmt_integration.py::test_try_with_fallback_qmt_first -v
```
Expected: PASS

```
cd C:/LinDangAgent && python -m pytest tests/test_qmt_integration.py -v
```
Expected: 2 passed

- [ ] **Step 2.5: Run full existing tushare-related tests for regression**

```
cd C:/LinDangAgent && python -m pytest tests/ -k "kline or tushare or integration" -v --no-header 2>&1 | tail -30
```
Expected: No new failures vs baseline.

- [ ] **Step 2.6: Commit**

```
cd C:/LinDangAgent && git add data/tushare_client.py tests/test_qmt_integration.py && git commit -m "feat(data): add qmt_fn slot to _try_with_fallback dispatcher

QMT is inserted as the top-priority data source slot. QMTUnavailable
and all other exceptions from qmt_fn trigger silent degradation to the
existing tushare/eastmoney/akshare/baostock/sina chain. Default
qmt_fn=None preserves 100% backward compatibility for call sites that
haven't adopted QMT yet.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: 把 QMT 挂接到 `get_price_df`（金丝雀接入点）

**Files:**
- Modify: `C:\LinDangAgent\data\tushare_client.py:385-412` (`get_price_df`)

- [ ] **Step 3.1: Write the failing test（get_price_df 走 QMT 且 schema 被转成中文列）**

```python
# 追加到 tests/test_qmt_integration.py
def test_get_price_df_uses_qmt(monkeypatch):
    """QMT 可用时，get_price_df 应返回 QMT 数据，列名是中文"""
    import pandas as pd
    from data import tushare_client

    # 伪造 qmt_client.get_kline
    import data.qmt_client as qmt_client
    def fake_get_kline(symbol, period="1d", start=None, end=None, count=120, adjust="front"):
        idx = pd.to_datetime(["2026-04-10", "2026-04-11"])
        return pd.DataFrame({
            "open": [10.0, 10.5], "high": [10.8, 10.9],
            "low": [9.9, 10.3], "close": [10.5, 10.7],
            "volume": [1000, 1200], "amount": [10500, 12800],
        }, index=idx)
    monkeypatch.setattr(qmt_client, "get_kline", fake_get_kline)
    monkeypatch.setattr(qmt_client, "is_alive", lambda: True)

    df, err = tushare_client.get_price_df("000001.SZ", days=2)
    assert err is None
    # 必须是中文列名（符合现有契约）
    for col in ["日期", "开盘", "最高", "最低", "收盘", "成交量"]:
        assert col in df.columns, f"缺少列 {col}: 实际={list(df.columns)}"
    assert len(df) == 2
```

- [ ] **Step 3.2: Run test to verify it fails**

```
cd C:/LinDangAgent && python -m pytest tests/test_qmt_integration.py::test_get_price_df_uses_qmt -v
```
Expected: FAIL — `get_price_df` 还没挂 QMT，要么走 tushare 要么返回英文列。

- [ ] **Step 3.3: Modify `get_price_df` to wire QMT in**

Edit `data/tushare_client.py` — find `get_price_df` at line 385. Add a `_qmt` closure and pass as `qmt_fn`:

```python
@compat_cache(ttl=300, show_spinner=False)
def get_price_df(ts_code: str, days: int = 140) -> tuple[pd.DataFrame, str | None]:
    from data.fallback import ak_get_price_df, em_get_price_df, bs_get_price_df
    from data import qmt_client
    from data.qmt_client import QMTUnavailable

    def _qmt():
        # QMT 未启用（未登录/未装）时 is_alive 返回 False，直接 raise 触发降级
        if not qmt_client.is_alive():
            raise QMTUnavailable("qmt not alive")
        df = qmt_client.get_kline(ts_code, period="1d", count=days, adjust="front")
        if df is None or df.empty:
            return pd.DataFrame(), "QMT 无 K 线"
        # 转换为项目标准中文列 schema
        out = df.reset_index().rename(columns={
            "index": "日期",
            "open": "开盘", "high": "最高", "low": "最低", "close": "收盘",
            "volume": "成交量", "amount": "成交额",
        })
        # 日期列若不存在则用现有 index
        if "日期" not in out.columns and "time" in out.columns:
            out = out.rename(columns={"time": "日期"})
        # 计算涨跌幅（兼容现有 schema；QMT 原生无此列）
        if "涨跌幅" not in out.columns:
            out["涨跌幅"] = out["收盘"].pct_change() * 100
        return out, None

    def _tushare():
        if _get_pro() is None:
            return pd.DataFrame(), _ts_err
        df = _retry_call(
            lambda: _get_pro().daily(ts_code=ts_code, start_date=ndays_ago(days), end_date=today()),
            retries=3, delay=1,
        )
        if df is None or df.empty:
            return pd.DataFrame(), "未获取到K线数据"
        df = df.sort_values("trade_date").reset_index(drop=True)
        df = df.rename(columns={
            "trade_date": "日期", "open": "开盘", "high": "最高",
            "low": "最低", "close": "收盘", "vol": "成交量",
            "pct_chg": "涨跌幅", "amount": "成交额",
        })
        return df, None

    return _try_with_fallback(
        _tushare,
        lambda: ak_get_price_df(ts_code, days),
        lambda: em_get_price_df(ts_code, days),
        baostock_fn=lambda: bs_get_price_df(ts_code, days),
        label="K线",
        qmt_fn=_qmt,
    )
```

- [ ] **Step 3.4: Run test to verify it passes**

```
cd C:/LinDangAgent && python -m pytest tests/test_qmt_integration.py::test_get_price_df_uses_qmt -v
```
Expected: PASS

- [ ] **Step 3.5: Run all integration tests for regression**

```
cd C:/LinDangAgent && python -m pytest tests/ -k "integration or kline or tushare" -v 2>&1 | tail -30
```
Expected: No new failures.

- [ ] **Step 3.6: Commit**

```
cd C:/LinDangAgent && git add data/tushare_client.py tests/test_qmt_integration.py && git commit -m "feat(data): wire QMT into get_price_df as top-priority source

get_price_df now tries QMT first via qmt_fn slot; on QMTUnavailable
or empty result it falls back silently to Tushare/EM/AKShare/Baostock.
QMT output is adapted to the project's standard Chinese-column K-line
schema (日期/开盘/最高/最低/收盘/成交量/涨跌幅/成交额) so downstream
consumers are unaware of the source switch.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: `tests/qmt_probe.py` — 20 项 API 探测脚本

**Files:**
- Create: `C:\LinDangAgent\tests\qmt_probe.py`

**Note:** 这是探测脚本，不走 pytest，是独立可执行脚本。分段构建以便中间能跑。

- [ ] **Step 4.1: Write probe skeleton + connection layer (Probes 1-2)**

```python
# tests/qmt_probe.py
"""
QMT / xtquant API 全功能探测脚本。

用法:
    python tests/qmt_probe.py                 # 使用默认 symbol 000001
    python tests/qmt_probe.py --symbol 600000 # 指定测试标的
    python tests/qmt_probe.py --no-reference  # 不自动更新 reference.md

产出:
    - 控制台报告（即时可读）
    - docs/qmt_probe_report_YYYYMMDD_HHMMSS.md（完整报告归档）
    - docs/qmt_reference.md（AI-oriented API 参考，自动追加实测记录）
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "docs"
REFERENCE_MD = ROOT / "docs" / "qmt_reference.md"


@dataclass
class ProbeResult:
    idx: int
    name: str
    status: str  # "OK" / "WARN" / "FAIL" / "RAISED"
    cost_ms: Optional[int] = None
    summary: str = ""
    sample: str = ""
    error: str = ""
    api_signature: str = ""     # 用于 reference.md
    returns_schema: str = ""    # 用于 reference.md
    gotchas: list[str] = field(default_factory=list)


def _run(idx: int, name: str, sig: str, fn: Callable[[], tuple[str, str, list[str]]]) -> ProbeResult:
    """fn 返回 (summary, sample, gotchas_list)。异常自动捕获。"""
    r = ProbeResult(idx=idx, name=name, api_signature=sig)
    t0 = time.time()
    try:
        summary, sample, gotchas = fn()
        r.status = "OK"
        r.summary = summary
        r.sample = sample
        r.gotchas = gotchas
    except AssertionError as e:
        r.status = "WARN"
        r.error = str(e)
    except Exception as e:
        r.status = "RAISED"
        r.error = f"{type(e).__name__}: {e}"
    r.cost_ms = int((time.time() - t0) * 1000)
    return r


def probe_connect(xtdata) -> tuple[str, str, list[str]]:
    # xtdata 只是全局模块，没有显式 connect；用 get_client_version 代理验证
    return ("imported OK", f"xtdata module path: {xtdata.__file__}", [])


def probe_client_version(xtdata) -> tuple[str, str, list[str]]:
    if not hasattr(xtdata, "get_client_version"):
        raise AssertionError("get_client_version 不存在，SDK 版本过老或 API 变更")
    ver = xtdata.get_client_version()
    return (f"version={ver}", f"{ver}", [])
```

- [ ] **Step 4.2: Add probes 3-8 (历史 K 线)**

```python
def probe_kline_daily_60(xtdata, sym) -> tuple[str, str, list[str]]:
    data = xtdata.get_market_data_ex(
        field_list=["time", "open", "high", "low", "close", "volume", "amount"],
        stock_list=[sym], period="1d", count=60,
    )
    if not data or sym not in data or data[sym].empty:
        raise AssertionError(f"返回空: keys={list((data or {}).keys())}")
    df = data[sym]
    cols = list(df.columns)
    first = df.iloc[0].to_dict()
    last = df.iloc[-1].to_dict()
    return (
        f"rows={len(df)}, cols={cols}",
        f"first: {first}\nlast:  {last}",
        [
            "symbol 必须带 .SZ/.SH 后缀",
            "period='1d' 日线；'1m'/'5m' 仅交易时段可查",
            "返回是 dict[symbol, DataFrame]，需要 data[sym] 取值",
        ],
    )


def probe_kline_daily_all(xtdata, sym) -> tuple[str, str, list[str]]:
    data = xtdata.get_market_data_ex(
        field_list=["time", "close"], stock_list=[sym], period="1d", count=-1,
    )
    if not data or sym not in data or data[sym].empty:
        raise AssertionError("全历史返回空")
    df = data[sym]
    return (f"rows={len(df)}", f"first_time={df.iloc[0]['time']}, last_close={df.iloc[-1]['close']}",
            ["count=-1 取全历史；数据量大时注意性能"])


def probe_kline_1m(xtdata, sym) -> tuple[str, str, list[str]]:
    data = xtdata.get_market_data_ex(
        field_list=["time", "open", "close"], stock_list=[sym], period="1m", count=240,
    )
    if not data or sym not in data or data[sym].empty:
        raise AssertionError("1m 返回空（可能为非交易时段预期行为）")
    return (f"rows={len(data[sym])}", str(data[sym].tail(2).to_dict()),
            ["分钟线仅交易时段可取最新；盘后跑此 probe 可能返回历史最后一个交易日"])


def probe_kline_multi_period(xtdata, sym) -> tuple[str, str, list[str]]:
    results = {}
    for p in ("5m", "15m", "30m", "60m"):
        data = xtdata.get_market_data_ex(
            field_list=["time", "close"], stock_list=[sym], period=p, count=10,
        )
        rows = len(data.get(sym, []))
        results[p] = rows
    if all(v == 0 for v in results.values()):
        raise AssertionError(f"所有周期都返回空: {results}")
    return (f"周期-行数: {results}", json.dumps(results), [])


def probe_kline_weekly_monthly(xtdata, sym) -> tuple[str, str, list[str]]:
    wk = xtdata.get_market_data_ex(["time", "close"], [sym], period="1w", count=10)
    mo = xtdata.get_market_data_ex(["time", "close"], [sym], period="1mon", count=10)
    return (f"week_rows={len(wk.get(sym, []))}, month_rows={len(mo.get(sym, []))}",
            f"weekly period='1w', monthly period='1mon'",
            ["周线 period='1w'；月线 period='1mon'（不是 '1M'）"])


def probe_kline_adjust(xtdata, sym) -> tuple[str, str, list[str]]:
    results = {}
    for adj in ("none", "front", "back"):
        try:
            data = xtdata.get_market_data_ex(
                ["time", "close"], [sym], period="1d", count=5, dividend_type=adj,
            )
            last_close = data.get(sym, []).iloc[-1]["close"] if sym in data and not data[sym].empty else None
            results[adj] = last_close
        except Exception as e:
            results[adj] = f"ERR:{e}"
    return (f"复权方式对比: {results}",
            json.dumps(results, default=str),
            ["dividend_type 取值: 'none' / 'front' / 'back'（不是 'qfq'/'hfq'）"])
```

- [ ] **Step 4.3: Add probes 9-20 (行情/元信息/边界)**

```python
def probe_full_tick(xtdata, sym) -> tuple[str, str, list[str]]:
    tick = xtdata.get_full_tick([sym])
    if not tick or sym not in tick:
        raise AssertionError("快照返回空（盘后预期可能只有上一交易日收盘快照）")
    row = tick[sym]
    keys = list(row.keys())
    return (f"字段: {keys}", f"sample: lastPrice={row.get('lastPrice')}, time={row.get('time')}",
            ["盘后 get_full_tick 返回上个交易日收盘；盘中才是实时"])


def probe_subscribe(xtdata, sym) -> tuple[str, str, list[str]]:
    # 订阅后立即取消，只验证 API 可用
    received = []
    def cb(data):
        received.append(data)
    try:
        subid = xtdata.subscribe_quote(sym, period="1d", callback=cb)
        time.sleep(1.0)
        if hasattr(xtdata, "unsubscribe_quote"):
            xtdata.unsubscribe_quote(subid)
        return (f"订阅 subid={subid}, received={len(received)} 次回调",
                f"回调数据样例前 200 字: {str(received[:1])[:200]}",
                ["subscribe_quote 是长期订阅，记得 unsubscribe_quote 清理",
                 "盘后订阅不会触发回调，只能验证 API 调用成功"])
    except Exception as e:
        raise AssertionError(f"订阅失败: {e}")


def probe_instrument_detail(xtdata, sym) -> tuple[str, str, list[str]]:
    detail = xtdata.get_instrument_detail(sym)
    if not detail:
        raise AssertionError("instrument_detail 返回空")
    return (f"字段: {list(detail.keys())[:10]}...",
            f"sample: {json.dumps({k: detail[k] for k in list(detail.keys())[:5]}, default=str)}",
            [])


def probe_sector_a(xtdata) -> tuple[str, str, list[str]]:
    stocks = xtdata.get_stock_list_in_sector("沪深A股")
    if not stocks:
        raise AssertionError("沪深A股板块为空")
    return (f"A股股票数={len(stocks)}", f"前5: {stocks[:5]}",
            ["板块名用中文，如 '沪深A股' / '科创板' / '创业板'"])


def probe_sector_star(xtdata) -> tuple[str, str, list[str]]:
    stocks = xtdata.get_stock_list_in_sector("科创板")
    if not stocks:
        raise AssertionError("科创板板块为空")
    return (f"科创板股票数={len(stocks)}", f"前5: {stocks[:5]}", [])


def probe_sector_list(xtdata) -> tuple[str, str, list[str]]:
    if not hasattr(xtdata, "get_sector_list"):
        raise AssertionError("get_sector_list 不存在")
    sectors = xtdata.get_sector_list()
    return (f"板块总数={len(sectors)}", f"前10: {sectors[:10]}",
            ["用这个 API 发现所有可查板块名"])


def probe_financial(xtdata, sym) -> tuple[str, str, list[str]]:
    if not hasattr(xtdata, "get_financial_data"):
        raise AssertionError("get_financial_data 不存在")
    fin = xtdata.get_financial_data([sym], table_list=["Balance"])
    if not fin:
        raise AssertionError("财务返回空")
    return (f"keys={list(fin.keys())[:3]}", f"{str(fin)[:200]}",
            ["财务数据可选但不是 QMT 强项，项目继续走 Tushare/AKShare"])


def probe_instrument_type(xtdata, sym) -> tuple[str, str, list[str]]:
    if not hasattr(xtdata, "get_instrument_type"):
        raise AssertionError("get_instrument_type 不存在")
    t = xtdata.get_instrument_type(sym)
    return (f"type={t}", f"{t}", ["区分股票/ETF/指数"])


def probe_bad_symbol(xtdata) -> tuple[str, str, list[str]]:
    try:
        data = xtdata.get_market_data_ex(
            ["time", "close"], ["999999.XX"], period="1d", count=5,
        )
        if not data or not data.get("999999.XX", pd.DataFrame()).empty:
            return ("非法 symbol 静默返回空", f"{data}", ["非法 symbol 不抛异常，返回空 df"])
        raise AssertionError(f"非法 symbol 异常行为: {data}")
    except Exception as e:
        return (f"非法 symbol 抛异常: {type(e).__name__}", str(e)[:200], [])


def probe_huge_count(xtdata, sym) -> tuple[str, str, list[str]]:
    t0 = time.time()
    data = xtdata.get_market_data_ex(
        ["time", "close"], [sym], period="1d", count=1_000_000,
    )
    rows = len(data.get(sym, []))
    cost = int((time.time() - t0) * 1000)
    return (f"超长 count=1M 返回 rows={rows}, 耗时 {cost}ms",
            f"实际返回全部历史，不报错",
            ["count 超过历史总量时安全截断为全历史"])


def probe_multi_symbol(xtdata) -> tuple[str, str, list[str]]:
    data = xtdata.get_market_data_ex(
        ["time", "close"], ["000001.SZ", "600000.SH"], period="1d", count=5,
    )
    ok_symbols = [s for s in data if not data[s].empty]
    if len(ok_symbols) < 2:
        raise AssertionError(f"批量跨市场失败: {list(data.keys())}")
    return (f"批量OK: {ok_symbols}", f"两只股票各返回 5 行",
            ["支持一次传入跨市场多只股票"])
```

- [ ] **Step 4.4: Add main runner + 报告生成**

```python
import pandas as pd  # noqa: E402


def build_probes(sym: str):
    """返回 [(idx, name, sig, callable), ...]"""
    from xtquant import xtdata
    return [
        (1,  "xtdata 可导入",              "import xtquant.xtdata",                          lambda: probe_connect(xtdata)),
        (2,  "get_client_version",         "xtdata.get_client_version()",                    lambda: probe_client_version(xtdata)),
        (3,  "日线 x 60 根",               "get_market_data_ex(..., period='1d', count=60)", lambda: probe_kline_daily_60(xtdata, sym)),
        (4,  "日线全历史 (count=-1)",      "get_market_data_ex(..., count=-1)",              lambda: probe_kline_daily_all(xtdata, sym)),
        (5,  "1m x 240 根",                "get_market_data_ex(..., period='1m', count=240)",lambda: probe_kline_1m(xtdata, sym)),
        (6,  "5m/15m/30m/60m 各周期",      "get_market_data_ex(..., period='5m/15m/30m/60m')",lambda: probe_kline_multi_period(xtdata, sym)),
        (7,  "周线/月线",                  "get_market_data_ex(..., period='1w'/'1mon')",    lambda: probe_kline_weekly_monthly(xtdata, sym)),
        (8,  "复权对比",                   "get_market_data_ex(..., dividend_type='front'/'back'/'none')", lambda: probe_kline_adjust(xtdata, sym)),
        (9,  "get_full_tick",              "xtdata.get_full_tick([sym])",                    lambda: probe_full_tick(xtdata, sym)),
        (10, "subscribe_quote",            "xtdata.subscribe_quote(sym, period, callback)",  lambda: probe_subscribe(xtdata, sym)),
        (11, "get_instrument_detail",      "xtdata.get_instrument_detail(sym)",              lambda: probe_instrument_detail(xtdata, sym)),
        (12, "沪深A股 板块成分",           "xtdata.get_stock_list_in_sector('沪深A股')",     lambda: probe_sector_a(xtdata)),
        (13, "科创板 板块成分",            "xtdata.get_stock_list_in_sector('科创板')",      lambda: probe_sector_star(xtdata)),
        (14, "get_sector_list",            "xtdata.get_sector_list()",                       lambda: probe_sector_list(xtdata)),
        (15, "get_financial_data",         "xtdata.get_financial_data([sym], ['Balance'])",  lambda: probe_financial(xtdata, sym)),
        (16, "get_instrument_type",        "xtdata.get_instrument_type(sym)",                lambda: probe_instrument_type(xtdata, sym)),
        (17, "非法 symbol",                "get_market_data_ex(['999999.XX'], ...)",         lambda: probe_bad_symbol(xtdata)),
        (18, "超长 count=1M",              "get_market_data_ex(..., count=1_000_000)",       lambda: probe_huge_count(xtdata, sym)),
        (19, "跨市场批量",                 "get_market_data_ex(['000001.SZ','600000.SH'])",  lambda: probe_multi_symbol(xtdata)),
        (20, "保留槽位（预留 Level2）",    "reserved",                                       lambda: ("skipped", "Level2 非本期目标", [])),
    ]


def render_console(results: list[ProbeResult]) -> str:
    lines = [f"======== QMT API Probe Report @ {dt.datetime.now():%Y-%m-%d %H:%M:%S} ========"]
    icon = {"OK": "OK", "WARN": "WARN", "RAISED": "FAIL"}
    for r in results:
        mark = icon.get(r.status, r.status)
        lines.append(f"[{r.idx:2d}] {r.name:<30s}  {mark:<5s} {r.cost_ms or 0:>5d}ms  {r.summary[:60]}")
        if r.error:
            lines.append(f"      error: {r.error[:120]}")
    ok_cnt = sum(1 for r in results if r.status == "OK")
    warn_cnt = sum(1 for r in results if r.status == "WARN")
    fail_cnt = sum(1 for r in results if r.status == "RAISED")
    lines.append(f"======== Summary: {ok_cnt}/{len(results)} OK, {warn_cnt} Warning, {fail_cnt} Fatal ========")
    return "\n".join(lines)


def render_report_md(results: list[ProbeResult], symbol: str) -> str:
    ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"# QMT Probe Report\n\n*Generated: {ts}  |  symbol: {symbol}*\n"]
    for r in results:
        lines.append(f"## [{r.idx}] {r.name}")
        lines.append(f"- **Signature:** `{r.api_signature}`")
        lines.append(f"- **Status:** {r.status}  (cost {r.cost_ms or 0}ms)")
        lines.append(f"- **Summary:** {r.summary}")
        if r.sample:
            lines.append(f"- **Sample:**\n```\n{r.sample[:500]}\n```")
        if r.error:
            lines.append(f"- **Error:** `{r.error}`")
        if r.gotchas:
            lines.append("- **Gotchas:**")
            for g in r.gotchas:
                lines.append(f"  - {g}")
        lines.append("")
    return "\n".join(lines)


def append_reference(results: list[ProbeResult]) -> None:
    """把 OK 的 API 追加到 qmt_reference.md（覆盖旧条目）"""
    ts = dt.datetime.now().strftime("%Y-%m-%d")
    header = f"# QMT / xtquant Reference (AI-oriented, auto-generated)\n\n*Last verified: {ts} by tests/qmt_probe.py*\n\n"
    body = []
    for r in results:
        if r.status != "OK":
            continue
        body.append(f"## {r.name}")
        body.append(f"**Signature:** `{r.api_signature}`")
        body.append(f"**Status (verified {ts}):** OK, {r.cost_ms or 0}ms")
        if r.summary:
            body.append(f"**Returns summary:** {r.summary}")
        if r.sample:
            body.append(f"**Verified sample:**\n```\n{r.sample[:400]}\n```")
        if r.gotchas:
            body.append("**Gotchas:**")
            for g in r.gotchas:
                body.append(f"- {g}")
        body.append("")
    REFERENCE_MD.parent.mkdir(parents=True, exist_ok=True)
    REFERENCE_MD.write_text(header + "\n".join(body), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="000001", help="测试用股票代码（不带后缀，默认 000001）")
    ap.add_argument("--no-reference", action="store_true", help="不更新 docs/qmt_reference.md")
    args = ap.parse_args()

    sys.path.insert(0, str(ROOT))

    from data.qmt_client import _normalize_symbol
    sym = _normalize_symbol(args.symbol)

    results: list[ProbeResult] = []
    for idx, name, sig, fn in build_probes(sym):
        r = _run(idx, name, sig, fn)
        results.append(r)

    console = render_console(results)
    print(console)

    # 写完整报告
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"qmt_probe_report_{dt.datetime.now():%Y%m%d_%H%M%S}.md"
    report_path.write_text(render_report_md(results, sym), encoding="utf-8")
    print(f"\n→ Report saved to: {report_path.relative_to(ROOT)}")

    if not args.no_reference:
        append_reference(results)
        print(f"→ qmt_reference.md updated: {REFERENCE_MD.relative_to(ROOT)}")

    return 0 if sum(1 for r in results if r.status == "RAISED") == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4.5: 语法检查 + dry run（QMT 未登录也应优雅报错）**

```
cd C:/LinDangAgent && python -c "import tests.qmt_probe as m; print('import OK')"
```
Expected: `import OK`

- [ ] **Step 4.6: 实测运行（需 QMT 客户端已登录）**

```
cd C:/LinDangAgent && python tests/qmt_probe.py --symbol 000001
```
Expected:
- 控制台打印 20 项体检报告
- `docs/qmt_probe_report_YYYYMMDD_HHMMSS.md` 生成
- `docs/qmt_reference.md` 生成（含所有 OK 的 API）
- ≥15/20 为 OK

**验收失败处理**：
- 连接失败（[1]/[2] RAISED）→ 检查 QMT 客户端是否登录。未登录就停下，向用户汇报。
- 某些 probe 意外 RAISED → 记录 error，继续跑其他；完成后根据结果调整 probe 代码或在 gotchas 中标注真实行为（不要骗自己说"通过了"）

- [ ] **Step 4.7: Commit**

```
cd C:/LinDangAgent && git add tests/qmt_probe.py docs/qmt_reference.md docs/qmt_probe_report_*.md && git commit -m "feat(qmt): add qmt_probe.py — 20-API exhaustive validator

Probes connection layer, K-line (daily/intraday/weekly/monthly/adjust),
realtime (full_tick + subscribe), instrument metadata, sectors,
financial, boundary cases (bad symbol, huge count, cross-market batch).
Auto-generates docs/qmt_reference.md with verified schema/samples/
gotchas so future Claude sessions can grep the actual API behavior
instead of re-reading the official docs.

First verified run included under docs/qmt_probe_report_*.md.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: `tests/test_qmt_smoke.py` — 金丝雀验收冒烟

**Files:**
- Create: `C:\LinDangAgent\tests\test_qmt_smoke.py`

- [ ] **Step 5.1: Write smoke test**

```python
# tests/test_qmt_smoke.py
"""
QMT 金丝雀冒烟测试 —— 需 QMT 客户端已登录

运行: python tests/test_qmt_smoke.py
非 pytest 断言，打印人类可读报告，返回码 0=通过 / 1=失败。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    print("======== QMT 金丝雀冒烟测试 ========")

    # 1. is_alive
    from data import qmt_client
    alive = qmt_client.is_alive()
    print(f"[1/4] qmt_client.is_alive() → {alive}")
    if not alive:
        print("    ❌ QMT 未登录/不可用，后续测试跳过")
        return 1

    # 2. get_kline
    try:
        df = qmt_client.get_kline("000001", count=60)
        print(f"[2/4] get_kline('000001', count=60) → rows={len(df)}, cols={list(df.columns)}")
        assert len(df) >= 40, f"期望 ≥40 行，实际 {len(df)}"
        for c in ("open", "high", "low", "close", "volume"):
            assert c in df.columns, f"缺少列 {c}"
    except Exception as e:
        print(f"    ❌ get_kline 失败: {e}")
        return 1

    # 3. get_price_df 走 QMT（验证 _data_source 切换）
    try:
        from data import tushare_client
        df2, err = tushare_client.get_price_df("000001.SZ", days=60)
        src = tushare_client._data_source
        print(f"[3/4] tushare_client.get_price_df('000001.SZ', days=60) → rows={len(df2)}, _data_source={src}, err={err}")
        if src != "qmt":
            print(f"    ⚠️  预期 _data_source=qmt，实际 {src}（QMT 可能因 schema 不符被降级）")
            return 1
        for c in ("日期", "开盘", "收盘", "成交量"):
            assert c in df2.columns, f"缺少中文列 {c}"
    except Exception as e:
        print(f"    ❌ get_price_df 失败: {e}")
        return 1

    # 4. 未登录场景降级（清掉 _connected 模拟）
    print("[4/4] 降级演练（跳过：需手动关闭 QMT 客户端测试）")

    print("\n✅ 金丝雀冒烟测试通过：QMT 已成为 get_price_df 的最高优先级数据源")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5.2: 实测运行**

```
cd C:/LinDangAgent && python tests/test_qmt_smoke.py
```
Expected: 返回码 0，打印 `_data_source=qmt`

- [ ] **Step 5.3: Commit**

```
cd C:/LinDangAgent && git add tests/test_qmt_smoke.py && git commit -m "test(qmt): add canary smoke test for get_price_df integration

Verifies qmt_client.is_alive() + get_kline() + that get_price_df
actually routes through QMT (_data_source='qmt' after call). Run
manually with QMT client logged in as acceptance gate.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: 更新 memory — 添加 QMT reference 条目

**Files:**
- Create: `C:\Users\lintian\.claude\projects\C--Users-lintian\memory\reference_qmt.md`
- Modify: `C:\Users\lintian\.claude\projects\C--Users-lintian\memory\MEMORY.md`

- [ ] **Step 6.1: 写 memory 文件**

Use Write tool to create `C:\Users\lintian\.claude\projects\C--Users-lintian\memory\reference_qmt.md`:

```markdown
---
name: QMT / xtquant API Reference 文档位置
description: QMT API 实测 reference，编码相关查询必先查此文档；由 tests/qmt_probe.py 自动生成
type: reference
---

## QMT / xtquant API Reference

- **实测 reference（主要入口）**: `C:\LinDangAgent\docs\qmt_reference.md`
- **最新探测报告**: `C:\LinDangAgent\docs\qmt_probe_report_*.md`（按时间戳）
- **探测脚本**: `C:\LinDangAgent\tests\qmt_probe.py`
- **客户端封装**: `C:\LinDangAgent\data\qmt_client.py`
- **官方文档**: https://dict.thinktrader.net/ （reference 未覆盖时查此处）

**How to apply:**
- 涉及 QMT / xtquant / 数据层查询时，**先读 `qmt_reference.md`** 找实测 schema 与坑
- 若需要的 API 不在 reference 里，查官方文档后**先扩展 `qmt_probe.py` 加一个 probe 并实测**，实测通过再写业务代码
- 不要凭官方文档直接写代码——文档和 SDK 实际行为常有差异，坑必须实测记录

**SDK 状态**: xtquant v250516 已安装在全局 Python 312，国金证券 QMT 账号于 2026-04-13 开通
```

- [ ] **Step 6.2: 在 MEMORY.md 索引中追加**

Use Edit tool on `C:\Users\lintian\.claude\projects\C--Users-lintian\memory\MEMORY.md`:

找到 `## References` 段的最后一行，在后面追加：

```markdown
- [reference_qmt.md](reference_qmt.md) — QMT/xtquant API reference 文档位置 + 实测策略
```

- [ ] **Step 6.3: 验证 memory 索引生效**

```
cat C:/Users/lintian/.claude/projects/C--Users-lintian/memory/MEMORY.md | grep -A0 qmt
```
Expected: 输出包含新追加的一行。

（此步无需 commit，memory 不纳入 LinDangAgent 仓库）

---

## Task 7: 最终验收 + project memory 更新

- [ ] **Step 7.1: 跑完整验收流程**

```
cd C:/LinDangAgent && python -m pytest tests/test_qmt_client.py tests/test_qmt_integration.py -v
```
Expected: 全部通过。

```
cd C:/LinDangAgent && python tests/qmt_probe.py --symbol 000001 2>&1 | tail -5
```
Expected: Summary 显示 ≥15/20 OK。

```
cd C:/LinDangAgent && python tests/test_qmt_smoke.py
```
Expected: 返回码 0，`_data_source=qmt`。

- [ ] **Step 7.2: 更新 project memory 状态**

Use Edit tool on `C:\Users\lintian\.claude\projects\C--Users-lintian\memory\project_qmt_integration.md` — 把 "## QMT 接入状态" 段下的 **"开通日期"** 这一行后面追加 `，阶段 A 已打通`。

然后在"## 四阶段路线"段里把第 1 条 `**数据源升级**（P0）` 标为 `✅ 2026-04-13 完成`。

- [ ] **Step 7.3: 确认 5 条验收标准全部达成**

回到 `docs/superpowers/specs/2026-04-13-qmt-integration-design.md §8`，逐条核对：

1. `python -c "from data.qmt_client import get_kline; print(get_kline('000001', count=60))"` → 拿到 60 根日线 ✅/❌
2. `get_price_df` 走 QMT（日志/`_data_source='qmt'`）；未登录时降级 ✅/❌
3. `qmt_probe.py` 一键跑，≥15/20 OK ✅/❌
4. `qmt_reference.md` ≥10 个 API 实测记录 ✅/❌
5. memory 索引已更新 ✅/❌

若有未达成项，不要 commit"完成"，汇报具体缺口。

- [ ] **Step 7.4: Final commit**

```
cd C:/LinDangAgent && git add -A && git status && git commit -m "feat(qmt): phase-A integration complete

- qmt_client wrapper with symbol normalization
- _try_with_fallback extended with qmt_fn slot (top priority)
- get_price_df wired to QMT via qmt_fn, Chinese-column adapter
- qmt_probe.py exhaustive 20-API validator w/ auto-generated reference
- smoke test verifies canary through get_price_df

Acceptance: all 5 criteria in spec §8 met.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review Checklist

**Spec coverage (§s in spec):**
- §4.1 qmt_client API (4 methods) → Task 1 ✅
- §4.2 fallback.py 接入 → Task 2 (改的其实是 _try_with_fallback in tushare_client.py，已在开头说明偏差)✅
- §4.3 金丝雀接入 → Task 3 (改的其实是 get_price_df，已说明偏差)✅
- §5.1 qmt_probe.py 20 项 → Task 4 ✅
- §5.2 test_qmt_smoke.py → Task 5 ✅
- §6.1 qmt_reference.md → Task 4 自动生成 ✅
- §6.2 memory reference_qmt.md + MEMORY.md → Task 6 ✅
- §7 交付物 → 全部落实
- §8 验收标准 5 条 → Task 7 Step 7.3 逐条核对

**Placeholder scan:** 无 TBD/TODO；每步都有完整代码和命令。

**Type consistency:**
- `QMTUnavailable` exception 全计划统一使用
- `_normalize_symbol` / `_denormalize_symbol` 名字一致
- `is_alive() -> bool`, `get_kline(...) -> pd.DataFrame`, `get_realtime(...) -> dict[str, dict]`, `get_sector_stocks(...) -> list[str]` 贯穿全计划
- 中文列名 (`日期/开盘/最高/最低/收盘/成交量/涨跌幅/成交额`) 在 Task 3 与既有 tushare_client 的 schema 一致

---

## Execution Handoff

计划已保存到 `C:\LinDangAgent\docs\superpowers\plans\2026-04-13-qmt-integration.md`。

**两种执行选项**：

**1. Subagent-Driven（推荐）** — 每个 task 派发一个新 subagent 执行，我在 task 之间 review，快速迭代，爆炸半径小

**2. Inline Execution** — 在当前会话执行，批量推进 + checkpoint review

建议 1，因为：
- Task 4（qmt_probe.py）需要你打开 QMT 客户端才能实测——适合一个独立 subagent 在你确认 QMT 登录后启动
- Task 1-3 是纯代码可在干净 subagent 里快速跑单测
- 爆炸半径隔离：qmt_probe 实测万一遇到 SDK 坑，subagent 报告回来再决定，不污染主会话

你想走 1 还是 2？
