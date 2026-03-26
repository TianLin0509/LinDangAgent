"""Build and load the 20-day memory dataset used by RAG-style retrieval."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TypedDict

import pandas as pd

from analysis.kline_research import ResearchConfig, build_research_dataset, normalize_price_frame
from analysis.pattern_semantics import build_pattern_details, pattern_summary_text
from data.history_dataset_builder import refresh_history_from_tushare
from data.similarity import load_history


BASE_DIR = Path(__file__).resolve().parent
RESEARCH_DIR = BASE_DIR / "research"
MEMORY_DATASET_PATH = RESEARCH_DIR / "window20_memories.parquet"
MEMORY_META_PATH = RESEARCH_DIR / "window20_memories_meta.json"

WINDOW_SIZE = 20
WINDOW20_FEATURE_COLUMNS = [
    "ret_1",
    "ret_3",
    "ret_5",
    "ret_10",
    "ret_20",
    "body_pct",
    "upper_shadow_pct",
    "lower_shadow_pct",
    "range_pct",
    "gap_pct",
    "close_pos",
    "ma5_gap",
    "ma10_gap",
    "ma20_gap",
    "ma60_gap",
    "ma5_vs_ma20",
    "dist_high_20",
    "dist_low_20",
    "volatility_5",
    "volatility_20",
    "atr14_pct",
    "rsi14",
    "macd_diff",
    "macd_hist",
    "volume_ratio_5",
    "volume_ratio_20",
    "streak_signed",
    "benchmark_ret_5",
    "relative_strength_5",
    "window_return_20",
    "window_volatility_20",
    "up_days_ratio_20",
    "down_days_ratio_20",
    "avg_body_pct_20",
    "avg_range_pct_20",
    "volume_trend_20",
]

PRICE_SEQUENCE_COLUMNS = [f"close_z_{idx:02d}" for idx in range(1, WINDOW_SIZE + 1)]
RETURN_SEQUENCE_COLUMNS = [f"ret_z_{idx:02d}" for idx in range(1, WINDOW_SIZE + 1)]
VOLUME_SEQUENCE_COLUMNS = [f"vol_z_{idx:02d}" for idx in range(1, WINDOW_SIZE + 1)]
PV_FLOW_SEQUENCE_COLUMNS = [f"pv_flow_{idx:02d}" for idx in range(1, WINDOW_SIZE + 1)]
SEQUENCE_COLUMNS = (
    PRICE_SEQUENCE_COLUMNS
    + RETURN_SEQUENCE_COLUMNS
    + VOLUME_SEQUENCE_COLUMNS
    + PV_FLOW_SEQUENCE_COLUMNS
)

LABEL_COLUMNS = [
    "label_ret_5",
    "label_ret_10",
    "label_ret_20",
    "label_up_5",
    "label_up_10",
    "label_up_20",
    "label_drawdown_5",
    "label_drawdown_10",
    "label_drawdown_20",
    "label_max_up_5",
    "label_max_up_10",
    "label_max_up_20",
]


class Window20MemoryRecord(TypedDict, total=False):
    sample_id: str
    ts_code: str
    name: str
    industry: str
    window_start: str
    window_end: str
    window_size: int
    pattern_key: str
    pattern_summary: str
    memory_text: str


@dataclass(slots=True)
class MemoryBuildStats:
    history_rows: int
    history_stocks: int
    memory_rows: int
    memory_stocks: int
    window_size: int
    history_start: str | None
    history_end: str | None
    built_at: str
    source: str
    config: dict


def build_window20_numeric_ensemble_dataset(
    *,
    years: int = 5,
    refresh_history: bool = False,
    full_rebuild_history: bool = False,
    history_frame: pd.DataFrame | None = None,
) -> MemoryBuildStats:
    """Alias for the stronger numeric ensemble dataset build."""
    return build_window20_memory_dataset(
        years=years,
        refresh_history=refresh_history,
        full_rebuild_history=full_rebuild_history,
        history_frame=history_frame,
    )


def load_window20_numeric_ensemble_dataset() -> pd.DataFrame:
    return load_window20_memory_dataset()


def build_window20_memory_dataset(
    *,
    years: int = 5,
    refresh_history: bool = False,
    full_rebuild_history: bool = False,
    history_frame: pd.DataFrame | None = None,
) -> MemoryBuildStats:
    """Build the 20-day memory dataset from local history or an injected frame."""
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)

    if history_frame is not None:
        history = history_frame.copy()
        source = "injected"
    elif refresh_history:
        history = refresh_history_from_tushare(years=years, full_rebuild=full_rebuild_history)
        source = "tushare_refresh"
    else:
        history = load_history()
        source = "local_history"
        if history.empty:
            history = refresh_history_from_tushare(years=years, full_rebuild=full_rebuild_history)
            source = "tushare_bootstrap"

    history = history.copy()
    if "trade_date" in history.columns:
        history["trade_date"] = pd.to_numeric(history["trade_date"], errors="coerce")
        min_trade_date = int((datetime.now().date() - timedelta(days=years * 366)).strftime("%Y%m%d"))
        history = history[history["trade_date"] >= min_trade_date].copy()

    cfg = ResearchConfig(
        lookback=WINDOW_SIZE,
        horizons=(5, 10, 20),
        event_gap=1,
        min_history=90,
        min_rule_samples=30,
    )
    base_samples = build_research_dataset(history, config=cfg)
    memory_df = _build_memory_frame(history, base_samples)
    memory_df.to_parquet(MEMORY_DATASET_PATH, index=False)

    stats = MemoryBuildStats(
        history_rows=int(len(history)),
        history_stocks=int(history["ts_code"].nunique()) if not history.empty else 0,
        memory_rows=int(len(memory_df)),
        memory_stocks=int(memory_df["ts_code"].nunique()) if not memory_df.empty else 0,
        window_size=WINDOW_SIZE,
        history_start=_fmt_date(history["trade_date"].min()) if not history.empty else None,
        history_end=_fmt_date(history["trade_date"].max()) if not history.empty else None,
        built_at=datetime.now().isoformat(timespec="seconds"),
        source=source,
        config=asdict(cfg),
    )
    MEMORY_META_PATH.write_text(json.dumps(asdict(stats), ensure_ascii=False, indent=2), encoding="utf-8")
    return stats


def load_window20_memory_dataset() -> pd.DataFrame:
    if not MEMORY_DATASET_PATH.exists():
        return pd.DataFrame()
    return pd.read_parquet(MEMORY_DATASET_PATH)


def load_window20_memory_metadata() -> dict:
    if not MEMORY_META_PATH.exists():
        return {}
    return json.loads(MEMORY_META_PATH.read_text(encoding="utf-8"))


def build_query_window20_record(price_frame: pd.DataFrame) -> dict:
    """
    Build a query record from raw history or a precomputed single-row record.

    Raw daily price input must contain enough history for long-window indicators.
    """
    if isinstance(price_frame, pd.Series):
        row = price_frame.to_dict()
        return _finalize_query_record(row)

    if len(price_frame) == 1 and set(WINDOW20_FEATURE_COLUMNS).issubset(price_frame.columns):
        row = price_frame.iloc[0].to_dict()
        return _finalize_query_record(row)

    normalized = normalize_price_frame(price_frame)
    codes = normalized["ts_code"].dropna().unique().tolist()
    if len(codes) != 1:
        raise ValueError("query price_frame must contain exactly one stock")
    if len(normalized) < 90:
        raise ValueError("raw query price_frame must contain at least 90 trading days")

    cfg = ResearchConfig(
        lookback=WINDOW_SIZE,
        horizons=(5, 10, 20),
        event_gap=1,
        min_history=90,
        min_rule_samples=30,
    )
    sample_df = build_research_dataset(normalized, config=cfg)
    if sample_df.empty:
        raise ValueError("unable to build query sample from the supplied price_frame")

    price_map = _build_window_boundaries(normalized)
    latest = sample_df.sort_values("trade_date").tail(1).copy()
    latest["window_key"] = latest["ts_code"].astype(str) + "|" + latest["trade_date"].dt.strftime("%Y-%m-%d")
    latest["window_start"] = latest["window_key"].map(price_map["window_start"])
    latest["window_end"] = latest["window_key"].map(price_map["window_end"])
    row = latest.iloc[0].to_dict()
    return _finalize_query_record(row)


def _build_memory_frame(history: pd.DataFrame, base_samples: pd.DataFrame) -> pd.DataFrame:
    if base_samples.empty:
        return pd.DataFrame()

    normalized = normalize_price_frame(history)
    bounds = _build_window_boundaries(normalized)

    memories = base_samples.copy()
    memories["window_key"] = memories["ts_code"].astype(str) + "|" + memories["trade_date"].dt.strftime("%Y-%m-%d")
    memories["window_start"] = memories["window_key"].map(bounds["window_start"])
    memories["window_end"] = memories["window_key"].map(bounds["window_end"])
    memories = memories.dropna(subset=["window_start", "window_end"]).copy()

    memories["window_size"] = WINDOW_SIZE
    memories["sample_id"] = memories["ts_code"].astype(str) + "_" + memories["window_end"].dt.strftime("%Y%m%d")
    memories["pattern_summary"] = memories.apply(_build_pattern_summary_from_row, axis=1)
    memories["memory_text"] = memories.apply(_build_memory_text_from_row, axis=1)
    memories = _attach_sequence_columns(memories, normalized)

    label_map = {
        "label_ret_5": "forward_return_5d",
        "label_ret_10": "forward_return_10d",
        "label_ret_20": "forward_return_20d",
        "label_up_5": "forward_up_5d",
        "label_up_10": "forward_up_10d",
        "label_up_20": "forward_up_20d",
        "label_drawdown_5": "forward_drawdown_5d",
        "label_drawdown_10": "forward_drawdown_10d",
        "label_drawdown_20": "forward_drawdown_20d",
        "label_max_up_5": "forward_max_up_5d",
        "label_max_up_10": "forward_max_up_10d",
        "label_max_up_20": "forward_max_up_20d",
    }
    for target, source in label_map.items():
        memories[target] = memories[source]

    memories = memories.dropna(subset=LABEL_COLUMNS).copy()

    keep_columns = [
        "sample_id",
        "ts_code",
        "name",
        "industry",
        "window_start",
        "window_end",
        "window_size",
        "pattern_key",
        "pattern_summary",
        "memory_text",
    ] + WINDOW20_FEATURE_COLUMNS + LABEL_COLUMNS + SEQUENCE_COLUMNS

    available_columns = [col for col in keep_columns if col in memories.columns]
    result = memories[available_columns].copy()
    result["window_start"] = pd.to_datetime(result["window_start"])
    result["window_end"] = pd.to_datetime(result["window_end"])
    return result.sort_values(["ts_code", "window_end"]).reset_index(drop=True)


def _attach_sequence_columns(memories: pd.DataFrame, normalized_history: pd.DataFrame) -> pd.DataFrame:
    seq_map = _build_sequence_maps(normalized_history)
    for column in SEQUENCE_COLUMNS:
        memories[column] = memories["window_key"].map(seq_map[column]).fillna(0.0)
    return memories


def _build_window_boundaries(price_frame: pd.DataFrame) -> dict[str, dict[str, pd.Timestamp]]:
    window_start_map: dict[str, pd.Timestamp] = {}
    window_end_map: dict[str, pd.Timestamp] = {}
    for code, group in price_frame.groupby("ts_code", sort=False):
        group = group.sort_values("trade_date").copy()
        group["window_start"] = group["trade_date"].shift(WINDOW_SIZE - 1)
        group = group.dropna(subset=["window_start"])
        keys = code + "|" + group["trade_date"].dt.strftime("%Y-%m-%d")
        window_start_map.update(dict(zip(keys, group["window_start"])))
        window_end_map.update(dict(zip(keys, group["trade_date"])))
    return {"window_start": window_start_map, "window_end": window_end_map}


def _build_sequence_maps(price_frame: pd.DataFrame) -> dict[str, dict[str, float]]:
    result = {column: {} for column in SEQUENCE_COLUMNS}
    for code, group in price_frame.groupby("ts_code", sort=False):
        group = group.sort_values("trade_date").reset_index(drop=True)
        close_arr = group["close"].to_numpy(dtype=float)
        vol_arr = group["vol"].to_numpy(dtype=float)
        ret_arr = pd.Series(close_arr).pct_change().fillna(0.0).to_numpy(dtype=float)
        pv_arr = ret_arr * _safe_zscore(vol_arr)

        for end_idx in range(WINDOW_SIZE - 1, len(group)):
            start_idx = end_idx - WINDOW_SIZE + 1
            key = f"{code}|{group.loc[end_idx, 'trade_date'].strftime('%Y-%m-%d')}"
            close_seq = _safe_zscore(close_arr[start_idx : end_idx + 1])
            ret_seq = _safe_zscore(ret_arr[start_idx : end_idx + 1])
            vol_seq = _safe_zscore(vol_arr[start_idx : end_idx + 1])
            pv_seq = _safe_zscore(pv_arr[start_idx : end_idx + 1])

            for seq, columns in (
                (close_seq, PRICE_SEQUENCE_COLUMNS),
                (ret_seq, RETURN_SEQUENCE_COLUMNS),
                (vol_seq, VOLUME_SEQUENCE_COLUMNS),
                (pv_seq, PV_FLOW_SEQUENCE_COLUMNS),
            ):
                for column, value in zip(columns, seq, strict=True):
                    result[column][key] = float(value)
    return result


def _finalize_query_record(row: dict) -> dict:
    result = row.copy()
    if "window_end" in result:
        result["window_end"] = pd.to_datetime(result["window_end"])
    if "window_start" in result:
        result["window_start"] = pd.to_datetime(result["window_start"])

    if not result.get("pattern_summary"):
        result["pattern_summary"] = _build_pattern_summary_from_row(result)
    if not result.get("memory_text"):
        result["memory_text"] = _build_memory_text_from_row(result)
    if not result.get("sample_id"):
        end_text = pd.to_datetime(result["window_end"]).strftime("%Y%m%d")
        result["sample_id"] = f"{result.get('ts_code', 'UNKNOWN')}_{end_text}"
    return result


def _build_pattern_summary_from_row(row) -> str:
    details = build_pattern_details(row)
    return pattern_summary_text(details)


def _build_memory_text_from_row(row) -> str:
    end_date = _fmt_datetime(row.get("window_end") or row.get("trade_date"))
    name = row.get("name") or row.get("ts_code") or "未知标的"
    pattern_summary = _build_pattern_summary_from_row(row)
    trend_text = _safe_feature_text(row.get("window_return_20"), "过去20个交易日整体偏强", "过去20个交易日整体偏弱", "过去20个交易日整体震荡")
    momentum_text = _safe_feature_text(row.get("ret_5"), "短线动能上行", "短线动能回落", "短线动能走平")
    volume_text = _safe_feature_text(row.get("volume_trend_20"), "量能较20日前放大", "量能较20日前收缩", "量能较20日前基本平稳")
    volatility_text = _safe_feature_text(row.get("window_volatility_20"), "波动偏大", "波动较低", "波动适中", positive_threshold=0.05, negative_threshold=0.02)
    return (
        f"截至 {end_date}，{name} {trend_text}，{momentum_text}，{volume_text}，{volatility_text}，"
        f"{pattern_summary}。"
    )


def _safe_feature_text(
    value,
    positive_text: str,
    negative_text: str,
    neutral_text: str,
    *,
    positive_threshold: float = 0.03,
    negative_threshold: float = -0.03,
) -> str:
    try:
        number = float(value)
    except Exception:
        return neutral_text
    if number >= positive_threshold:
        return positive_text
    if number <= negative_threshold:
        return negative_text
    return neutral_text


def _fmt_date(value) -> str | None:
    if pd.isna(value):
        return None
    text = str(int(float(value)))
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}"


def _fmt_datetime(value) -> str:
    if value is None or pd.isna(value):
        return "未知日期"
    return pd.to_datetime(value).strftime("%Y-%m-%d")


def _safe_zscore(values) -> list[float]:
    arr = pd.Series(values, dtype="float64").to_numpy()
    mean = float(arr.mean()) if len(arr) else 0.0
    std = float(arr.std()) if len(arr) else 0.0
    if std < 1e-8:
        return [0.0] * len(arr)
    return ((arr - mean) / std).tolist()
