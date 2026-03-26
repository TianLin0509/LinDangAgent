from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from utils.app_config import get_secret


SH_TZ = ZoneInfo("Asia/Shanghai")
TOP_RESULT_PATTERN = "*.json"
SSE_INDEX_CODE = "000001.SH"

BASE_DIR = Path(__file__).resolve().parent.parent
TOP100_REVIEW_DIR = BASE_DIR / "storage" / "top100_reviews"
CACHE_DIR_CANDIDATES = (
    BASE_DIR / "Stock_top10" / "cache",
    BASE_DIR.parent / "Stock_top10" / "cache",
    BASE_DIR / "temp_stock_top10_stage" / "cache",
)

_tushare_patched = False


@dataclass(frozen=True)
class ReviewCandidate:
    result_file: Path
    generated_at: datetime
    compare_trade_date: str
    result_data: dict


def _make_tushare_pro():
    global _tushare_patched
    import requests as _requests
    import tushare as ts

    token = get_secret("TUSHARE_TOKEN", "")
    url = get_secret("TUSHARE_URL", "http://lianghua.nanyangqiankun.top")
    if not token:
        raise RuntimeError("TUSHARE_TOKEN 未配置")

    ts.set_token(token)
    pro = ts.pro_api(token)
    pro._DataApi__token = token
    pro._DataApi__http_url = url

    if not _tushare_patched:
        orig_post = _requests.post

        def _patched_post(*args, **kwargs):
            kwargs.setdefault("timeout", 15)
            return orig_post(*args, **kwargs)

        _requests.post = _patched_post
        _tushare_patched = True

    return pro


def _iter_result_files() -> list[Path]:
    files: list[Path] = []
    for cache_dir in CACHE_DIR_CANDIDATES:
        if not cache_dir.exists():
            continue
        files.extend(
            path
            for path in cache_dir.glob(TOP_RESULT_PATTERN)
            if "deep_status" not in path.name.lower()
        )
    return sorted(set(files), key=lambda p: p.stat().st_mtime, reverse=True)


def _load_result_file(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_generated_at(path: Path, result_data: dict) -> datetime:
    finished = (result_data or {}).get("finished")
    if finished:
        try:
            return datetime.strptime(str(finished), "%Y-%m-%d %H:%M:%S").replace(tzinfo=SH_TZ)
        except Exception:
            pass
    return datetime.fromtimestamp(path.stat().st_mtime, tz=SH_TZ)


def _get_open_trade_dates(pro, start_date: str, end_date: str) -> list[str]:
    cal = pro.trade_cal(exchange="SSE", start_date=start_date, end_date=end_date)
    if cal is None or cal.empty:
        return []
    cal = cal[cal["is_open"] == 1].sort_values("cal_date")
    return cal["cal_date"].astype(str).tolist()


def _pick_compare_trade_date(generated_at: datetime, open_trade_dates: list[str]) -> str:
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=SH_TZ)

    for trade_date in open_trade_dates:
        close_dt = datetime.combine(
            datetime.strptime(trade_date, "%Y%m%d").date(),
            time(15, 0),
            tzinfo=SH_TZ,
        )
        if close_dt > generated_at:
            return trade_date
    raise RuntimeError("未找到与结果时间匹配的对比交易日")


def _fallback_compare_trade_date(generated_at: datetime, result_data: dict | None = None) -> str:
    result_data = result_data or {}
    explicit = str(result_data.get("compare_trade_date") or "").strip()
    if explicit and re.fullmatch(r"\d{8}", explicit):
        return explicit
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=SH_TZ)
    return generated_at.strftime("%Y%m%d")


def _latest_closed_trade_date(now: datetime, open_trade_dates: list[str]) -> str | None:
    if now.tzinfo is None:
        now = now.replace(tzinfo=SH_TZ)

    latest: str | None = None
    for trade_date in open_trade_dates:
        close_dt = datetime.combine(
            datetime.strptime(trade_date, "%Y%m%d").date(),
            time(15, 0),
            tzinfo=SH_TZ,
        )
        if close_dt <= now:
            latest = trade_date
    return latest


def _select_review_candidate(now: datetime | None = None) -> ReviewCandidate:
    now = now or datetime.now(SH_TZ)
    pro = _make_tushare_pro()
    result_files = _iter_result_files()
    if not result_files:
        raise RuntimeError("未找到 Top100/Top10 结果文件")

    start = (now.date().replace(day=1)).strftime("%Y%m%d")
    end = now.strftime("%Y%m%d")
    open_trade_dates = _get_open_trade_dates(pro, start, end)
    if not open_trade_dates:
        latest_closed_trade_date = now.strftime("%Y%m%d")
    else:
        latest_closed_trade_date = _latest_closed_trade_date(now, open_trade_dates)
    if not latest_closed_trade_date:
        raise RuntimeError("当前还没有可复盘的已收盘交易日")

    eligible: list[ReviewCandidate] = []
    for result_file in result_files:
        result_data = _load_result_file(result_file)
        results = result_data.get("results") or []
        if not results:
            continue

        generated_at = _parse_generated_at(result_file, result_data)
        if open_trade_dates:
            try:
                compare_trade_date = _pick_compare_trade_date(generated_at, open_trade_dates)
            except Exception:
                compare_trade_date = _fallback_compare_trade_date(generated_at, result_data)
        else:
            compare_trade_date = _fallback_compare_trade_date(generated_at, result_data)
        if compare_trade_date <= latest_closed_trade_date:
            eligible.append(
                ReviewCandidate(
                    result_file=result_file,
                    generated_at=generated_at,
                    compare_trade_date=compare_trade_date,
                    result_data=result_data,
                )
            )

    if not eligible:
        raise RuntimeError("当前没有可复盘的 Top100 结果")

    return max(eligible, key=lambda item: item.generated_at)


def _fetch_stock_daily_map(pro, trade_date: str, ts_codes: list[str]) -> dict[str, dict]:
    df = pro.daily(trade_date=trade_date)
    if df is None or df.empty:
        return {}
    df["ts_code"] = df["ts_code"].astype(str).str.upper()
    ts_code_set = {_normalize_ts_code(code) for code in ts_codes if code}
    df = df[df["ts_code"].isin(ts_code_set)].copy()
    return {row["ts_code"]: row.to_dict() for _, row in df.iterrows()}


def _fetch_sse_index_pct_chg(pro, trade_date: str) -> float | None:
    df = pro.index_daily(ts_code=SSE_INDEX_CODE, start_date=trade_date, end_date=trade_date)
    if df is None or df.empty:
        return None
    row = df.iloc[0]
    try:
        return float(row["pct_chg"])
    except Exception:
        return None


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2f}%"


def _open_buy_return_pct(row: dict) -> float | None:
    try:
        open_price = float(row.get("open") or 0)
        close_price = float(row.get("close") or 0)
    except Exception:
        return None
    if open_price <= 0:
        return None
    return (close_price / open_price - 1.0) * 100.0


def _extract_review_rows(result_rows: list[dict], daily_map: dict[str, dict], market_pct: float | None) -> list[dict]:
    review_rows: list[dict] = []
    for rank, row in enumerate(result_rows[:100], start=1):
        ts_code = _normalize_ts_code(row.get("代码") or row.get("ts_code") or "")
        daily_row = daily_map.get(ts_code, {})
        stock_pct = None
        if daily_row:
            try:
                stock_pct = float(daily_row.get("pct_chg"))
            except Exception:
                stock_pct = None

        review_rows.append(
            {
                "rank": rank,
                "stock_name": str(row.get("股票名称") or row.get("name") or ""),
                "ts_code": ts_code,
                "match_score": row.get("综合匹配度", row.get("综合评分", "")),
                "short_term": str(row.get("短线建议") or row.get("操作评级") or ""),
                "daily_pct_chg": stock_pct,
                "open_buy_pct": _open_buy_return_pct(daily_row),
                "market_pct_chg": market_pct,
            }
        )
    return review_rows


def _normalize_ts_code(value: object) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    if "." in text:
        return text
    if text.startswith("6"):
        return f"{text}.SH"
    if text.startswith(("4", "8")):
        return f"{text}.BJ"
    return f"{text}.SZ"


def render_top100_review_markdown(review: dict) -> str:
    lines = [
        f"# Top100 次日表现复盘（{review['compare_trade_date']}）",
        "",
        f"- 榜单来源文件：`{review['source_file']}`",
        f"- 榜单生成时间：`{review['generated_at']}`",
        f"- 对比交易日：`{review['compare_trade_date']}`",
        f"- 分析模型：`{review['model']}`",
        f"- 本次 Token 消耗：`{review['tokens_used']}`",
        f"- 结果数量：`{len(review['rows'])}`",
        "",
        "| 股票名 | 代码 | 排名 | 综合匹配度 | 短线建议 | 当天涨跌幅 | 开盘买入策略 | 上证指数涨跌幅 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    for row in review["rows"]:
        lines.append(
            "| {stock_name} | {ts_code} | {rank} | {match_score} | {short_term} | {daily_pct} | {open_buy} | {market_pct} |".format(
                stock_name=row["stock_name"] or "-",
                ts_code=row["ts_code"] or "-",
                rank=row["rank"],
                match_score=row["match_score"],
                short_term=row["short_term"] or "-",
                daily_pct=_fmt_pct(row["daily_pct_chg"]),
                open_buy=_fmt_pct(row["open_buy_pct"]),
                market_pct=_fmt_pct(row["market_pct_chg"]),
            )
        )
    lines.append("")
    return "\n".join(lines)


def save_top100_review_markdown(review: dict, markdown_text: str) -> Path:
    TOP100_REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    generated_slug = review["generated_at"].replace(":", "").replace("-", "").replace(" ", "_")
    filename = f"top100_review_{review['compare_trade_date']}_{generated_slug}.md"
    output_path = TOP100_REVIEW_DIR / filename
    output_path.write_text(markdown_text, encoding="utf-8")
    return output_path


def build_latest_top100_review(now: datetime | None = None) -> dict:
    now = now or datetime.now(SH_TZ)
    candidate = _select_review_candidate(now=now)
    pro = _make_tushare_pro()
    result_rows = candidate.result_data.get("results") or []
    ts_codes = [str(row.get("代码") or "").upper() for row in result_rows[:100] if row.get("代码")]
    daily_map = _fetch_stock_daily_map(pro, candidate.compare_trade_date, ts_codes)
    market_pct = _fetch_sse_index_pct_chg(pro, candidate.compare_trade_date)

    review = {
        "source_file": str(candidate.result_file),
        "generated_at": candidate.generated_at.strftime("%Y-%m-%d %H:%M:%S"),
        "compare_trade_date": candidate.compare_trade_date,
        "model": candidate.result_data.get("model") or "",
        "tokens_used": candidate.result_data.get("tokens_used") or 0,
        "rows": _extract_review_rows(result_rows, daily_map, market_pct),
    }
    markdown_text = render_top100_review_markdown(review)
    output_path = save_top100_review_markdown(review, markdown_text)
    review["markdown_path"] = str(output_path)
    review["markdown_text"] = markdown_text
    return review
