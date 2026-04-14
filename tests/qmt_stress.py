"""
QMT 重构前压测：针对"即将用 QMT 重构单股分析+夜间学习"场景做窄而深验证。

用法:
    python tests/qmt_stress.py

产出:
    - 控制台报告
    - docs/qmt_stress_report_YYYYMMDD_HHMMSS.md
    - docs/qmt_reference.md 末尾追加 Stress Findings 段
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

REPORT_DIR = ROOT / "docs"
REFERENCE_MD = ROOT / "docs" / "qmt_reference.md"


# ── 辅助 ──────────────────────────────────────────────────────
def _dl(xtdata, sym, period="1d"):
    try:
        xtdata.download_history_data(sym, period=period, start_time="", end_time="")
    except Exception:
        pass


def _safe(fn, default=None):
    try:
        return fn()
    except Exception as e:
        return f"ERR: {type(e).__name__}: {e}"


def _kline_rows(xtdata, sym, period="1d", count=30):
    _dl(xtdata, sym, period)
    data = xtdata.get_market_data_ex(
        field_list=["time", "open", "high", "low", "close", "volume"],
        stock_list=[sym], period=period, count=count,
    )
    df = data.get(sym, pd.DataFrame())
    return len(df), df


# ── Scenario 1: 特殊标的兼容性 ─────────────────────────────────
SPECIAL_SYMBOLS = {
    # (symbol_with_suffix, label, expect_klines)
    "normal_sz": ("000001.SZ", "平安银行（主板深）", True),
    "normal_sh": ("600036.SH", "招商银行（主板沪）", True),
    "chinext":   ("300750.SZ", "宁德时代（创业板）", True),
    "star":      ("688981.SH", "中芯国际（科创板）", True),
    "bse_43":    ("430300.BJ", "北交所430段（或尝试 832145）", True),
    "bse_83":    ("833454.BJ", "北交所833段", True),
    "etf_sh":    ("510300.SH", "沪深300ETF", True),
    "etf_sz":    ("159915.SZ", "创业板ETF", True),
    "index":     ("000300.SH", "沪深300指数", True),
}


def scenario_1_special_symbols(xtdata):
    """各类特殊标的 K 线 + 元信息 + 类型"""
    report = ["## Scenario 1: 特殊标的兼容性\n"]
    findings = []
    for key, (sym, label, _) in SPECIAL_SYMBOLS.items():
        report.append(f"### {label} `{sym}`")
        rows, df = _safe(lambda: _kline_rows(xtdata, sym, "1d", 30), default=(0, pd.DataFrame()))
        if isinstance(rows, str):
            report.append(f"- K线: {rows}")
        else:
            report.append(f"- K线 30根: rows={rows}  最后一根={df.iloc[-1].to_dict() if rows else 'EMPTY'}")
            if rows == 0:
                findings.append(f"- `{sym}` ({label}) K线返回空 → 可能需要特殊处理")

        detail = _safe(lambda: xtdata.get_instrument_detail(sym))
        if isinstance(detail, dict):
            keys = list(detail.keys())
            report.append(f"- instrument_detail: {len(keys)} 字段，前10={keys[:10]}")
            # Show VALUE of common status fields we expect
            status_fields = [k for k in keys if any(
                p in k.lower() for p in ("status", "suspend", "st", "instrumentstatus", "trade", "listdate", "delist")
            )]
            for f in status_fields[:5]:
                report.append(f"  - `{f}` = {detail[f]}")
        else:
            report.append(f"- instrument_detail: {detail}")

        itype = _safe(lambda: xtdata.get_instrument_type(sym))
        report.append(f"- instrument_type: {itype}")
        report.append("")

    return "\n".join(report), findings


# ── Scenario 2: 财务全表深挖 ──────────────────────────────────
FINANCIAL_TABLES = [
    "Balance", "Income", "CashFlow",
    "PershareIndex", "CapitalStructure", "HolderNum",
    "TopTenHolder", "TopTenHolderFree",
]


def scenario_2_financial_tables(xtdata):
    report = ["## Scenario 2: 财务数据全表\n\n- symbol: 000001.SZ"]
    findings = []
    sym = "000001.SZ"
    for table in FINANCIAL_TABLES:
        report.append(f"\n### 表 `{table}`")
        fin = _safe(lambda: xtdata.get_financial_data([sym], table_list=[table]))
        if isinstance(fin, str):
            report.append(f"- {fin}")
            findings.append(f"- 财务表 `{table}` 失败: {fin}")
            continue
        if not fin or sym not in fin:
            report.append(f"- 返回空: {fin}")
            findings.append(f"- 财务表 `{table}` 返回空或缺 key")
            continue
        sub = fin[sym]
        # sub 可能是 dict[table_name, DataFrame] 或直接 DataFrame
        if isinstance(sub, dict):
            for k, v in sub.items():
                if isinstance(v, pd.DataFrame):
                    report.append(f"- `{k}`: rows={len(v)}, cols={list(v.columns)[:15]}")
                    if not v.empty:
                        report.append(f"  - 首行样例: {v.iloc[0].to_dict()}")
                else:
                    report.append(f"- `{k}`: {type(v).__name__} = {str(v)[:200]}")
        elif isinstance(sub, pd.DataFrame):
            report.append(f"- DataFrame rows={len(sub)}, cols={list(sub.columns)[:15]}")
            if not sub.empty:
                report.append(f"  - 首行: {sub.iloc[0].to_dict()}")
        else:
            report.append(f"- 返回类型={type(sub).__name__}: {str(sub)[:200]}")

    return "\n".join(report), findings


# ── Scenario 3: 板块全景 ───────────────────────────────────────
HOT_CONCEPT_GUESSES = [
    "锂电池", "CPO", "人工智能", "光伏", "新能源车", "消费电子",
    "半导体", "白酒", "医疗器械", "军工", "房地产", "银行",
]


def scenario_3_sector_landscape(xtdata):
    report = ["## Scenario 3: 板块全景\n"]
    findings = []

    sectors = _safe(lambda: xtdata.get_sector_list())
    if isinstance(sectors, str):
        report.append(f"- get_sector_list: {sectors}")
        findings.append(f"- get_sector_list 失败: {sectors}")
        return "\n".join(report), findings

    report.append(f"### 全部板块名（{len(sectors)}个）\n")
    report.append("```")
    for s in sectors:
        report.append(s)
    report.append("```\n")

    # 热门概念板是否存在
    report.append("### 热门概念覆盖检查")
    for concept in HOT_CONCEPT_GUESSES:
        # 完全匹配
        exact = concept in sectors
        # 模糊匹配（包含关键词）
        fuzzy = [s for s in sectors if concept in s]
        report.append(f"- `{concept}`: exact={exact}, 模糊匹配={fuzzy[:5]}")
        if not fuzzy:
            findings.append(f"- 概念板块 `{concept}` 在 QMT 板块列表中完全缺失")

    # 对每个匹配到的概念，拉一次成分数量
    report.append("\n### 前 3 个匹配板块的成分数量抽样")
    tried = 0
    for concept in HOT_CONCEPT_GUESSES:
        if tried >= 3:
            break
        fuzzy = [s for s in sectors if concept in s]
        if fuzzy:
            target = fuzzy[0]
            stocks = _safe(lambda: xtdata.get_stock_list_in_sector(target))
            if isinstance(stocks, list):
                report.append(f"- `{target}`: {len(stocks)} 只, 前5={stocks[:5]}")
                tried += 1
            else:
                report.append(f"- `{target}`: {stocks}")

    return "\n".join(report), findings


# ── Scenario 4: 批量性能基准 ───────────────────────────────────
def scenario_4_batch_perf(xtdata):
    report = ["## Scenario 4: 批量性能基准（关键！决定夜间学习可行性）\n"]
    findings = []

    # 从沪深A股池取样
    pool = _safe(lambda: xtdata.get_stock_list_in_sector("沪深A股"))
    if not isinstance(pool, list) or len(pool) < 1000:
        report.append(f"- 获取股票池失败: {pool}")
        findings.append("- 无法获取沪深A股池，跳过批量压测")
        return "\n".join(report), findings

    sizes = [10, 100, 500, 1000]
    for n in sizes:
        sample = pool[:n]
        report.append(f"\n### 批量 N={n} 只，取近 60 日日线")

        # 4a. 批量 download
        t0 = time.time()
        try:
            xtdata.download_history_data2(sample, period="1d", start_time="", end_time="", callback=None)
            dl_cost = int((time.time() - t0) * 1000)
            report.append(f"- download_history_data2 批量下载: {dl_cost}ms ({dl_cost/n:.1f}ms/只)")
        except Exception as e:
            # 回退到单只循环下载
            t0 = time.time()
            for s in sample:
                _dl(xtdata, s, "1d")
            dl_cost = int((time.time() - t0) * 1000)
            report.append(f"- 单只循环下载: {dl_cost}ms ({dl_cost/n:.1f}ms/只)  [download_history_data2 不可用: {e}]")

        # 4b. get_market_data_ex 批量
        t0 = time.time()
        try:
            data = xtdata.get_market_data_ex(
                field_list=["time", "open", "high", "low", "close", "volume"],
                stock_list=sample, period="1d", count=60,
            )
            mde_cost = int((time.time() - t0) * 1000)
            nonempty = sum(1 for s in sample if s in data and not data[s].empty)
            report.append(f"- get_market_data_ex 批量取 60 日: {mde_cost}ms, 有数据/总数 = {nonempty}/{n}")
        except Exception as e:
            report.append(f"- get_market_data_ex 批量失败: {e}")
            findings.append(f"- N={n} get_market_data_ex 批量失败")
            continue

        # 4c. get_local_data 对比（若存在）
        if hasattr(xtdata, "get_local_data"):
            t0 = time.time()
            try:
                local = xtdata.get_local_data(
                    field_list=["time", "open", "high", "low", "close", "volume"],
                    stock_list=sample, period="1d", count=60,
                )
                local_cost = int((time.time() - t0) * 1000)
                local_nonempty = sum(1 for s in sample if s in local and not local[s].empty) if local else 0
                report.append(f"- get_local_data 批量取 60 日: {local_cost}ms, 有数据/总数 = {local_nonempty}/{n}")
                if mde_cost > 0:
                    report.append(f"  → 相对 get_market_data_ex 提速 {mde_cost/max(local_cost,1):.1f}x")
            except Exception as e:
                report.append(f"- get_local_data 失败: {e}")

    # 批量含非法 symbol 的错误处理
    report.append("\n### 批量含非法 symbol")
    dirty = pool[:5] + ["999999.XX", "888888.YY"]
    try:
        data = xtdata.get_market_data_ex(
            field_list=["time", "close"], stock_list=dirty, period="1d", count=5,
        )
        report.append(f"- 5 正 + 2 非法，返回 keys: {list(data.keys())}")
        report.append(f"- 非法 symbol 行为: '999999.XX' in data = {'999999.XX' in data}, empty={data.get('999999.XX', pd.DataFrame()).empty if '999999.XX' in data else 'N/A'}")
    except Exception as e:
        report.append(f"- 批量含非法 symbol 抛异常: {e}")
        findings.append(f"- 批量含非法 symbol 导致整批异常: {e}")

    return "\n".join(report), findings


# ── Scenario 5: 复权一致性 ─────────────────────────────────────
def scenario_5_adjustment_consistency(xtdata):
    report = ["## Scenario 5: 复权一致性\n"]
    findings = []

    # 选今年有过除权的股票（用 get_divid_factors 筛）
    candidates = ["002594.SZ", "300750.SZ", "000651.SZ", "600519.SH", "000858.SZ"]

    for sym in candidates:
        report.append(f"\n### `{sym}`")
        _dl(xtdata, sym, "1d")

        try:
            factors = xtdata.get_divid_factors(sym) if hasattr(xtdata, "get_divid_factors") else None
            if factors is None:
                report.append("- get_divid_factors 接口不存在")
            else:
                if isinstance(factors, pd.DataFrame):
                    report.append(f"- 除权因子: rows={len(factors)}, cols={list(factors.columns)}")
                    if not factors.empty:
                        report.append(f"  - 最近3条: {factors.tail(3).to_dict(orient='records')}")
                else:
                    report.append(f"- 除权因子: {type(factors).__name__} = {str(factors)[:200]}")
        except Exception as e:
            report.append(f"- get_divid_factors 失败: {e}")

        # 三种复权比较最近 5 日收盘价
        closes = {}
        for adj in ("none", "front", "back"):
            try:
                data = xtdata.get_market_data_ex(
                    ["time", "close"], [sym], period="1d", count=5, dividend_type=adj,
                )
                if sym in data and not data[sym].empty:
                    closes[adj] = [float(x) for x in data[sym]["close"].tolist()]
                else:
                    closes[adj] = None
            except Exception as e:
                closes[adj] = f"ERR: {e}"

        report.append(f"- 近5日 close 三种复权对比: {json.dumps(closes, default=str, ensure_ascii=False)}")

        # 判断三者是否一致
        if all(isinstance(closes[a], list) for a in ("none", "front", "back")):
            if closes["none"] == closes["front"] == closes["back"]:
                report.append("  → 三者完全相同（近5日未跨除权日，正常）")
            else:
                report.append("  → 三者存在差异（近5日跨除权日）")

    return "\n".join(report), findings


# ── 主流程 ─────────────────────────────────────────────────────
def main():
    print("======== QMT Stress Test @ " + dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " ========\n")
    from xtquant import xtdata

    all_sections = []
    all_findings = []

    scenarios = [
        ("Scenario 1: 特殊标的", scenario_1_special_symbols),
        ("Scenario 2: 财务全表", scenario_2_financial_tables),
        ("Scenario 3: 板块全景", scenario_3_sector_landscape),
        ("Scenario 4: 批量性能", scenario_4_batch_perf),
        ("Scenario 5: 复权一致性", scenario_5_adjustment_consistency),
    ]

    for name, fn in scenarios:
        print(f"\n>>> 运行 {name}...")
        t0 = time.time()
        try:
            section, findings = fn(xtdata)
            all_sections.append(section)
            all_findings.extend(findings)
            cost = int((time.time() - t0) * 1000)
            print(f"    OK, {cost}ms, findings={len(findings)}")
        except Exception as e:
            err = f"## {name}\n\n**FATAL**: {type(e).__name__}: {e}\n\n```\n{traceback.format_exc()}\n```"
            all_sections.append(err)
            all_findings.append(f"- {name} FATAL: {e}")
            print(f"    FATAL {e}")

    # 写完整报告
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = REPORT_DIR / f"qmt_stress_report_{ts}.md"

    full_report = [f"# QMT Stress Test Report\n\n*Generated: {dt.datetime.now():%Y-%m-%d %H:%M:%S}*\n"]
    if all_findings:
        full_report.append("\n## Critical Findings\n")
        full_report.extend(all_findings)
    full_report.append("\n---\n")
    full_report.extend(all_sections)
    report_path.write_text("\n".join(full_report), encoding="utf-8")
    print(f"\n→ Report saved: {report_path.relative_to(ROOT)}")

    # Append to qmt_reference.md
    if REFERENCE_MD.exists():
        existing = REFERENCE_MD.read_text(encoding="utf-8")
        append_section = f"\n\n---\n\n## Stress Findings ({dt.datetime.now():%Y-%m-%d})\n\n"
        if all_findings:
            append_section += "### Findings requiring attention\n"
            append_section += "\n".join(all_findings) + "\n\n"
        append_section += f"### Full stress report\nSee `docs/qmt_stress_report_{ts}.md` for complete details.\n"
        REFERENCE_MD.write_text(existing + append_section, encoding="utf-8")
        print(f"→ Appended findings to: {REFERENCE_MD.relative_to(ROOT)}")

    print(f"\n======== Stress Test Done: {len(all_findings)} findings ========")
    # Print first 20 findings to console
    if all_findings:
        print("\nTop findings:")
        for f in all_findings[:20]:
            print(f"  {f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
