"""
QMT 深度重审 —— 纠正 Task 4/8 的遗漏和误判

此脚本聚焦 Task 4 probe / Task 8 stress test 漏掉或错判的 API。
"""
from __future__ import annotations
import datetime as dt
import json
import sys
import time
import traceback
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _safe(fn, label=""):
    try:
        t0 = time.time()
        result = fn()
        return ("OK", int((time.time() - t0) * 1000), result)
    except Exception as e:
        return ("ERR", int((time.time() - t0) * 1000), f"{type(e).__name__}: {e}")


def main():
    from xtquant import xtdata
    report = [f"# QMT Deep Audit @ {dt.datetime.now():%Y-%m-%d %H:%M:%S}\n"]
    findings = []

    sym = "000001.SZ"

    # ── A. 之前误判的 API 复测 ─────────────────────────────
    report.append("## A. 复测之前误判 / 遗漏的 API\n")

    # A1. get_instrument_detail 加 iscomplete=True
    report.append("### A1. get_instrument_detail(iscomplete=True)")
    for s in ["000001.SZ", "430300.BJ", "833454.BJ", "510300.SH", "000300.SH"]:
        status, cost, result = _safe(lambda: xtdata.get_instrument_detail(s, iscomplete=True))
        if status == "OK" and isinstance(result, dict):
            keys = list(result.keys())
            report.append(f"- `{s}`: {cost}ms, 字段数={len(keys)}, 前8={keys[:8]}")
            # ST 相关字段
            for k in ("InstrumentStatus", "InstrumentName", "UpStopPrice", "PreClose", "IsTrading"):
                if k in result:
                    report.append(f"  - {k}={result[k]}")
        else:
            report.append(f"- `{s}`: FAILED: {result}")

    # A2. get_instrument_detail_list (批量版)
    report.append("\n### A2. get_instrument_detail_list (批量版，对比单只循环)")
    test_syms = ["000001.SZ", "600036.SH", "300750.SZ", "688981.SH", "510300.SH"]
    status, cost, result = _safe(
        lambda: xtdata.get_instrument_detail_list(test_syms, iscomplete=True)
    )
    if status == "OK":
        report.append(f"- 批量 {len(test_syms)} 只: {cost}ms, 返回 keys={list(result.keys()) if isinstance(result, dict) else type(result).__name__}")
        if isinstance(result, dict):
            for s in test_syms[:2]:
                if s in result:
                    d = result[s]
                    report.append(f"  - `{s}`: 字段数={len(d) if isinstance(d, dict) else 'N/A'}")
    else:
        report.append(f"- 失败: {result}")

    # A3. download_his_st_data + ST 查询
    report.append("\n### A3. download_his_st_data (历史 ST 数据)")
    status, cost, result = _safe(lambda: xtdata.download_his_st_data())
    report.append(f"- download: {status}, {cost}ms, result={str(result)[:120]}")

    # 下载后看有没有查询接口
    # 可能通过 get_instrument_detail 查 ST 状态
    for st_sym in ["600225.SH", "000333.SZ"]:  # 尝试一些可能的 ST
        status, cost, result = _safe(lambda: xtdata.get_instrument_detail(st_sym, iscomplete=True))
        if status == "OK" and isinstance(result, dict):
            name = result.get("InstrumentName", "")
            st_status = result.get("InstrumentStatus", "")
            report.append(f"  - `{st_sym}` name={name}, InstrumentStatus={st_status}")

    # A4. 前复权重测（之前发现 front==none）
    report.append("\n### A4. 前复权 dividend_type 重测（002594.SZ 比亚迪，有过除权）")
    for adj in ("none", "front", "back"):
        status, cost, result = _safe(
            lambda: xtdata.get_market_data_ex(
                ["time", "close"], ["002594.SZ"], period="1d", count=500,
                dividend_type=adj,
            )
        )
        if status == "OK":
            df = result.get("002594.SZ", pd.DataFrame())
            if not df.empty:
                first_close = float(df.iloc[0]["close"])
                last_close = float(df.iloc[-1]["close"])
                report.append(f"  - dividend_type='{adj}': 首 close={first_close:.2f}, 尾 close={last_close:.2f}, rows={len(df)}")

    # A5. 概念板——get_sector_info 能返回什么？
    report.append("\n### A5. get_sector_info (结构化板块信息)")
    for concept in ["锂电池", "半导体", "消费电子", "白酒", ""]:
        status, cost, result = _safe(lambda: xtdata.get_sector_info(concept))
        if status == "OK":
            if isinstance(result, dict) and result:
                report.append(f"  - `{concept}`: {cost}ms, keys={list(result.keys())[:10]}")
            elif result:
                report.append(f"  - `{concept}`: {cost}ms, type={type(result).__name__}, len/val={len(result) if hasattr(result, '__len__') else result}")
            else:
                report.append(f"  - `{concept}`: {cost}ms, EMPTY {result!r}")
        else:
            report.append(f"  - `{concept}`: ERROR: {result}")

    # A6. download_sector_data 后能否看到更多板块
    report.append("\n### A6. download_sector_data 后 get_sector_list 变化")
    status, cost, result = _safe(lambda: xtdata.download_sector_data())
    report.append(f"- download_sector_data: {status}, {cost}ms")
    status, cost, result = _safe(lambda: xtdata.get_sector_list())
    if status == "OK" and isinstance(result, list):
        report.append(f"- 下载后 get_sector_list: {len(result)} 个板块")
        report.append(f"  - 前 20: {result[:20]}")
        # 概念板在不在？
        hot = [s for s in result if any(k in s for k in ("锂", "电池", "芯片", "半导体", "CPO", "AI", "人工智能", "光伏", "白酒", "军工", "银行", "医药"))]
        report.append(f"  - 疑似概念板命中: {hot[:20]}")
        if hot:
            findings.append(f"✅ 下载 sector_data 后概念板可用，命中 {len(hot)} 个热门关键词")

    # ── B. 重要遗漏 API 探索 ──────────────────────────────
    report.append("\n## B. 重要遗漏 API 初探\n")

    # B1. get_main_contract（虽然是期货，但看看返回 schema）
    # skip

    # B2. get_full_kline
    report.append("### B2. get_full_kline（最新 K 线全推）")
    status, cost, result = _safe(
        lambda: xtdata.get_full_kline(["000001.SZ"], period="1d")
        if hasattr(xtdata, "get_full_kline") else None
    )
    if status == "OK":
        report.append(f"- cost={cost}ms, type={type(result).__name__}")
        if isinstance(result, dict):
            report.append(f"  - keys={list(result.keys())[:3]}")
            for k, v in list(result.items())[:1]:
                report.append(f"  - sample {k}: {str(v)[:200]}")

    # B3. get_l2_quote（盘后可能为空，但验证 API 签名）
    report.append("\n### B3. Level 2 行情 (get_l2_quote)")
    try:
        l2 = xtdata.get_l2_quote(
            field_list=["time", "askPrice", "bidPrice"],
            stock_code="000001.SZ",
            start_time="", end_time="", count=1,
        )
        report.append(f"- get_l2_quote result type={type(l2).__name__}, len={len(l2) if hasattr(l2,'__len__') else 'N/A'}")
    except Exception as e:
        report.append(f"- get_l2_quote error: {type(e).__name__}: {e}")

    # B4. get_broker_queue_data
    report.append("\n### B4. get_broker_queue_data (券商队列大单)")
    try:
        bq = xtdata.get_broker_queue_data(
            stock_list=["000001.SZ"], start_time="", end_time="", count=5,
        )
        report.append(f"- type={type(bq).__name__}")
        if isinstance(bq, dict):
            report.append(f"  - keys={list(bq.keys())}")
    except Exception as e:
        report.append(f"- error: {type(e).__name__}: {e}")

    # B5. get_transactioncount
    report.append("\n### B5. get_transactioncount (大单统计)")
    try:
        tc = xtdata.get_transactioncount(["000001.SZ"])
        report.append(f"- type={type(tc).__name__}, sample={str(tc)[:300]}")
    except Exception as e:
        report.append(f"- error: {type(e).__name__}: {e}")

    # B6. get_divid_factors 重测
    report.append("\n### B6. get_divid_factors (除权因子，复权自建用)")
    try:
        df = xtdata.get_divid_factors("002594.SZ")
        if isinstance(df, pd.DataFrame):
            report.append(f"- shape={df.shape}, cols={list(df.columns)}")
            if not df.empty:
                report.append(f"  - 最近3条: {df.tail(3).to_dict(orient='records')}")
    except Exception as e:
        report.append(f"- error: {type(e).__name__}: {e}")

    # B7. get_metatable_list
    report.append("\n### B7. get_metatable_list (元数据表)")
    try:
        ml = xtdata.get_metatable_list()
        report.append(f"- type={type(ml).__name__}, count={len(ml) if hasattr(ml,'__len__') else 'N/A'}")
        if isinstance(ml, (list, tuple)):
            report.append(f"  - 前30: {list(ml)[:30]}")
        elif isinstance(ml, dict):
            report.append(f"  - keys前30: {list(ml.keys())[:30]}")
    except Exception as e:
        report.append(f"- error: {type(e).__name__}: {e}")

    # B8. compute_coming_trading_calendar
    report.append("\n### B8. compute_coming_trading_calendar (未来交易日)")
    try:
        cal = xtdata.compute_coming_trading_calendar("SH", start_time="20260414", end_time="20260501")
        report.append(f"- type={type(cal).__name__}, sample={str(cal)[:300]}")
    except Exception as e:
        report.append(f"- error: {type(e).__name__}: {e}")

    # B9. bnd_get_call_info / conversion_price (选一个有 CB 的股票)
    report.append("\n### B9. bnd_get_conversion_price (可转债转股价)")
    # 找一个有可转债的股票 — 试宁德时代？或尝试几个
    for cb_sym in ["123070.SZ", "113553.SH", "128123.SZ"]:
        try:
            r = xtdata.bnd_get_conversion_price(cb_sym, start_time="20240101", end_time="20260414")
            if isinstance(r, pd.DataFrame) and not r.empty:
                report.append(f"- `{cb_sym}`: shape={r.shape}, cols={list(r.columns)}")
                break
            else:
                report.append(f"- `{cb_sym}`: {type(r).__name__}, {r if not hasattr(r,'empty') else 'empty'}")
        except Exception as e:
            report.append(f"- `{cb_sym}`: error: {type(e).__name__}: {e}")

    # B10. get_trading_dates / get_trading_calendar
    report.append("\n### B10. get_trading_dates (交易日列表)")
    try:
        dates = xtdata.get_trading_dates("SH", start_time="20260101", end_time="20260414", count=-1)
        report.append(f"- 2026-01-01 to 2026-04-14: {len(dates) if hasattr(dates,'__len__') else '?'} 交易日")
        if hasattr(dates, "__len__") and len(dates):
            report.append(f"  - 前3: {dates[:3]}, 后3: {dates[-3:]}")
    except Exception as e:
        report.append(f"- error: {type(e).__name__}: {e}")

    # ── 汇总 ──────────────────────────────────────────────
    report.append("\n## 关键 Findings\n")
    for f in findings:
        report.append(f"- {f}")

    out = "\n".join(report)
    print(out)

    # 写报告
    (ROOT / "docs").mkdir(parents=True, exist_ok=True)
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    (ROOT / "docs" / f"qmt_audit_report_{ts}.md").write_text(out, encoding="utf-8")
    print(f"\n报告保存到: docs/qmt_audit_report_{ts}.md")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"FATAL: {e}")
        traceback.print_exc()
        sys.exit(1)
