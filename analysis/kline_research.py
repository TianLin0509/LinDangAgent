"""Research utilities for K-line pattern statistics and probability forecasts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype


EPS = 1e-12
DEFAULT_FEATURE_COLUMNS = [
    "ret_1",
    "ret_3",
    "ret_5",
    "body_pct",
    "upper_shadow_pct",
    "lower_shadow_pct",
    "range_pct",
    "gap_pct",
    "close_pos",
    "volume_ratio_5",
    "volume_ratio_20",
    "streak_signed",
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
    "benchmark_ret_5",
    "relative_strength_5",
]

PRICE_ALIASES = {
    "trade_date": ["trade_date", "日期"],
    "open": ["open", "开盘"],
    "high": ["high", "最高"],
    "low": ["low", "最低"],
    "close": ["close", "收盘"],
    "vol": ["vol", "成交量"],
    "amount": ["amount", "成交额"],
    "pct_chg": ["pct_chg", "涨跌幅"],
    "ts_code": ["ts_code", "股票代码"],
    "name": ["name", "股票名称"],
}


@dataclass(slots=True)
class ResearchConfig:
    lookback: int = 7
    horizons: tuple[int, ...] = (5, 10, 20)
    event_gap: int = 5
    min_history: int = 90
    min_rule_samples: int = 30
    benchmark_code: str | None = None


@dataclass(slots=True)
class LinearProbabilityModel:
    """A tiny logistic model implemented with numpy."""

    feature_names: list[str]
    horizon: int
    learning_rate: float = 0.05
    epochs: int = 400
    l2: float = 1e-3
    max_train_rows: int = 40_000
    random_state: int = 42
    bias: float = 0.0
    weights: np.ndarray | None = None
    mean_: np.ndarray | None = None
    scale_: np.ndarray | None = None

    def fit(self, frame: pd.DataFrame) -> "LinearProbabilityModel":
        target_col = f"forward_up_{self.horizon}d"
        columns = list(dict.fromkeys(self.feature_names + [target_col]))
        valid_mask = np.ones(len(frame), dtype=bool)
        for column in columns:
            valid_mask &= frame[column].notna().to_numpy()

        valid_idx = np.flatnonzero(valid_mask)
        if valid_idx.size == 0:
            raise ValueError("not enough clean rows to fit the probability model")

        target = frame[target_col].to_numpy(dtype=np.float32, copy=False)
        sampled_idx = valid_idx

        if valid_idx.size > self.max_train_rows:
            positive_idx = valid_idx[target[valid_idx] >= 0.5]
            negative_idx = valid_idx[target[valid_idx] < 0.5]
            pos_target = min(len(positive_idx), self.max_train_rows // 2)
            neg_target = min(len(negative_idx), self.max_train_rows - pos_target)
            if pos_target == 0 or neg_target == 0:
                rng = np.random.default_rng(self.random_state)
                sampled_idx = np.sort(rng.choice(valid_idx, size=self.max_train_rows, replace=False))
            else:
                rng = np.random.default_rng(self.random_state)
                pos_choice = rng.choice(positive_idx, size=pos_target, replace=False)
                neg_choice = rng.choice(negative_idx, size=neg_target, replace=False)
                sampled_idx = np.sort(
                    np.concatenate([pos_choice, neg_choice])
                )

        x = frame.iloc[sampled_idx][self.feature_names].to_numpy(dtype=np.float32, copy=True)
        y = target[sampled_idx].astype(np.float32, copy=False)

        self.mean_ = x.mean(axis=0)
        self.scale_ = x.std(axis=0)
        self.scale_[self.scale_ < EPS] = 1.0
        x = (x - self.mean_) / self.scale_

        self.weights = np.zeros(x.shape[1], dtype=np.float32)
        self.bias = 0.0

        for _ in range(self.epochs):
            logits = x @ self.weights + self.bias
            probs = 1.0 / (1.0 + np.exp(-np.clip(logits, -30, 30)))
            error = probs - y
            grad_w = (x.T @ error) / len(x) + self.l2 * self.weights
            grad_b = float(error.mean())
            self.weights -= self.learning_rate * grad_w
            self.bias -= self.learning_rate * grad_b

        return self

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        if self.weights is None or self.mean_ is None or self.scale_ is None:
            raise ValueError("model has not been fitted")

        x = frame[self.feature_names].to_numpy(dtype=float)
        x = np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)
        x = (x - self.mean_) / self.scale_
        logits = x @ self.weights + self.bias
        return 1.0 / (1.0 + np.exp(-np.clip(logits, -30, 30)))


def normalize_price_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize price columns from Chinese or English naming into one schema."""
    df = frame.copy()
    rename_map: dict[str, str] = {}
    for target, aliases in PRICE_ALIASES.items():
        for alias in aliases:
            if alias in df.columns:
                rename_map[alias] = target
                break
    df = df.rename(columns=rename_map)

    required = {"trade_date", "open", "high", "low", "close", "vol"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"missing required price columns: {sorted(missing)}")

    if "ts_code" not in df.columns:
        df["ts_code"] = "UNKNOWN"

    numeric_cols = [col for col in ["open", "high", "low", "close", "vol", "amount", "pct_chg"] if col in df.columns]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    trade_date = df["trade_date"]
    if is_numeric_dtype(trade_date):
        trade_date = trade_date.astype("Int64").astype(str)
    df["trade_date"] = pd.to_datetime(trade_date, errors="coerce")

    df = df.dropna(subset=["trade_date", "open", "high", "low", "close", "vol"]).copy()
    df["ts_code"] = df["ts_code"].astype(str).str.upper()
    if "name" in df.columns:
        df["name"] = df["name"].fillna("").astype(str)

    return df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)


def build_research_dataset(
    price_frame: pd.DataFrame,
    *,
    config: ResearchConfig | None = None,
) -> pd.DataFrame:
    """Build a multi-stock dataset with features, labels, and rule tags."""
    cfg = config or ResearchConfig()
    prices = normalize_price_frame(price_frame)

    benchmark = _build_benchmark_features(prices, cfg.benchmark_code)
    parts: list[pd.DataFrame] = []

    for _, group in prices.groupby("ts_code", sort=False):
        if len(group) < max(cfg.min_history, max(cfg.horizons) + cfg.lookback + 5):
            continue
        enriched = _enrich_single_stock(group.reset_index(drop=True), cfg.horizons, benchmark)
        warmup = max(cfg.lookback, 60)
        if len(enriched) <= warmup:
            continue
        enriched = enriched.iloc[warmup:].copy()
        parts.append(enriched)

    if not parts:
        return pd.DataFrame()

    dataset = pd.concat(parts, ignore_index=True)
    dataset = _add_rule_tags(dataset)
    dataset = apply_event_gap(dataset, cfg.event_gap)
    return dataset


def summarize_rule_patterns(
    dataset: pd.DataFrame,
    *,
    horizon: int = 5,
    min_samples: int = 30,
    pattern_col: str = "pattern_key",
) -> pd.DataFrame:
    """Summarize how each rule-defined pattern behaved historically."""
    target_cols = [
        pattern_col,
        f"forward_return_{horizon}d",
        f"forward_up_{horizon}d",
        f"forward_drawdown_{horizon}d",
        f"forward_max_up_{horizon}d",
        "sample_weight",
    ]
    clean = dataset.dropna(subset=target_cols).copy()
    if clean.empty:
        return pd.DataFrame()

    grouped = (
        clean.groupby(pattern_col)
        .agg(
            sample_count=(pattern_col, "size"),
            up_prob=(f"forward_up_{horizon}d", "mean"),
            avg_return=(f"forward_return_{horizon}d", "mean"),
            median_return=(f"forward_return_{horizon}d", "median"),
            avg_drawdown=(f"forward_drawdown_{horizon}d", "mean"),
            avg_max_up=(f"forward_max_up_{horizon}d", "mean"),
        )
        .reset_index()
    )
    grouped = grouped[grouped["sample_count"] >= min_samples].copy()
    grouped["up_prob"] = grouped["up_prob"] * 100
    grouped = grouped.sort_values(["up_prob", "avg_return", "sample_count"], ascending=[False, False, False])
    return grouped.reset_index(drop=True)


def train_probability_model(
    dataset: pd.DataFrame,
    *,
    horizon: int = 5,
    feature_names: Sequence[str] | None = None,
) -> LinearProbabilityModel:
    feature_list = list(feature_names or DEFAULT_FEATURE_COLUMNS)
    model = LinearProbabilityModel(feature_names=feature_list, horizon=horizon)
    return model.fit(dataset)


def predict_latest(
    dataset: pd.DataFrame,
    *,
    stock_code: str,
    model: LinearProbabilityModel,
) -> dict:
    code = stock_code.upper()
    stock_rows = dataset[dataset["ts_code"] == code].sort_values("trade_date")
    if stock_rows.empty:
        raise ValueError(f"stock not found in dataset: {stock_code}")

    latest = stock_rows.tail(1).copy()
    prob = float(model.predict_proba(latest)[0])
    horizon = model.horizon
    return {
        "ts_code": code,
        "trade_date": latest.iloc[0]["trade_date"],
        "pattern_key": latest.iloc[0].get("pattern_key", ""),
        "up_probability": round(prob * 100, 2),
        "expected_return_proxy": round((prob - 0.5) * 2, 4),
        "horizon": horizon,
    }


def walk_forward_evaluate(
    dataset: pd.DataFrame,
    *,
    horizon: int = 5,
    feature_names: Sequence[str] | None = None,
    min_train_rows: int = 500,
    folds: int = 4,
) -> dict:
    """Evaluate the model using ordered time splits."""
    feature_list = list(feature_names or DEFAULT_FEATURE_COLUMNS)
    target_col = f"forward_up_{horizon}d"
    clean = dataset.dropna(subset=feature_list + [target_col]).sort_values("trade_date").copy()
    if len(clean) < max(min_train_rows + 50, 200):
        return {"folds": [], "summary": None}

    unique_dates = np.array(sorted(clean["trade_date"].dropna().unique()))
    if len(unique_dates) < folds + 2:
        return {"folds": [], "summary": None}

    fold_points = np.linspace(0.55, 0.9, folds)
    results: list[dict] = []

    for ratio in fold_points:
        split_idx = int(len(unique_dates) * ratio)
        train_end = unique_dates[split_idx - 1]
        test_end_idx = min(len(unique_dates) - 1, split_idx + max(5, len(unique_dates) // (folds * 3)))
        test_end = unique_dates[test_end_idx]

        train = clean[clean["trade_date"] <= train_end]
        test = clean[(clean["trade_date"] > train_end) & (clean["trade_date"] <= test_end)]
        if len(train) < min_train_rows or test.empty:
            continue

        model = train_probability_model(train, horizon=horizon, feature_names=feature_list)
        probs = model.predict_proba(test)
        labels = test[target_col].to_numpy(dtype=float)
        preds = (probs >= 0.5).astype(float)

        accuracy = float((preds == labels).mean())
        brier = float(np.mean((probs - labels) ** 2))

        selected = test.loc[probs >= 0.55, f"forward_return_{horizon}d"]
        results.append(
            {
                "train_end": train_end,
                "test_end": test_end,
                "train_rows": int(len(train)),
                "test_rows": int(len(test)),
                "accuracy": round(accuracy, 4),
                "brier": round(brier, 4),
                "avg_selected_return": round(float(selected.mean()) if not selected.empty else 0.0, 4),
                "selected_count": int((probs >= 0.55).sum()),
                "baseline_up_rate": round(float(labels.mean()), 4),
            }
        )

    if not results:
        return {"folds": [], "summary": None}

    result_frame = pd.DataFrame(results)
    summary = {
        "avg_accuracy": round(float(result_frame["accuracy"].mean()), 4),
        "avg_brier": round(float(result_frame["brier"].mean()), 4),
        "avg_selected_return": round(float(result_frame["avg_selected_return"].mean()), 4),
        "avg_baseline_up_rate": round(float(result_frame["baseline_up_rate"].mean()), 4),
        "fold_count": int(len(result_frame)),
    }
    return {"folds": results, "summary": summary}


def build_stock_research_snapshot(
    dataset: pd.DataFrame,
    *,
    stock_code: str,
    horizon: int = 5,
    feature_names: Sequence[str] | None = None,
    min_rule_samples: int = 30,
) -> dict:
    """Return the latest stock signal plus the historical stats of the same pattern."""
    feature_list = list(feature_names or DEFAULT_FEATURE_COLUMNS)
    model = train_probability_model(dataset, horizon=horizon, feature_names=feature_list)
    latest = predict_latest(dataset, stock_code=stock_code, model=model)
    pattern = latest["pattern_key"]

    rule_stats = summarize_rule_patterns(dataset, horizon=horizon, min_samples=min_rule_samples)
    same_pattern = rule_stats[rule_stats["pattern_key"] == pattern].head(1)
    latest["pattern_stats"] = same_pattern.iloc[0].to_dict() if not same_pattern.empty else None
    return latest


def apply_event_gap(dataset: pd.DataFrame, gap: int) -> pd.DataFrame:
    if gap <= 1 or dataset.empty:
        dataset = dataset.copy()
        dataset["sample_weight"] = 1.0
        return dataset

    kept_parts: list[pd.DataFrame] = []
    for _, group in dataset.groupby("ts_code", sort=False):
        group = group.sort_values("trade_date").copy()
        keep_mask = np.ones(len(group), dtype=bool)
        last_kept = -gap
        for idx in range(len(group)):
            if idx - last_kept < gap:
                keep_mask[idx] = False
                continue
            last_kept = idx
        reduced = group.loc[keep_mask].copy()
        reduced["sample_weight"] = 1.0
        kept_parts.append(reduced)

    return pd.concat(kept_parts, ignore_index=True) if kept_parts else pd.DataFrame(columns=dataset.columns)


def _build_benchmark_features(prices: pd.DataFrame, benchmark_code: str | None) -> pd.DataFrame:
    if prices.empty:
        return pd.DataFrame(columns=["trade_date", "benchmark_ret_5", "benchmark_ret_20"])

    benchmark = None
    if benchmark_code:
        benchmark = prices[prices["ts_code"] == benchmark_code.upper()].copy()

    if benchmark is None or benchmark.empty:
        benchmark = (
            prices.groupby("trade_date")
            .agg(benchmark_close=("close", "mean"))
            .reset_index()
            .sort_values("trade_date")
        )
    else:
        benchmark = benchmark[["trade_date", "close"]].rename(columns={"close": "benchmark_close"})

    benchmark["benchmark_ret_5"] = benchmark["benchmark_close"].pct_change(5)
    benchmark["benchmark_ret_20"] = benchmark["benchmark_close"].pct_change(20)
    return benchmark[["trade_date", "benchmark_ret_5", "benchmark_ret_20"]]


def _enrich_single_stock(group: pd.DataFrame, horizons: Iterable[int], benchmark: pd.DataFrame) -> pd.DataFrame:
    df = group.copy()
    close = df["close"]
    open_ = df["open"]
    high = df["high"]
    low = df["low"]
    vol = df["vol"]

    hl_range = (high - low).replace(0, np.nan)
    prev_close = close.shift(1)
    returns = close.pct_change()

    df["ret_1"] = returns
    df["ret_3"] = close.pct_change(3)
    df["ret_5"] = close.pct_change(5)
    df["ret_10"] = close.pct_change(10)
    df["ret_20"] = close.pct_change(20)
    df["body_pct"] = (close - open_) / (open_.replace(0, np.nan) + EPS)
    df["upper_shadow_pct"] = (high - np.maximum(open_, close)) / (hl_range + EPS)
    df["lower_shadow_pct"] = (np.minimum(open_, close) - low) / (hl_range + EPS)
    df["range_pct"] = hl_range / (close.replace(0, np.nan) + EPS)
    df["gap_pct"] = (open_ / (prev_close + EPS)) - 1.0
    df["close_pos"] = (close - low) / (hl_range + EPS)
    df["volume_ratio_5"] = vol / (vol.rolling(5).mean() + EPS)
    df["volume_ratio_20"] = vol / (vol.rolling(20).mean() + EPS)
    df["streak_signed"] = _signed_streak(close)
    df["ma5_gap"] = close / (close.rolling(5).mean() + EPS) - 1.0
    df["ma10_gap"] = close / (close.rolling(10).mean() + EPS) - 1.0
    df["ma20_gap"] = close / (close.rolling(20).mean() + EPS) - 1.0
    df["ma60_gap"] = close / (close.rolling(60).mean() + EPS) - 1.0
    df["ma5_vs_ma20"] = close.rolling(5).mean() / (close.rolling(20).mean() + EPS) - 1.0
    df["dist_high_20"] = close / (high.rolling(20).max() + EPS) - 1.0
    df["dist_low_20"] = close / (low.rolling(20).min() + EPS) - 1.0
    df["volatility_5"] = returns.rolling(5).std()
    df["volatility_20"] = returns.rolling(20).std()
    df["atr14_pct"] = _atr(high, low, close, 14) / (close + EPS)
    df["rsi14"] = _rsi(close, 14)
    macd_diff, macd_signal, macd_hist = _macd(close)
    df["macd_diff"] = macd_diff
    df["macd_hist"] = macd_hist

    df = df.merge(benchmark, on="trade_date", how="left")
    df["relative_strength_5"] = df["ret_5"] - df["benchmark_ret_5"]
    df["window_return_20"] = df["ret_20"]
    df["window_volatility_20"] = returns.rolling(20).std()
    df["up_days_ratio_20"] = (returns > 0).rolling(20).mean()
    df["down_days_ratio_20"] = (returns < 0).rolling(20).mean()
    df["avg_body_pct_20"] = df["body_pct"].rolling(20).mean()
    df["avg_range_pct_20"] = df["range_pct"].rolling(20).mean()
    df["volume_trend_20"] = (vol / (vol.shift(20) + EPS) - 1.0)

    for horizon in horizons:
        fwd = _future_window_metrics(close.to_numpy(dtype=float), low.to_numpy(dtype=float), high.to_numpy(dtype=float), horizon)
        df[f"forward_return_{horizon}d"] = fwd["forward_return"]
        df[f"forward_up_{horizon}d"] = (fwd["forward_return"] > 0).astype(float)
        df[f"forward_drawdown_{horizon}d"] = fwd["forward_drawdown"]
        df[f"forward_max_up_{horizon}d"] = fwd["forward_max_up"]

    df["macd_signal_line"] = macd_signal
    return df


def _add_rule_tags(dataset: pd.DataFrame) -> pd.DataFrame:
    df = dataset.copy()
    df["trend_tag"] = np.select(
        [df["ma20_gap"] > 0.03, df["ma20_gap"] < -0.03],
        ["trend_up", "trend_down"],
        default="trend_flat",
    )
    df["momentum_tag"] = np.select(
        [df["ret_5"] > 0.04, df["ret_5"] < -0.04],
        ["momentum_up", "momentum_down"],
        default="momentum_flat",
    )
    df["volume_tag"] = np.select(
        [df["volume_ratio_5"] > 1.6, df["volume_ratio_5"] < 0.8],
        ["volume_expand", "volume_contract"],
        default="volume_neutral",
    )
    df["rsi_tag"] = np.select(
        [df["rsi14"] >= 65, df["rsi14"] <= 35],
        ["rsi_hot", "rsi_cold"],
        default="rsi_mid",
    )
    df["candle_tag"] = np.select(
        [
            (df["lower_shadow_pct"] > 0.45) & (df["body_pct"] > -0.01),
            (df["upper_shadow_pct"] > 0.45) & (df["body_pct"] < 0.01),
            df["body_pct"] > 0.03,
            df["body_pct"] < -0.03,
        ],
        ["lower_shadow", "upper_shadow", "wide_bull", "wide_bear"],
        default="neutral_body",
    )
    df["breakout_tag"] = np.select(
        [df["dist_high_20"] > -0.01, df["dist_low_20"] < 0.01],
        ["near_high", "near_low"],
        default="mid_range",
    )
    df["pattern_key"] = (
        df["trend_tag"]
        + "|"
        + df["momentum_tag"]
        + "|"
        + df["volume_tag"]
        + "|"
        + df["rsi_tag"]
        + "|"
        + df["candle_tag"]
        + "|"
        + df["breakout_tag"]
    )
    return df


def _signed_streak(close: pd.Series) -> pd.Series:
    changes = np.sign(close.diff().fillna(0.0))
    streak = np.zeros(len(close), dtype=float)
    for i in range(1, len(close)):
        if changes.iloc[i] == 0:
            streak[i] = 0
        elif changes.iloc[i] == changes.iloc[i - 1]:
            streak[i] = streak[i - 1] + changes.iloc[i]
        else:
            streak[i] = changes.iloc[i]
    return pd.Series(streak, index=close.index)


def _rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _macd(close: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    diff = ema12 - ema26
    signal = diff.ewm(span=9, adjust=False).mean()
    hist = (diff - signal) * 2
    return diff, signal, hist


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


def _future_window_metrics(close: np.ndarray, low: np.ndarray, high: np.ndarray, horizon: int) -> dict[str, np.ndarray]:
    n = len(close)
    future_return = np.full(n, np.nan, dtype=float)
    future_drawdown = np.full(n, np.nan, dtype=float)
    future_max_up = np.full(n, np.nan, dtype=float)

    for idx in range(n):
        end = idx + horizon
        if end >= n:
            break
        base = close[idx]
        future_close = close[end]
        future_slice_low = low[idx + 1 : end + 1]
        future_slice_high = high[idx + 1 : end + 1]

        future_return[idx] = future_close / (base + EPS) - 1.0
        future_drawdown[idx] = future_slice_low.min() / (base + EPS) - 1.0
        future_max_up[idx] = future_slice_high.max() / (base + EPS) - 1.0

    return {
        "forward_return": future_return,
        "forward_drawdown": future_drawdown,
        "forward_max_up": future_max_up,
    }
