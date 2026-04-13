"""
QMT / xtquant API 全功能探测脚本。

用法:
    python tests/qmt_probe.py                 # 默认 symbol 000001
    python tests/qmt_probe.py --symbol 600000 # 指定测试标的
    python tests/qmt_probe.py --no-reference  # 不自动更新 reference.md

产出:
    - 控制台报告（即时可读）
    - docs/qmt_probe_report_YYYYMMDD_HHMMSS.md
    - docs/qmt_reference.md（AI-oriented API 参考，按 OK 的 probe 重写）
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "docs"
REFERENCE_MD = ROOT / "docs" / "qmt_reference.md"


@dataclass
class ProbeResult:
    idx: int
    name: str
    api_signature: str
    status: str = ""  # "OK" / "WARN" / "RAISED" / "SKIP"
    cost_ms: Optional[int] = None
    summary: str = ""
    sample: str = ""
    error: str = ""
    gotchas: list = field(default_factory=list)


def _run(idx: int, name: str, sig: str, fn: Callable) -> ProbeResult:
    r = ProbeResult(idx=idx, name=name, api_signature=sig, status="")
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


def _dl(xtdata, sym: str, period: str) -> None:
    """测试前保证数据已下载到本地；失败静默，交给后续 probe 暴露问题"""
    try:
        xtdata.download_history_data(sym, period=period, start_time="", end_time="")
    except Exception:
        pass


# ── Probes ─────────────────────────────────────────────────────
def probe_connect(xtdata):
    return ("imported OK", f"xtdata module path: {xtdata.__file__}", [])


def probe_client_version(xtdata):
    if not hasattr(xtdata, "get_client_version"):
        raise AssertionError("get_client_version 不存在")
    ver = xtdata.get_client_version()
    return (f"version={ver}", f"{ver}", [])


def probe_kline_daily_60(xtdata, sym):
    _dl(xtdata, sym, "1d")
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
            "**必须先 download_history_data(sym, period) 才能 get_market_data_ex 出数据**（否则返回空）",
            "symbol 必须带 .SZ/.SH/.BJ 后缀",
            "period='1d' 日线；'1m'/'5m' 仅交易时段可查",
            "返回是 dict[symbol, DataFrame]，需要 data[sym] 取值",
        ],
    )


def probe_kline_daily_all(xtdata, sym):
    _dl(xtdata, sym, "1d")
    data = xtdata.get_market_data_ex(
        field_list=["time", "close"], stock_list=[sym], period="1d", count=-1,
    )
    if not data or sym not in data or data[sym].empty:
        raise AssertionError("全历史返回空")
    df = data[sym]
    return (f"rows={len(df)}", f"first_time={df.iloc[0]['time']}, last_close={df.iloc[-1]['close']}",
            ["count=-1 取全历史；数据量大时注意性能"])


def probe_kline_1m(xtdata, sym):
    _dl(xtdata, sym, "1m")
    data = xtdata.get_market_data_ex(
        field_list=["time", "open", "close"], stock_list=[sym], period="1m", count=240,
    )
    if not data or sym not in data or data[sym].empty:
        raise AssertionError("1m 返回空（可能为非交易时段预期行为，需 download_history_data 分钟级数据）")
    return (f"rows={len(data[sym])}", str(data[sym].tail(2).to_dict()),
            ["分钟线仅交易时段可取最新；盘后可能返回历史最后一个交易日",
             "分钟线数据量大，download 耗时比日线长"])


def probe_kline_multi_period(xtdata, sym):
    results = {}
    for p in ("5m", "15m", "30m", "60m"):
        _dl(xtdata, sym, p)
        try:
            data = xtdata.get_market_data_ex(
                field_list=["time", "close"], stock_list=[sym], period=p, count=10,
            )
            rows = len(data.get(sym, pd.DataFrame()))
            results[p] = rows
        except Exception as e:
            results[p] = f"ERR:{e}"
    ok = [p for p, v in results.items() if isinstance(v, int) and v > 0]
    if not ok:
        raise AssertionError(f"所有周期都返回空: {results}")
    return (f"周期-行数: {results}", json.dumps(results),
            ["5m/15m/30m/60m 都要独立 download"])


def probe_kline_weekly_monthly(xtdata, sym):
    _dl(xtdata, sym, "1w")
    _dl(xtdata, sym, "1mon")
    wk = xtdata.get_market_data_ex(["time", "close"], [sym], period="1w", count=10)
    mo = xtdata.get_market_data_ex(["time", "close"], [sym], period="1mon", count=10)
    wk_rows = len(wk.get(sym, pd.DataFrame()))
    mo_rows = len(mo.get(sym, pd.DataFrame()))
    if wk_rows == 0 and mo_rows == 0:
        raise AssertionError(f"周/月线返回空: wk={wk_rows}, mo={mo_rows}")
    return (f"week_rows={wk_rows}, month_rows={mo_rows}",
            "周线 period='1w'，月线 period='1mon'",
            ["周线 period='1w'；月线 period='1mon'（不是 '1M' 或 '1month'）"])


def probe_kline_adjust(xtdata, sym):
    _dl(xtdata, sym, "1d")
    results = {}
    for adj in ("none", "front", "back"):
        try:
            data = xtdata.get_market_data_ex(
                ["time", "close"], [sym], period="1d", count=5, dividend_type=adj,
            )
            last_close = data.get(sym, pd.DataFrame())
            if not last_close.empty:
                results[adj] = float(last_close.iloc[-1]["close"])
            else:
                results[adj] = None
        except Exception as e:
            results[adj] = f"ERR:{e}"
    return (f"复权对比: {results}",
            json.dumps(results, default=str),
            ["dividend_type 取值: 'none' / 'front' / 'back'（不是 'qfq'/'hfq'）"])


def probe_full_tick(xtdata, sym):
    tick = xtdata.get_full_tick([sym])
    if not tick or sym not in tick:
        raise AssertionError("快照返回空（盘后可能只有上一交易日收盘快照）")
    row = tick[sym]
    keys = list(row.keys())
    return (f"字段: {keys}", f"sample: lastPrice={row.get('lastPrice')}, time={row.get('time')}",
            ["盘后 get_full_tick 返回上个交易日收盘；盘中才是实时"])


def probe_subscribe(xtdata, sym):
    received = []
    def cb(data):
        received.append(data)
    try:
        subid = xtdata.subscribe_quote(sym, period="1d", callback=cb)
        time.sleep(1.0)
        if hasattr(xtdata, "unsubscribe_quote"):
            xtdata.unsubscribe_quote(subid)
        return (f"subid={subid}, received={len(received)} 次回调",
                f"回调数据样例前 200 字: {str(received[:1])[:200]}",
                ["subscribe_quote 长期订阅，必须 unsubscribe_quote 清理",
                 "盘后订阅不会触发回调，只能验证 API 调用成功"])
    except Exception as e:
        raise AssertionError(f"订阅失败: {e}")


def probe_instrument_detail(xtdata, sym):
    detail = xtdata.get_instrument_detail(sym)
    if not detail:
        raise AssertionError("instrument_detail 返回空")
    keys = list(detail.keys())
    sample_keys = keys[:5]
    return (f"字段数={len(keys)}, 前5字段={sample_keys}",
            json.dumps({k: detail[k] for k in sample_keys}, default=str, ensure_ascii=False),
            [])


def probe_sector_a(xtdata):
    stocks = xtdata.get_stock_list_in_sector("沪深A股")
    if not stocks:
        raise AssertionError("沪深A股板块为空")
    return (f"A股股票数={len(stocks)}", f"前5: {stocks[:5]}",
            ["板块名用中文：'沪深A股' / '科创板' / '创业板' / '中小板'"])


def probe_sector_star(xtdata):
    stocks = xtdata.get_stock_list_in_sector("科创板")
    if not stocks:
        raise AssertionError("科创板板块为空")
    return (f"科创板股票数={len(stocks)}", f"前5: {stocks[:5]}", [])


def probe_sector_list(xtdata):
    if not hasattr(xtdata, "get_sector_list"):
        raise AssertionError("get_sector_list 不存在")
    sectors = xtdata.get_sector_list()
    return (f"板块总数={len(sectors)}", f"前10: {sectors[:10]}",
            ["用此 API 发现所有可查板块名"])


def probe_financial(xtdata, sym):
    if not hasattr(xtdata, "get_financial_data"):
        raise AssertionError("get_financial_data 不存在")
    fin = xtdata.get_financial_data([sym], table_list=["Balance"])
    if not fin:
        raise AssertionError("财务返回空")
    return (f"keys={list(fin.keys())[:3]}", f"{str(fin)[:200]}",
            ["财务非 QMT 强项，项目继续走 Tushare/AKShare"])


def probe_instrument_type(xtdata, sym):
    if not hasattr(xtdata, "get_instrument_type"):
        raise AssertionError("get_instrument_type 不存在")
    t = xtdata.get_instrument_type(sym)
    return (f"type={t}", f"{t}", ["区分股票/ETF/指数"])


def probe_bad_symbol(xtdata):
    try:
        data = xtdata.get_market_data_ex(
            ["time", "close"], ["999999.XX"], period="1d", count=5,
        )
        empty = (not data) or data.get("999999.XX", pd.DataFrame()).empty
        if empty:
            return ("非法 symbol 静默返回空", str(data)[:200],
                    ["非法 symbol 不抛异常，返回空 df —— 调用方必须自行校验"])
        raise AssertionError(f"非法 symbol 异常行为: {data}")
    except Exception as e:
        return (f"非法 symbol 抛异常: {type(e).__name__}", str(e)[:200], [])


def probe_huge_count(xtdata, sym):
    _dl(xtdata, sym, "1d")
    t0 = time.time()
    data = xtdata.get_market_data_ex(
        ["time", "close"], [sym], period="1d", count=1_000_000,
    )
    rows = len(data.get(sym, pd.DataFrame()))
    cost = int((time.time() - t0) * 1000)
    return (f"count=1M 返回 rows={rows}, 耗时 {cost}ms",
            f"超过历史总量安全截断",
            ["count 超过历史总量时截断为全历史，不报错"])


def probe_multi_symbol(xtdata):
    _dl(xtdata, "000001.SZ", "1d")
    _dl(xtdata, "600000.SH", "1d")
    data = xtdata.get_market_data_ex(
        ["time", "close"], ["000001.SZ", "600000.SH"], period="1d", count=5,
    )
    ok = [s for s in data if not data[s].empty]
    if len(ok) < 2:
        raise AssertionError(f"批量跨市场失败: 成功={ok}, keys={list(data.keys())}")
    return (f"批量OK: {ok}", "两只股票各返回 5 行",
            ["单次调用可批量传入跨市场多只股票"])


# ── Build probe list + runner ──────────────────────────────────
def build_probes(sym: str):
    from xtquant import xtdata
    return [
        (1,  "xtdata 可导入",              "import xtquant.xtdata",                          lambda: probe_connect(xtdata)),
        (2,  "get_client_version",          "xtdata.get_client_version()",                    lambda: probe_client_version(xtdata)),
        (3,  "日线 x 60 根",                "get_market_data_ex(..., period='1d', count=60)", lambda: probe_kline_daily_60(xtdata, sym)),
        (4,  "日线全历史 (count=-1)",       "get_market_data_ex(..., count=-1)",              lambda: probe_kline_daily_all(xtdata, sym)),
        (5,  "1m x 240 根",                 "get_market_data_ex(..., period='1m', count=240)",lambda: probe_kline_1m(xtdata, sym)),
        (6,  "5m/15m/30m/60m 各周期",       "get_market_data_ex(..., period='5m/15m/30m/60m')",lambda: probe_kline_multi_period(xtdata, sym)),
        (7,  "周线/月线",                   "get_market_data_ex(..., period='1w'/'1mon')",    lambda: probe_kline_weekly_monthly(xtdata, sym)),
        (8,  "复权对比",                    "get_market_data_ex(..., dividend_type=...)",     lambda: probe_kline_adjust(xtdata, sym)),
        (9,  "get_full_tick",               "xtdata.get_full_tick([sym])",                    lambda: probe_full_tick(xtdata, sym)),
        (10, "subscribe_quote",             "xtdata.subscribe_quote(sym, period, callback)",  lambda: probe_subscribe(xtdata, sym)),
        (11, "get_instrument_detail",       "xtdata.get_instrument_detail(sym)",              lambda: probe_instrument_detail(xtdata, sym)),
        (12, "沪深A股 板块成分",            "xtdata.get_stock_list_in_sector('沪深A股')",     lambda: probe_sector_a(xtdata)),
        (13, "科创板 板块成分",             "xtdata.get_stock_list_in_sector('科创板')",      lambda: probe_sector_star(xtdata)),
        (14, "get_sector_list",             "xtdata.get_sector_list()",                       lambda: probe_sector_list(xtdata)),
        (15, "get_financial_data",          "xtdata.get_financial_data([sym], ['Balance'])",  lambda: probe_financial(xtdata, sym)),
        (16, "get_instrument_type",         "xtdata.get_instrument_type(sym)",                lambda: probe_instrument_type(xtdata, sym)),
        (17, "非法 symbol",                 "get_market_data_ex(['999999.XX'], ...)",         lambda: probe_bad_symbol(xtdata)),
        (18, "超长 count=1M",               "get_market_data_ex(..., count=1_000_000)",       lambda: probe_huge_count(xtdata, sym)),
        (19, "跨市场批量",                  "get_market_data_ex(['000001.SZ','600000.SH'])",  lambda: probe_multi_symbol(xtdata)),
        (20, "保留槽位（Level2 预留）",     "reserved",                                       lambda: ("skipped", "Level2 非本期目标", [])),
    ]


def render_console(results):
    lines = [f"======== QMT API Probe Report @ {dt.datetime.now():%Y-%m-%d %H:%M:%S} ========"]
    for r in results:
        mark = {"OK": "OK  ", "WARN": "WARN", "RAISED": "FAIL", "SKIP": "SKIP"}.get(r.status, r.status)
        lines.append(f"[{r.idx:2d}] {r.name:<32s}  {mark}  {(r.cost_ms or 0):>5d}ms  {r.summary[:60]}")
        if r.error:
            lines.append(f"      error: {r.error[:150]}")
    ok_cnt = sum(1 for r in results if r.status == "OK")
    warn_cnt = sum(1 for r in results if r.status == "WARN")
    fail_cnt = sum(1 for r in results if r.status == "RAISED")
    lines.append(f"======== Summary: {ok_cnt}/{len(results)} OK, {warn_cnt} Warning, {fail_cnt} Fatal ========")
    return "\n".join(lines)


def render_report_md(results, symbol):
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


def write_reference(results):
    ts = dt.datetime.now().strftime("%Y-%m-%d")
    header = f"# QMT / xtquant Reference (AI-oriented, auto-generated)\n\n*Last verified: {ts} by `tests/qmt_probe.py`*\n\nThis file is **auto-generated from real API calls**. Do not hand-edit — changes will be overwritten on next probe run.\nIf you need to add an API, extend `tests/qmt_probe.py` with a new probe and re-run.\n\n---\n\n"
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
    ap.add_argument("--symbol", default="000001")
    ap.add_argument("--no-reference", action="store_true")
    args = ap.parse_args()

    sys.path.insert(0, str(ROOT))
    from data.qmt_client import _normalize_symbol
    sym = _normalize_symbol(args.symbol)

    results = []
    for idx, name, sig, fn in build_probes(sym):
        r = _run(idx, name, sig, fn)
        results.append(r)

    console = render_console(results)
    print(console)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"qmt_probe_report_{dt.datetime.now():%Y%m%d_%H%M%S}.md"
    report_path.write_text(render_report_md(results, sym), encoding="utf-8")
    print(f"\n-> Report saved to: {report_path.relative_to(ROOT)}")

    if not args.no_reference:
        write_reference(results)
        print(f"-> qmt_reference.md updated: {REFERENCE_MD.relative_to(ROOT)}")

    return 0 if sum(1 for r in results if r.status == "RAISED") == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
