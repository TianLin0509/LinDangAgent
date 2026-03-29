"""结果追踪 — 对比 AI 报告预测与实际行情

扫描 reports.db 中 T+8 天以上的报告，拉取后续实际行情，
计算 5/10/20 日收益率，与 AI 评分/方向对比，写入 outcomes.jsonl。
"""

import json
import logging
import re
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
KNOWLEDGE_DIR = BASE_DIR / "data" / "knowledge"
OUTCOMES_FILE = KNOWLEDGE_DIR / "outcomes.jsonl"
DB_PATH = BASE_DIR / "storage" / "reports.db"

_lock = threading.Lock()

# ── 评分提取（复用 analysis_service 的逻辑）──────────────────────────

SCORE_WEIGHTS = {
    "基本面": 0.15,
    "预期差": 0.35,
    "资金面": 0.30,
    "技术面": 0.20,
}


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
    weighted = sum(scores.get(d, 5) * w for d, w in SCORE_WEIGHTS.items())
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


# ── 已评估记录缓存 ─────────────────────────────────────────────────

_evaluated_ids: set[str] = set()
_evaluated_loaded = False


def _load_evaluated_ids():
    """加载已评估的 report_id 集合。"""
    global _evaluated_ids, _evaluated_loaded
    if _evaluated_loaded:
        return
    _evaluated_ids.clear()
    if OUTCOMES_FILE.exists():
        for line in OUTCOMES_FILE.read_text(encoding="utf-8").strip().split("\n"):
            if not line:
                continue
            try:
                entry = json.loads(line)
                rid = entry.get("report_id", "")
                if rid:
                    _evaluated_ids.add(rid)
            except json.JSONDecodeError:
                continue
    _evaluated_loaded = True


# ── 核心: 评估待处理报告 ───────────────────────────────────────────

def evaluate_pending(min_days: int = 8) -> int:
    """评估所有超过 min_days 天且未评估的报告。返回新评估数量。"""
    from data.tushare_client import get_price_df

    _load_evaluated_ids()

    if not DB_PATH.exists():
        logger.info("[outcome_tracker] reports.db not found, skip")
        return 0

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
        return 0

    evaluated = 0
    for row in rows:
        report_id = row["report_id"]
        if report_id in _evaluated_ids:
            continue

        stock_code = row["stock_code"]
        stock_name = row["stock_name"]
        created_at = row["created_at"]

        # 读取完整报告文本
        md_path = Path(row["markdown_path"])
        if not md_path.exists():
            logger.warning("[outcome_tracker] markdown not found: %s", md_path)
            continue
        report_text = md_path.read_text(encoding="utf-8")

        # 提取评分
        scores = _extract_scores(report_text)
        if not scores:
            logger.info("[outcome_tracker] no scores in report %s, skip", report_id[:8])
            continue

        direction = _infer_direction(scores)
        close_at_report = _extract_close_from_report(report_text)

        # 拉取后续行情
        try:
            report_date = datetime.strptime(created_at[:10], "%Y-%m-%d")
            price_df, err = get_price_df(stock_code)
            if err or price_df is None or price_df.empty:
                logger.warning("[outcome_tracker] price fetch failed for %s: %s", stock_code, err)
                continue
        except Exception as exc:
            logger.warning("[outcome_tracker] price fetch error for %s: %r", stock_code, exc)
            continue

        # 计算后续收益率
        returns = _calc_returns(price_df, report_date, close_at_report)
        if returns is None:
            continue

        # 判定是否命中
        is_bullish = direction == "bullish"
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
            "hit_5d": (returns["return_5d"] > 0) == is_bullish if direction != "neutral" else None,
            "hit_10d": (returns["return_10d"] > 0) == is_bullish if direction != "neutral" else None,
            "hit_20d": (returns["return_20d"] > 0) == is_bullish if direction != "neutral" else None,
            "evaluated_at": datetime.now().isoformat(timespec="seconds"),
        }

        _append_outcome(outcome)
        _evaluated_ids.add(report_id)
        evaluated += 1
        logger.info(
            "[outcome_tracker] evaluated %s %s: score=%.1f dir=%s ret_10d=%.1f%% hit=%s",
            stock_name, stock_code, scores.get("综合加权", 0),
            direction, returns["return_10d"],
            outcome["hit_10d"],
        )

    return evaluated


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

        def _get_return(offset: int) -> float:
            target_idx = base_idx + offset
            if target_idx < len(df_sorted):
                future_close = float(df_sorted.iloc[target_idx]["收盘"])
                return round((future_close - base_close) / base_close * 100, 2)
            # 数据不够则用最后一条
            last_close = float(df_sorted.iloc[-1]["收盘"])
            return round((last_close - base_close) / base_close * 100, 2)

        return {
            "close_at_report": base_close,
            "return_5d": _get_return(5),
            "return_10d": _get_return(10),
            "return_20d": _get_return(20),
        }
    except Exception as exc:
        logger.warning("[outcome_tracker] _calc_returns error: %r", exc)
        return None


def _append_outcome(outcome: dict):
    """追加一条 outcome 到 JSONL 文件。"""
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    with _lock:
        with open(OUTCOMES_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(outcome, ensure_ascii=False) + "\n")


# ── 查询接口 ──────────────────────────────────────────────────────

def load_outcomes(days: int = 0) -> list[dict]:
    """加载所有 outcome 记录。days=0 表示全部。"""
    if not OUTCOMES_FILE.exists():
        return []
    results = []
    cutoff = ""
    if days > 0:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    for line in OUTCOMES_FILE.read_text(encoding="utf-8").strip().split("\n"):
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if cutoff and entry.get("report_date", "") < cutoff:
            continue
        results.append(entry)
    return results


def get_accuracy_summary(days: int = 90) -> dict:
    """返回整体准确率统计。"""
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

    # 按评分段分
    high_score = [o for o in directional if o.get("weighted_score", 0) >= 7]
    high_hit_10 = sum(1 for o in high_score if o.get("hit_10d"))

    return {
        "sample_count": len(outcomes),
        "directional_count": total,
        "hit_rate_5d": round(hit_5 / total * 100, 1) if total else 0,
        "hit_rate_10d": round(hit_10 / total * 100, 1) if total else 0,
        "hit_rate_20d": round(hit_20 / total * 100, 1) if total else 0,
        "high_score_count": len(high_score),
        "high_score_hit_10d": round(high_hit_10 / len(high_score) * 100, 1) if high_score else 0,
        "avg_return_10d": round(sum(o.get("return_10d", 0) for o in directional) / total, 2) if total else 0,
    }


def get_stock_history(stock_code: str) -> list[dict]:
    """返回特定股票的历史分析结果。"""
    outcomes = load_outcomes()
    return [o for o in outcomes if o.get("stock_code") == stock_code]


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
            except Exception:
                continue

            returns = _calc_returns(price_df, report_date, close_at_report)
            if returns is None:
                continue

            is_bullish = direction == "bullish"
            outcome = {
                "report_id": unique_id,
                "report_date": file_date,
                "stock_code": ts_code,
                "stock_name": stock_name,
                "source": "top100",
                "rank": rank,
                "scores": sub_scores,
                "weighted_score": weighted_10,
                "match_score_100": match_score,
                "short_term_advice": short_term,
                "direction": direction,
                "close_at_report": returns["close_at_report"],
                "return_5d": returns["return_5d"],
                "return_10d": returns["return_10d"],
                "return_20d": returns["return_20d"],
                "hit_5d": (returns["return_5d"] > 0) == is_bullish if direction != "neutral" else None,
                "hit_10d": (returns["return_10d"] > 0) == is_bullish if direction != "neutral" else None,
                "hit_20d": (returns["return_20d"] > 0) == is_bullish if direction != "neutral" else None,
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
