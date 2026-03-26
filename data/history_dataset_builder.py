"""Build and refresh local A-share history and research datasets from Tushare."""

from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from analysis.kline_research import ResearchConfig, build_research_dataset
from data.similarity import load_history
from data.tushare_client import get_pro, load_stock_list


BASE_DIR = Path(__file__).resolve().parent
HISTORY_DIR = BASE_DIR / "history"
RESEARCH_DIR = BASE_DIR / "research"
HISTORY_META_PATH = HISTORY_DIR / "metadata.json"
RESEARCH_DATASET_PATH = RESEARCH_DIR / "kline_samples.parquet"
RESEARCH_META_PATH = RESEARCH_DIR / "kline_samples_meta.json"


@dataclass(slots=True)
class BuildStats:
    history_rows: int
    history_stocks: int
    history_start: str | None
    history_end: str | None
    sample_rows: int
    sample_stocks: int
    built_at: str
    config: dict


def refresh_history_from_tushare(
    *,
    years: int = 5,
    full_rebuild: bool = False,
    pause_seconds: float = 0.05,
) -> pd.DataFrame:
    """Refresh the local all-market daily history using Tushare."""
    pro = _wait_for_tushare()
    if pro is None:
        raise RuntimeError("Tushare client is not ready")

    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    stock_list, err = load_stock_list()
    if err:
        raise RuntimeError(f"failed to load stock list: {err}")

    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=years * 366)
    start_str = start_date.strftime("%Y%m%d")
    end_str = end_date.strftime("%Y%m%d")

    existing = pd.DataFrame()
    if not full_rebuild:
        try:
            existing = load_history()
        except Exception:
            existing = pd.DataFrame()

    if not existing.empty:
        existing = existing.copy()
        existing["trade_date"] = pd.to_numeric(existing["trade_date"], errors="coerce").astype("Int64")
        existing = existing[existing["trade_date"] >= int(start_str)].copy()
        last_date = int(existing["trade_date"].max())
        fetch_start = (datetime.strptime(str(last_date), "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
    else:
        fetch_start = start_str

    fetched = _fetch_daily_by_trade_date(
        pro,
        start_date=fetch_start,
        end_date=end_str,
        pause_seconds=pause_seconds,
    )

    merged = _merge_history(existing, fetched, stock_list)
    _write_partitioned_history(merged)
    HISTORY_META_PATH.write_text(
        json.dumps(
            {
                "source": "tushare",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "start_date": start_str,
                "end_date": end_str,
                "rows": int(len(merged)),
                "stocks": int(merged["ts_code"].nunique()) if not merged.empty else 0,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return merged


def build_and_save_research_dataset(
    *,
    config: ResearchConfig | None = None,
    years: int = 5,
    refresh_history: bool = False,
    full_rebuild_history: bool = False,
) -> BuildStats:
    """Create the persisted research sample dataset used for stock-name lookup."""
    cfg = config or ResearchConfig()
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)

    if refresh_history:
        history = refresh_history_from_tushare(years=years, full_rebuild=full_rebuild_history)
    else:
        history = load_history()
        if history.empty:
            history = refresh_history_from_tushare(years=years, full_rebuild=full_rebuild_history)

    history = history.copy()
    history["trade_date"] = pd.to_numeric(history["trade_date"], errors="coerce")
    min_trade_date = int((datetime.now().date() - timedelta(days=years * 366)).strftime("%Y%m%d"))
    history = history[history["trade_date"] >= min_trade_date].copy()

    samples = build_research_dataset(history, config=cfg)
    samples.to_parquet(RESEARCH_DATASET_PATH, index=False)

    stats = BuildStats(
        history_rows=int(len(history)),
        history_stocks=int(history["ts_code"].nunique()) if not history.empty else 0,
        history_start=_fmt_trade_date(history["trade_date"].min()) if not history.empty else None,
        history_end=_fmt_trade_date(history["trade_date"].max()) if not history.empty else None,
        sample_rows=int(len(samples)),
        sample_stocks=int(samples["ts_code"].nunique()) if not samples.empty else 0,
        built_at=datetime.now().isoformat(timespec="seconds"),
        config=asdict(cfg),
    )

    RESEARCH_META_PATH.write_text(
        json.dumps(asdict(stats), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return stats


def load_research_dataset() -> pd.DataFrame:
    if not RESEARCH_DATASET_PATH.exists():
        return pd.DataFrame()
    return pd.read_parquet(RESEARCH_DATASET_PATH)


def load_research_metadata() -> dict:
    if not RESEARCH_META_PATH.exists():
        return {}
    return json.loads(RESEARCH_META_PATH.read_text(encoding="utf-8"))


def _fetch_daily_by_trade_date(pro, *, start_date: str, end_date: str, pause_seconds: float) -> pd.DataFrame:
    trade_cal = pro.trade_cal(exchange="", start_date=start_date, end_date=end_date)
    if trade_cal is None or trade_cal.empty:
        return pd.DataFrame()

    open_days = (
        trade_cal[trade_cal["is_open"] == 1]["cal_date"]
        .astype(str)
        .sort_values()
        .tolist()
    )
    if not open_days:
        return pd.DataFrame()

    frames: list[pd.DataFrame] = []
    total = len(open_days)
    for idx, trade_date in enumerate(open_days, start=1):
        try:
            frame = pro.daily(trade_date=trade_date)
            if frame is not None and not frame.empty:
                frames.append(frame)
        except Exception:
            continue
        if pause_seconds > 0:
            time.sleep(pause_seconds)
        if idx % 100 == 0:
            print(f"[history_builder] fetched {idx}/{total} trade dates")

    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _merge_history(existing: pd.DataFrame, fetched: pd.DataFrame, stock_list: pd.DataFrame) -> pd.DataFrame:
    frames = []
    if existing is not None and not existing.empty:
        normalized_existing = existing.copy()
        normalized_existing["trade_date"] = pd.to_numeric(normalized_existing["trade_date"], errors="coerce")
        frames.append(normalized_existing)

    if fetched is not None and not fetched.empty:
        normalized_fetched = fetched.copy()
        normalized_fetched["trade_date"] = pd.to_numeric(normalized_fetched["trade_date"], errors="coerce")
        frames.append(normalized_fetched)

    if not frames:
        return pd.DataFrame()

    history = pd.concat(frames, ignore_index=True)
    history = history.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")

    keep_cols = ["ts_code", "trade_date", "open", "high", "low", "close", "vol", "pct_chg", "amount"]
    history = history[[col for col in keep_cols if col in history.columns]].copy()

    info_cols = ["ts_code", "symbol", "name", "industry", "list_date"]
    if stock_list is not None and not stock_list.empty:
        info = stock_list[[col for col in info_cols if col in stock_list.columns]].drop_duplicates("ts_code")
        history = history.merge(info, on="ts_code", how="left")

    history = history.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    return history


def _write_partitioned_history(history: pd.DataFrame, parts: int = 3) -> None:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    for old_part in HISTORY_DIR.glob("all_daily_part*.parquet"):
        old_part.unlink()

    if history.empty:
        return

    part_count = max(1, parts)
    chunks = list(_split_frame(history, part_count))
    for idx, chunk in enumerate(chunks):
        chunk.to_parquet(HISTORY_DIR / f"all_daily_part{idx}.parquet", index=False)


def _split_frame(frame: pd.DataFrame, parts: int):
    step = math.ceil(len(frame) / parts)
    for start in range(0, len(frame), step):
        yield frame.iloc[start : start + step].copy()


def _fmt_trade_date(value) -> str | None:
    if pd.isna(value):
        return None
    text = str(int(float(value)))
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}"


def _wait_for_tushare(timeout_seconds: int = 20):
    deadline = time.time() + timeout_seconds
    pro = get_pro()
    while pro is None and time.time() < deadline:
        time.sleep(1)
        pro = get_pro()
    return pro
