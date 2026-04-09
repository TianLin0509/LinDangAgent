"""结果追踪 — 对比 AI 报告预测与实际行情

扫描 reports.db 中 T+8 天以上的报告，拉取后续实际行情，
计算 5/10/20 日收益率，与 AI 评分/方向对比，写入 outcomes.db（SQLite）。

迁移说明：原 outcomes.jsonl 在首次读取时自动迁移到 SQLite，
迁移后旧文件重命名为 outcomes.jsonl.bak。
"""

import json
import logging
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from knowledge.kb_config import BASE_DIR, OUTCOMES_FILE, REPORTS_DB_PATH, SCORE_WEIGHTS
from knowledge.kb_db import get_manager

logger = logging.getLogger(__name__)

DB_PATH = REPORTS_DB_PATH

# ── 评分提取（复用 analysis_service 的逻辑）──────────────────────────


def _extract_scores(report_text: str) -> dict | None:
    """从报告 markdown 中提取四维评分。"""
    match = re.search(r"<<<SCORES>>>(.*?)<<<END_SCORES>>>", report_text, re.DOTALL)
    if not match:
        return None
    block = match.group(1)
    scores: dict[str, float] = {}
    for line in block.strip().splitlines():
        line = line.strip()
        if not line or line == "---":
            continue
        parsed = re.match(r"(.+?)[:：]\s*(\d+(?:\.\d+)?)\s*/\s*10", line)
        if parsed:
            scores[parsed.group(1).strip()] = float(parsed.group(2))
    if not scores:
        return None
    weighted = sum(scores[d] * w for d, w in SCORE_WEIGHTS.items() if d in scores)
    total_w = sum(w for d, w in SCORE_WEIGHTS.items() if d in scores)
    if total_w > 0:
        scores["综合加权"] = round(weighted / total_w, 1)
    return scores


def _infer_direction(scores: dict) -> str:
    """根据综合加权推断看多/看空。"""
    composite = scores.get("综合加权", 5)
    if composite >= 6:
        return "bullish"
    elif composite <= 3:
        return "bearish"
    return "neutral"


def _extract_close_from_report(report_text: str) -> float | None:
    """尝试从报告正文中提取最新收盘价。"""
    # 常见格式: 收盘: 15.32  或  收盘价：15.32
    match = re.search(r"收盘[价]?[：:]\s*(\d+\.?\d*)", report_text)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    return None


# ── SQLite 辅助函数 ──────────────────────────────────────────────────


def _row_to_outcome(row: dict) -> dict:
    """将 SQLite 行转回 outcome dict（JSON 反序列化 + int→bool）。"""
    outcome = dict(row)
    # JSON 字段反序列化
    for field in ("scores", "war_room_generals"):
        if isinstance(outcome.get(field), str):
            try:
                outcome[field] = json.loads(outcome[field])
            except (json.JSONDecodeError, TypeError):
                outcome[field] = {}
    # hit_* / beat_market 转 bool
    for field in ("hit_5d", "hit_10d", "hit_20d", "beat_market_10d"):
        v = outcome.get(field)
        if v is not None:
            outcome[field] = bool(v)
    return outcome


def _migrate_jsonl_to_db():
    """从旧 JSONL 文件迁移数据到 SQLite（一次性，兼容过渡）。"""
    if not OUTCOMES_FILE.exists():
        return
    from knowledge.kb_io import read_jsonl_iter
    count = 0
    for entry in read_jsonl_iter(OUTCOMES_FILE):
        _append_outcome(entry)
        count += 1
    if count:
        logger.info("[outcome_tracker] migrated %d outcomes from JSONL to SQLite", count)
        # 重命名旧文件
        backup = OUTCOMES_FILE.with_suffix(".jsonl.bak")
        OUTCOMES_FILE.rename(backup)
        logger.info("[outcome_tracker] renamed %s -> %s", OUTCOMES_FILE.name, backup.name)


# ── 已评估记录缓存 ─────────────────────────────────────────────────

_evaluated_ids: set[str] = set()
_evaluated_loaded = False

# ── 沪深300基准收益缓存 ────────────────────────────────────────────
_benchmark_cache: dict[str, dict] = {}  # report_date_str -> {return_5d, return_10d, return_20d}

# ── war_room_tracker 关联 ─────────────────────────────────────────
_war_room_tracker: dict[str, dict] | None = None  # report_id -> tracker_entry


def _load_war_room_tracker() -> dict[str, dict]:
    """加载 war_room_tracker.jsonl，构建 report_id → tracker 映射。"""
    global _war_room_tracker
    if _war_room_tracker is not None:
        return _war_room_tracker
    _war_room_tracker = {}
    tracker_file = BASE_DIR / "data" / "knowledge" / "war_room_tracker.jsonl"
    if not tracker_file.exists():
        return _war_room_tracker
    for line in tracker_file.read_text(encoding="utf-8").strip().split("\n"):
        if not line:
            continue
        try:
            entry = json.loads(line)
            rid = entry.get("report_id", "")
            if rid:
                _war_room_tracker[rid] = entry
        except json.JSONDecodeError:
            continue
    return _war_room_tracker


def _get_benchmark_returns(report_date: datetime) -> dict | None:
    """拉取沪深300(000300.SH)在 report_date 后的 T+5/10/20 收益率，带缓存。"""
    key = report_date.strftime("%Y-%m-%d")
    if key in _benchmark_cache:
        return _benchmark_cache[key]
    try:
        from data.tushare_client import get_price_df
        bm_df, err = get_price_df("000300.SH", days=365)
        if err or bm_df is None or bm_df.empty:
            return None
        result = _calc_returns(bm_df, report_date, None)
        if result:
            _benchmark_cache[key] = result
        return result
    except Exception as exc:
        logger.warning("[outcome_tracker] benchmark fetch failed: %r", exc)
        return None


def _load_evaluated_ids():
    """加载已评估的 report_id 集合（从 SQLite）。"""
    global _evaluated_ids, _evaluated_loaded
    if _evaluated_loaded:
        return
    _evaluated_ids.clear()
    mgr = get_manager()
    with mgr.read("outcomes") as conn:
        for row in conn.execute("SELECT report_id FROM outcomes"):
            _evaluated_ids.add(row[0])
    # 兼容：如果 JSONL 存在但 DB 为空，从 JSONL 迁移
    if not _evaluated_ids and OUTCOMES_FILE.exists():
        _migrate_jsonl_to_db()
        # 迁移后重新加载
        with mgr.read("outcomes") as conn:
            for row in conn.execute("SELECT report_id FROM outcomes"):
                _evaluated_ids.add(row[0])
    _evaluated_loaded = True


# ── 核心: 评估待处理报告 ───────────────────────────────────────────

def evaluate_pending(min_days: int = 8) -> int:
    """评估所有超过 min_days 天且未评估的报告。返回新评估数量。"""
    from data.tushare_client import get_price_df

    _load_evaluated_ids()
    rows = _fetch_pending_reports(min_days)
    if not rows:
        return 0

    evaluated = 0
    for row in rows:
        if _evaluate_single_report(row, get_price_df):
            evaluated += 1
    return evaluated


def _fetch_pending_reports(min_days: int) -> list:
    """从 reports.db 获取待评估的报告行。"""
    if not DB_PATH.exists():
        logger.info("[outcome_tracker] reports.db not found, skip")
        return []

    cutoff = (datetime.now() - timedelta(days=min_days)).strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT report_id, stock_name, stock_code, summary, markdown_path, created_at "
            "FROM reports WHERE created_at <= ? ORDER BY created_at",
            (cutoff,),
        ).fetchall()

    if not rows:
        logger.info("[outcome_tracker] no pending reports to evaluate")
    return rows


def _evaluate_single_report(row, get_price_df) -> bool:
    """评估单个报告，成功返回 True。"""
    report_id = row["report_id"]
    if report_id in _evaluated_ids:
        return False

    stock_code = row["stock_code"]
    stock_name = row["stock_name"]
    created_at = row["created_at"]

    # 读取完整报告文本
    md_path = Path(row["markdown_path"])
    if not md_path.exists():
        logger.warning("[outcome_tracker] markdown not found: %s", md_path)
        return False
    report_text = md_path.read_text(encoding="utf-8")

    # 提取评分
    scores = _extract_scores(report_text)
    if not scores:
        logger.info("[outcome_tracker] no scores in report %s, skip", report_id[:8])
        return False

    direction = _infer_direction(scores)
    close_at_report = _extract_close_from_report(report_text)

    # 拉取后续行情
    try:
        report_date = datetime.strptime(created_at[:10], "%Y-%m-%d")
        price_df, err = get_price_df(stock_code)
        if err or price_df is None or price_df.empty:
            logger.warning("[outcome_tracker] price fetch failed for %s: %s", stock_code, err)
            return False
    except Exception as exc:
        logger.warning("[outcome_tracker] price fetch error for %s: %r", stock_code, exc)
        return False

    # 计算后续收益率
    returns = _calc_returns(price_df, report_date, close_at_report)
    if returns is None:
        return False

    # 判定是否命中
    is_bullish = direction == "bullish"
    is_directional = direction != "neutral"

    def _safe_hit(ret: float, reliable: bool) -> bool | None:
        if not is_directional or not reliable:
            return None
        return (ret > 0) == is_bullish

    outcome = {
        "report_id": report_id,
        "report_date": created_at[:10],
        "stock_code": stock_code,
        "stock_name": stock_name,
        "scores": {k: v for k, v in scores.items() if not k.startswith("_")},
        "weighted_score": scores.get("综合加权", 0),
        "direction": direction,
        "close_at_report": returns["close_at_report"],
        "return_5d": returns["return_5d"],
        "return_10d": returns["return_10d"],
        "return_20d": returns["return_20d"],
        "hit_5d": _safe_hit(returns["return_5d"], returns.get("reliable_5d", True)),
        "hit_10d": _safe_hit(returns["return_10d"], returns.get("reliable_10d", True)),
        "hit_20d": _safe_hit(returns["return_20d"], returns.get("reliable_20d", True)),
        "actual_trade_days": returns.get("actual_trade_days", 0),
        "evaluated_at": datetime.now().isoformat(timespec="seconds"),
    }

    # 补充沪深300基准收益（用于超额胜率计算）
    bm = _get_benchmark_returns(report_date)
    if bm:
        outcome["return_benchmark_10d"] = bm["return_10d"]
        outcome["beat_market_10d"] = returns["return_10d"] > bm["return_10d"] if direction == "bullish" else None

    # 补充 war_room_tracker 关联数据（多模型分歧度）
    tracker = _load_war_room_tracker()
    if report_id in tracker:
        wr = tracker[report_id]
        outcome["war_room_divergence"] = wr.get("divergence", 0)
        outcome["war_room_generals"] = wr.get("generals", {})

    _append_outcome(outcome)
    _evaluated_ids.add(report_id)
    logger.info(
        "[outcome_tracker] evaluated %s %s: score=%.1f dir=%s ret_10d=%.1f%% hit=%s",
        stock_name, stock_code, scores.get("综合加权", 0),
        direction, returns["return_10d"],
        outcome["hit_10d"],
    )
    return True


def _calc_returns(
    price_df, report_date: datetime, close_at_report: float | None
) -> dict | None:
    """计算 T+5/10/20 日收益率。"""
    try:
        # price_df 列名: 日期, 开盘, 最高, 最低, 收盘, 成交量, 涨跌幅
        df = price_df.copy()
        if "日期" in df.columns:
            df["_date"] = df["日期"].astype(str).str[:10]
        else:
            return None

        report_str = report_date.strftime("%Y-%m-%d")
        # 找到报告日期当天或之后的第一个交易日
        df_sorted = df.sort_values("_date").reset_index(drop=True)
        after = df_sorted[df_sorted["_date"] >= report_str]
        if after.empty:
            return None

        base_idx = after.index[0]
        base_close = float(after.iloc[0]["收盘"])
        if close_at_report and abs(close_at_report - base_close) / base_close < 0.15:
            base_close = close_at_report  # 用报告中记录的更准确

        max_offset = len(df_sorted) - 1 - base_idx  # 实际可用的交易日数

        def _get_return(offset: int) -> tuple[float, bool]:
            """返回 (收益率, 是否数据充足)"""
            target_idx = base_idx + offset
            if target_idx < len(df_sorted):
                future_close = float(df_sorted.iloc[target_idx]["收盘"])
                return round((future_close - base_close) / base_close * 100, 2), True
            # 数据不够：用最后一条，但标记为不可靠
            last_close = float(df_sorted.iloc[-1]["收盘"])
            return round((last_close - base_close) / base_close * 100, 2), False

        r5, r5_ok = _get_return(5)
        r10, r10_ok = _get_return(10)
        r20, r20_ok = _get_return(20)

        return {
            "close_at_report": base_close,
            "return_5d": r5,
            "return_10d": r10,
            "return_20d": r20,
            "actual_trade_days": max_offset,  # 实际可用交易日数
            "reliable_5d": r5_ok,
            "reliable_10d": r10_ok,
            "reliable_20d": r20_ok,
        }
    except Exception as exc:
        logger.warning("[outcome_tracker] _calc_returns error: %r", exc)
        return None


def _append_outcome(outcome: dict):
    """写入一条 outcome 到 SQLite（INSERT OR REPLACE）。"""
    def _bool_to_int(v):
        if v is None:
            return None
        return 1 if v else 0

    mgr = get_manager()
    with mgr.write("outcomes") as conn:
        conn.execute("""
            INSERT OR REPLACE INTO outcomes (
                report_id, report_date, stock_code, stock_name, source,
                scores, weighted_score, direction, close_at_report,
                return_5d, return_10d, return_20d,
                hit_5d, hit_10d, hit_20d,
                actual_trade_days, evaluated_at,
                return_benchmark_10d, beat_market_10d,
                war_room_divergence, war_room_generals
            ) VALUES (?,?,?,?,?, ?,?,?,?, ?,?,?, ?,?,?, ?,?, ?,?, ?,?)
        """, (
            outcome["report_id"], outcome["report_date"],
            outcome["stock_code"], outcome.get("stock_name", ""),
            outcome.get("source", "report"),
            json.dumps(outcome.get("scores", {}), ensure_ascii=False),
            outcome.get("weighted_score"),
            outcome.get("direction"),
            outcome.get("close_at_report"),
            outcome.get("return_5d"),
            outcome.get("return_10d"),
            outcome.get("return_20d"),
            _bool_to_int(outcome.get("hit_5d")),
            _bool_to_int(outcome.get("hit_10d")),
            _bool_to_int(outcome.get("hit_20d")),
            outcome.get("actual_trade_days"),
            outcome.get("evaluated_at"),
            outcome.get("return_benchmark_10d"),
            _bool_to_int(outcome.get("beat_market_10d")),
            outcome.get("war_room_divergence"),
            json.dumps(outcome.get("war_room_generals", {}), ensure_ascii=False),
        ))


# ── 查询接口 ──────────────────────────────────────────────────────

def load_outcomes(days: int = 0) -> list[dict]:
    """加载 outcome 记录（从 SQLite）。days=0 表示全部。"""
    mgr = get_manager()
    with mgr.read("outcomes") as conn:
        conn.row_factory = sqlite3.Row
        if days > 0:
            cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            rows = conn.execute(
                "SELECT * FROM outcomes WHERE report_date >= ? ORDER BY report_date",
                (cutoff,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM outcomes ORDER BY report_date").fetchall()
        conn.row_factory = None  # 恢复默认
        return [_row_to_outcome(dict(row)) for row in rows]


def get_accuracy_summary(days: int = 90) -> dict:
    """返回整体准确率统计，含分段统计和超额胜率。"""
    outcomes = load_outcomes(days=days)
    if not outcomes:
        return {"sample_count": 0}

    directional = [o for o in outcomes if o.get("direction") != "neutral"]
    total = len(directional)
    if total == 0:
        return {"sample_count": len(outcomes), "directional_count": 0}

    hit_5 = sum(1 for o in directional if o.get("hit_5d"))
    hit_10 = sum(1 for o in directional if o.get("hit_10d"))
    hit_20 = sum(1 for o in directional if o.get("hit_20d"))

    # 按评分段分：高(≥7) / 中(5-7) / 低(<5)
    def _bucket_stats(group: list) -> dict:
        n = len(group)
        if not n:
            return {"count": 0}
        return {
            "count": n,
            "hit_rate_10d": round(sum(1 for o in group if o.get("hit_10d")) / n * 100, 1),
            "avg_return_10d": round(sum(o.get("return_10d", 0) for o in group) / n, 2),
        }

    high_score = [o for o in directional if o.get("weighted_score", 0) >= 7]
    mid_score = [o for o in directional if 5 <= o.get("weighted_score", 0) < 7]
    low_score = [o for o in directional if o.get("weighted_score", 0) < 5]

    # 超额胜率：看多且10日收益 > 沪深300同期（有基准数据的样本）
    beat_eligible = [
        o for o in directional
        if o.get("direction") == "bullish" and o.get("return_benchmark_10d") is not None
    ]
    beat_count = sum(1 for o in beat_eligible if o.get("beat_market_10d"))

    result = {
        "sample_count": len(outcomes),
        "directional_count": total,
        "hit_rate_5d": round(hit_5 / total * 100, 1),
        "hit_rate_10d": round(hit_10 / total * 100, 1),
        "hit_rate_20d": round(hit_20 / total * 100, 1),
        "avg_return_10d": round(sum(o.get("return_10d", 0) for o in directional) / total, 2),
        "by_score_bucket": {
            "high_ge7": _bucket_stats(high_score),
            "mid_5to7": _bucket_stats(mid_score),
            "low_lt5": _bucket_stats(low_score),
        },
        # 保留旧字段兼容
        "high_score_count": len(high_score),
        "high_score_hit_10d": round(
            sum(1 for o in high_score if o.get("hit_10d")) / len(high_score) * 100, 1
        ) if high_score else 0,
    }

    if beat_eligible:
        result["beat_market_eligible"] = len(beat_eligible)
        result["beat_market_rate_10d"] = round(beat_count / len(beat_eligible) * 100, 1)

    return result


def get_stock_history(stock_code: str) -> list[dict]:
    """返回特定股票的历史分析结果（索引查询，无需全量加载）。"""
    mgr = get_manager()
    with mgr.read("outcomes") as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM outcomes WHERE stock_code = ? ORDER BY report_date",
            (stock_code,),
        ).fetchall()
        conn.row_factory = None
        return [_row_to_outcome(dict(row)) for row in rows]


def get_recent_scores_distribution(limit: int = 20) -> dict | None:
    """获取最近N次分析的评分分布（中位数/均值/分位数），用于评分相对锚定。"""
    mgr = get_manager()
    try:
        with mgr.read("outcomes") as conn:
            rows = conn.execute(
                "SELECT weighted_score FROM outcomes WHERE weighted_score IS NOT NULL "
                "ORDER BY report_date DESC LIMIT ?",
                (limit,),
            ).fetchall()

        if len(rows) < 5:
            return None

        scores = sorted([row[0] for row in rows])
        n = len(scores)
        return {
            "count": n,
            "median": scores[n // 2],
            "mean": sum(scores) / n,
            "p25": scores[n // 4],
            "p75": scores[3 * n // 4],
            "min": scores[0],
            "max": scores[-1],
        }
    except Exception:
        return None


# ── Top100 推荐结果追踪 ──────────────────────────────────────────

TOP10_CACHE_DIR = BASE_DIR / "Stock_top10" / "cache"


def evaluate_top100_pending(min_days: int = 8) -> int:
    """评估 Top100 推荐列表中超过 min_days 天的股票。返回新评估数量。"""
    from data.tushare_client import get_price_df

    _load_evaluated_ids()

    if not TOP10_CACHE_DIR.exists():
        logger.info("[outcome_tracker] top10 cache dir not found, skip")
        return 0

    cutoff_date = (datetime.now() - timedelta(days=min_days)).strftime("%Y-%m-%d")
    evaluated = 0

    # 扫描所有 Top100 结果文件
    for result_file in sorted(TOP10_CACHE_DIR.glob("*.json")):
        if "deep_status" in result_file.name.lower():
            continue

        try:
            data = json.loads(result_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        results = data.get("results", [])
        if not results:
            continue

        # 从文件名提取日期（格式: 2026-03-19_模型名.json）
        file_date = result_file.stem[:10]  # "2026-03-19"
        if file_date > cutoff_date:
            continue  # 还不够 min_days 天

        model_name = data.get("model", "")

        for rank, stock in enumerate(results[:100], start=1):
            stock_code_raw = str(stock.get("代码", ""))
            stock_name = str(stock.get("股票名称", ""))
            if not stock_code_raw:
                continue

            # 构造唯一 ID: top100_{日期}_{股票代码}
            ts_code = _normalize_top100_code(stock_code_raw)
            unique_id = f"top100_{file_date}_{ts_code}"
            if unique_id in _evaluated_ids:
                continue

            # 提取评分
            match_score = stock.get("综合匹配度", 0)
            try:
                match_score = float(match_score)
            except (ValueError, TypeError):
                match_score = 0

            # 综合匹配度是 0-100 制，转为 0-10 方便统一
            weighted_10 = round(match_score / 10, 1)
            direction = "bullish" if match_score >= 60 else ("neutral" if match_score >= 40 else "bearish")
            short_term = str(stock.get("短线建议", ""))

            # 提取子维度评分（0-10 制）
            sub_scores = {}
            for key in ["基本面", "题材热度", "技术面"]:
                val = stock.get(key)
                if val is not None:
                    try:
                        sub_scores[key] = float(val)
                    except (ValueError, TypeError):
                        pass

            # 获取最新价作为基准
            close_at_report = None
            try:
                close_at_report = float(stock.get("最新价", 0))
            except (ValueError, TypeError):
                pass

            # 拉取后续行情
            try:
                report_date = datetime.strptime(file_date, "%Y-%m-%d")
                price_df, err = get_price_df(ts_code)
                if err or price_df is None or price_df.empty:
                    continue
            except Exception as exc:
                logger.debug("[outcome_tracker] top100 evaluate row skip: %r", exc)
                continue

            returns = _calc_returns(price_df, report_date, close_at_report)
            if returns is None:
                continue

            is_bullish = direction == "bullish"
            is_dir = direction != "neutral"

            def _safe_hit_t100(ret: float, reliable: bool) -> bool | None:
                if not is_dir or not reliable:
                    return None
                return (ret > 0) == is_bullish

            # 将 Top100 子维度映射到标准四维（题材热度→预期差）
            mapped_scores = {
                "基本面": sub_scores.get("基本面", weighted_10),
                "预期差": sub_scores.get("题材热度", weighted_10),
                "资金面": weighted_10,  # Top100 无单独资金面，用综合代替
                "技术面": sub_scores.get("技术面", weighted_10),
            }

            outcome = {
                "report_id": unique_id,
                "report_date": file_date,
                "stock_code": ts_code,
                "stock_name": stock_name,
                "source": "top100",
                "rank": rank,
                "scores": mapped_scores,
                "weighted_score": weighted_10,
                "match_score_100": match_score,
                "short_term_advice": short_term,
                "direction": direction,
                "close_at_report": returns["close_at_report"],
                "return_5d": returns["return_5d"],
                "return_10d": returns["return_10d"],
                "return_20d": returns["return_20d"],
                "hit_5d": _safe_hit_t100(returns["return_5d"], returns.get("reliable_5d", True)),
                "hit_10d": _safe_hit_t100(returns["return_10d"], returns.get("reliable_10d", True)),
                "hit_20d": _safe_hit_t100(returns["return_20d"], returns.get("reliable_20d", True)),
                "actual_trade_days": returns.get("actual_trade_days", 0),
                "model": model_name,
                "evaluated_at": datetime.now().isoformat(timespec="seconds"),
            }

            _append_outcome(outcome)
            _evaluated_ids.add(unique_id)
            evaluated += 1

        if evaluated > 0:
            logger.info(
                "[outcome_tracker] evaluated %d top100 stocks from %s",
                evaluated, file_date,
            )

    return evaluated


def _normalize_top100_code(code: str) -> str:
    """将 Top100 中的股票代码标准化为 Tushare 格式。"""
    code = code.strip().upper()
    if "." in code:
        return code
    if code.startswith("6"):
        return f"{code}.SH"
    if code.startswith(("4", "8")):
        return f"{code}.BJ"
    return f"{code}.SZ"


def get_top100_accuracy(days: int = 90) -> dict:
    """返回 Top100 推荐的准确率统计（按排名段分）。"""
    outcomes = [o for o in load_outcomes(days=days) if o.get("source") == "top100"]
    if not outcomes:
        return {"sample_count": 0}

    directional = [o for o in outcomes if o.get("direction") != "neutral"]
    total = len(directional)

    # 按排名段分
    top10 = [o for o in directional if o.get("rank", 999) <= 10]
    top30 = [o for o in directional if o.get("rank", 999) <= 30]
    top100 = directional

    def _bucket_stats(group: list) -> dict:
        n = len(group)
        if not n:
            return {"total": 0}
        return {
            "total": n,
            "hit_rate_5d": round(sum(1 for o in group if o.get("hit_5d")) / n * 100, 1),
            "hit_rate_10d": round(sum(1 for o in group if o.get("hit_10d")) / n * 100, 1),
            "avg_return_5d": round(sum(o.get("return_5d", 0) for o in group) / n, 2),
            "avg_return_10d": round(sum(o.get("return_10d", 0) for o in group) / n, 2),
        }

    return {
        "sample_count": len(outcomes),
        "top10": _bucket_stats(top10),
        "top30": _bucket_stats(top30),
        "top100": _bucket_stats(top100),
    }
