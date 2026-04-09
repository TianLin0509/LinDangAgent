# -*- coding: utf-8 -*-
"""盘感日记 — 每日K线观察 + 预测 + 回溯验证 + 形态胜率统计

核心循环（scheduler 每日调用）：
  1. scan_and_observe(): 扫描关注股票，识别形态，记录观察+预测
  2. backtest_pending(): 回溯T-5天的观察，检查实际走势
  3. rebuild_pattern_stats(): 重算形态×环境×位置的胜率统计

数据存储：SQLite data/knowledge/kline_diary.db
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


# ── 1. 每日扫描观察 ──────────────────────────────────────────────

def scan_and_observe(stock_list: list[tuple[str, str]] = None) -> int:
    """扫描股票列表，识别K线形态，记录观察。

    stock_list: [(ts_code, stock_name), ...] 如果为None，从关注清单+近期报告获取。
    返回新增观察数。
    """
    import uuid

    if stock_list is None:
        stock_list = _get_observation_targets()

    if not stock_list:
        logger.info("[kline_diary] no stocks to observe")
        return 0

    from knowledge.kline_patterns import detect_all_patterns, classify_position, classify_volume_state

    # 获取当前环境
    regime = "shock"
    try:
        from knowledge.regime_detector import get_current_regime
        r = get_current_regime()
        regime = r.get("regime", "shock") if r else "shock"
    except Exception as exc:
        logger.debug("[kline_diary] regime fetch failed: %r", exc)

    from knowledge.kb_db import get_manager
    mgr = get_manager()

    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().isoformat(timespec="seconds")
    observed = 0

    for ts_code, stock_name in stock_list:
        # 检查今日是否已观察
        with mgr.read("kline_diary") as conn:
            exists = conn.execute(
                "SELECT 1 FROM kline_observations WHERE date=? AND stock_code=?",
                (today, ts_code),
            ).fetchone()

        if exists:
            continue

        # 获取K线数据
        try:
            from data.tushare_client import get_price_df
            df, _err = get_price_df(ts_code, days=60)
            if df is None or len(df) < 10:
                continue
        except Exception as exc:
            logger.debug("[kline_diary] price fetch failed for %s: %r", ts_code, exc)
            continue

        # 识别形态
        patterns = detect_all_patterns(df)
        if not patterns:
            continue  # 无明显形态，不记录

        position = classify_position(df)
        volume_state = classify_volume_state(df)

        # 查历史胜率作为预测置信度
        pattern_ids = [p.pattern_id for p in patterns]
        prediction, confidence = _generate_prediction(pattern_ids, regime, position)

        obs_id = str(uuid.uuid4())[:8]
        with mgr.write("kline_diary") as conn:
            conn.execute(
                "INSERT INTO kline_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (obs_id, today, ts_code, stock_name,
                 json.dumps(pattern_ids), regime, position, volume_state,
                 prediction, confidence,
                 None, None, now),
            )

        pattern_names = [p.name for p in patterns]
        logger.info("[kline_diary] observed %s: %s (%s %s %s) → %s",
                    stock_name, "+".join(pattern_names), regime, position, volume_state, prediction)
        observed += 1

    logger.info("[kline_diary] scan complete: %d new observations", observed)
    return observed


def _get_observation_targets() -> list[tuple[str, str]]:
    """获取待观察的股票列表：关注清单 + 近7天分析过的股票"""
    targets = set()

    # 关注清单
    try:
        from knowledge.session_handoff import get_watchlist
        for w in get_watchlist():
            targets.add((w["stock_code"], w["stock_name"]))
    except Exception as exc:
        logger.debug("[kline_diary] watchlist scan failed: %r", exc)

    # 近7天分析过的报告
    try:
        import sqlite3 as _sql
        db = Path(__file__).resolve().parent.parent / "storage" / "reports.db"
        if db.exists():
            conn = _sql.connect(str(db))
            cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
            rows = conn.execute(
                "SELECT DISTINCT stock_code, stock_name FROM reports "
                "WHERE created_at >= ? AND stock_code IS NOT NULL AND stock_code != ''",
                (cutoff,),
            ).fetchall()
            conn.close()
            for code, name in rows:
                if code and name:
                    targets.add((code, name))
    except Exception as exc:
        logger.debug("[kline_diary] recent analysis scan failed: %r", exc)

    return list(targets)[:20]  # 最多观察20只


def _generate_prediction(pattern_ids: list[str], regime: str, position: str) -> tuple[str, float]:
    """基于历史胜率生成预测。"""
    stats = get_pattern_stats(pattern_ids, regime, position)

    if not stats:
        # 无历史数据，根据形态本身给默认判断
        bullish_patterns = {"hammer", "bullish_engulf", "piercing_line", "morning_star",
                           "three_soldiers", "vol_breakout", "shrink_pullback",
                           "vol_bot_diverge", "macd_bull_div", "rsi_oversold"}
        bullish = sum(1 for p in pattern_ids if p in bullish_patterns)
        bearish = len(pattern_ids) - bullish
        if bullish > bearish:
            return "偏多（无历史数据，基于形态特征）", 0.55
        elif bearish > bullish:
            return "偏空（无历史数据，基于形态特征）", 0.55
        return "中性", 0.5

    # 加权平均胜率
    total_weight = sum(s["sample_count"] for s in stats)
    if total_weight == 0:
        return "中性", 0.5

    weighted_rate = sum(s["win_rate_5d"] * s["sample_count"] for s in stats) / total_weight
    weighted_return = sum(s["avg_return_5d"] * s["sample_count"] for s in stats) / total_weight
    total_samples = sum(s["sample_count"] for s in stats)

    if weighted_rate > 55:
        direction = f"偏多(历史胜率{weighted_rate:.0f}%，均涨{weighted_return:+.1f}%，{total_samples}样本)"
    elif weighted_rate < 45:
        direction = f"偏空(历史胜率{weighted_rate:.0f}%，均{weighted_return:+.1f}%，{total_samples}样本)"
    else:
        direction = f"中性(历史胜率{weighted_rate:.0f}%，{total_samples}样本)"

    confidence = min(0.5 + abs(weighted_rate - 50) / 100 + total_samples / 200, 0.9)
    return direction, confidence


# ── 2. 回溯验证 ──────────────────────────────────────────────────

def backtest_pending(days_ago: int = 5) -> int:
    """回溯T-N天的观察，填入实际收益和命中状态。返回更新数量。"""
    from knowledge.kb_db import get_manager
    mgr = get_manager()
    cutoff = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    cutoff_max = (datetime.now() - timedelta(days=days_ago - 2)).strftime("%Y-%m-%d")

    with mgr.read("kline_diary") as conn:
        rows = conn.execute(
            "SELECT obs_id, stock_code, date FROM kline_observations "
            "WHERE actual_return_5d IS NULL AND date <= ? AND date >= ?",
            (cutoff, cutoff_max),
        ).fetchall()

    if not rows:
        return 0

    updated = 0
    for obs_id, stock_code, obs_date in rows:
        try:
            from data.tushare_client import get_price_df
            df, _err = get_price_df(stock_code, days=30)
            if df is None or len(df) < 5:
                continue

            # 找到观察日的收盘价
            df["trade_date_str"] = df["trade_date"].astype(str) if "trade_date" in df.columns else ""
            obs_close = None
            future_close = None

            for i, row in df.iterrows():
                date_str = str(row.get("trade_date", ""))[:10]
                if date_str == obs_date.replace("-", ""):
                    obs_close = row["close"]
                    # 往后找5个交易日
                    future_idx = df.index.get_loc(i) + 5
                    if future_idx < len(df):
                        future_close = df.iloc[future_idx]["close"]
                    break

            if obs_close is not None and future_close is not None:
                ret_5d = (future_close - obs_close) / obs_close * 100
                # hit 判断应考虑预测方向（从 prediction 字段推断）
                # 默认：涨了算 hit（兼容无方向信息的旧数据）
                hit = 1 if ret_5d > 0 else 0

                with mgr.write("kline_diary") as conn:
                    conn.execute(
                        "UPDATE kline_observations SET actual_return_5d=?, hit=? WHERE obs_id=?",
                        (round(ret_5d, 2), hit, obs_id),
                    )

                updated += 1
                logger.info("[kline_diary] backtest %s: 5d return %.1f%% %s",
                            stock_code, ret_5d, "✅" if hit else "❌")

        except Exception as exc:
            logger.debug("[kline_diary] backtest failed for %s: %r", stock_code, exc)

    logger.info("[kline_diary] backtest complete: %d updated", updated)
    return updated


# ── 3. 重算形态统计 ──────────────────────────────────────────────

def rebuild_pattern_stats() -> int:
    """从已验证的观察中重算形态×环境×位置的胜率统计。"""
    from knowledge.kb_db import get_manager
    mgr = get_manager()

    with mgr.read("kline_diary") as conn:
        rows = conn.execute(
            "SELECT patterns, regime, position, actual_return_5d, hit "
            "FROM kline_observations WHERE actual_return_5d IS NOT NULL",
        ).fetchall()

    if not rows:
        return 0

    # 聚合
    stats = {}  # (pattern, regime, position) → {samples, wins, returns}
    for patterns_json, regime, position, ret_5d, hit in rows:
        patterns = json.loads(patterns_json) if patterns_json else []
        for pattern in patterns:
            key = (pattern, regime, position)
            if key not in stats:
                stats[key] = {"samples": 0, "wins": 0, "returns": []}
            stats[key]["samples"] += 1
            if hit:
                stats[key]["wins"] += 1
            stats[key]["returns"].append(ret_5d)

    # 写入统计表
    now = datetime.now().isoformat(timespec="seconds")
    with mgr.write("kline_diary") as conn:
        conn.execute("DELETE FROM kline_pattern_stats")
        for (pattern, regime, position), data in stats.items():
            n = data["samples"]
            win_rate = data["wins"] / n * 100 if n > 0 else 0
            avg_ret = sum(data["returns"]) / n if n > 0 else 0
            conn.execute(
                "INSERT INTO kline_pattern_stats VALUES (?,?,?,?,?,?,?,?)",
                (pattern, regime, position, n, data["wins"],
                 round(win_rate, 1), round(avg_ret, 2), now),
            )

    logger.info("[kline_diary] pattern stats rebuilt: %d entries", len(stats))

    # 自动触发形态组合发现
    try:
        discover_combo_patterns()
    except Exception as exc:
        logger.debug("[kline_diary] combo discovery failed: %r", exc)

    return len(stats)


# ── 查询接口 ─────────────────────────────────────────────────────

def get_pattern_stats(pattern_ids: list[str], regime: str = "",
                      position: str = "") -> list[dict]:
    """查询指定形态在指定环境/位置下的历史胜率。"""
    if not pattern_ids:
        return []

    from knowledge.kb_db import get_manager
    mgr = get_manager()
    with mgr.read("kline_diary") as conn:
        placeholders = ",".join("?" * len(pattern_ids))
        query = f"SELECT * FROM kline_pattern_stats WHERE pattern IN ({placeholders})"
        params = list(pattern_ids)

        if regime:
            query += " AND regime=?"
            params.append(regime)
        if position:
            query += " AND position=?"
            params.append(position)

        rows = conn.execute(query, params).fetchall()

    return [
        {
            "pattern": r[0], "regime": r[1], "position": r[2],
            "sample_count": r[3], "win_count": r[4],
            "win_rate_5d": r[5], "avg_return_5d": r[6],
        }
        for r in rows
    ]


def get_recent_observations(days: int = 7, limit: int = 20) -> list[dict]:
    """获取近期盘感观察记录。"""
    from knowledge.kb_db import get_manager
    mgr = get_manager()
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    with mgr.read("kline_diary") as conn:
        rows = conn.execute(
            "SELECT date, stock_code, stock_name, patterns, regime, position, "
            "volume_state, prediction, actual_return_5d, hit "
            "FROM kline_observations WHERE date >= ? ORDER BY date DESC LIMIT ?",
            (cutoff, limit),
        ).fetchall()

    from knowledge.kline_patterns import PATTERN_INFO
    results = []
    for r in rows:
        pattern_ids = json.loads(r[3]) if r[3] else []
        pattern_names = [PATTERN_INFO.get(p, (p, ""))[0] for p in pattern_ids]
        hit_mark = ""
        if r[9] is not None:
            hit_mark = "✅" if r[9] else "❌"

        results.append({
            "date": r[0], "stock_code": r[1], "stock_name": r[2],
            "patterns": pattern_names, "regime": r[4], "position": r[5],
            "volume_state": r[6], "prediction": r[7],
            "actual_return_5d": r[8],
            "hit": hit_mark,
        })
    return results


def get_diary_stats() -> dict:
    """盘感日记统计概览。"""
    from knowledge.kb_db import get_manager
    mgr = get_manager()
    with mgr.read("kline_diary") as conn:
        total = conn.execute("SELECT COUNT(*) FROM kline_observations").fetchone()[0]
        verified = conn.execute(
            "SELECT COUNT(*) FROM kline_observations WHERE actual_return_5d IS NOT NULL"
        ).fetchone()[0]
        hits = conn.execute(
            "SELECT COUNT(*) FROM kline_observations WHERE hit=1"
        ).fetchone()[0]
        stats_count = conn.execute("SELECT COUNT(*) FROM kline_pattern_stats").fetchone()[0]

        # 最准和最不准的形态
        best = conn.execute(
            "SELECT pattern, regime, position, win_rate_5d, sample_count "
            "FROM kline_pattern_stats WHERE sample_count >= 3 "
            "ORDER BY win_rate_5d DESC LIMIT 3"
        ).fetchall()
        worst = conn.execute(
            "SELECT pattern, regime, position, win_rate_5d, sample_count "
            "FROM kline_pattern_stats WHERE sample_count >= 3 "
            "ORDER BY win_rate_5d ASC LIMIT 3"
        ).fetchall()

    from knowledge.kline_patterns import PATTERN_INFO

    return {
        "total_observations": total,
        "verified": verified,
        "hit_rate": round(hits / verified * 100, 1) if verified > 0 else 0,
        "pattern_stats_count": stats_count,
        "best_patterns": [
            {"pattern": PATTERN_INFO.get(r[0], (r[0], ""))[0],
             "regime": r[1], "position": r[2],
             "win_rate": r[3], "samples": r[4]}
            for r in best
        ],
        "worst_patterns": [
            {"pattern": PATTERN_INFO.get(r[0], (r[0], ""))[0],
             "regime": r[1], "position": r[2],
             "win_rate": r[3], "samples": r[4]}
            for r in worst
        ],
    }


def get_cross_stock_pattern_peers(pattern_ids: list[str], regime: str = "",
                                   exclude_code: str = "", days: int = 7,
                                   limit: int = 5) -> list[dict]:
    """查询近N天出现同样形态的其他股票及实际走势（跨股盘感）。

    返回: [{stock_name, stock_code, date, patterns, regime, position,
            actual_return_5d, hit}, ...]
    """
    if not pattern_ids:
        return []

    from knowledge.kb_db import get_manager
    mgr = get_manager()
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    with mgr.read("kline_diary") as conn:
        rows = conn.execute(
            "SELECT date, stock_code, stock_name, patterns, regime, position, "
            "volume_state, actual_return_5d, hit "
            "FROM kline_observations WHERE date >= ? "
            "ORDER BY date DESC",
            (cutoff,),
        ).fetchall()

    from knowledge.kline_patterns import PATTERN_INFO

    target_set = set(pattern_ids)
    peers = []
    for r in rows:
        code = r[1]
        if code == exclude_code:
            continue

        obs_patterns = json.loads(r[3]) if r[3] else []
        obs_set = set(obs_patterns)

        # 至少有一个形态重合
        overlap = target_set & obs_set
        if not overlap:
            continue

        # 同环境优先（但不硬过滤）
        regime_match = (r[4] == regime) if regime else True

        pattern_names = [PATTERN_INFO.get(p, (p, ""))[0] for p in obs_patterns]
        hit_mark = ""
        if r[8] is not None:
            hit_mark = "✅" if r[8] else "❌"

        peers.append({
            "date": r[0], "stock_code": code, "stock_name": r[2],
            "patterns": pattern_names, "pattern_ids": obs_patterns,
            "regime": r[4], "position": r[5], "volume_state": r[6],
            "actual_return_5d": r[7], "hit": hit_mark,
            "overlap_count": len(overlap),
            "regime_match": regime_match,
        })

    # 排序：同环境+重合度高+有验证结果的优先
    peers.sort(key=lambda x: (
        x["regime_match"],
        x["overlap_count"],
        x["actual_return_5d"] is not None,
    ), reverse=True)

    return peers[:limit]


def get_patterns_for_stock(stock_code: str, regime: str = "") -> list[dict]:
    """获取某只股票当前形态对应的历史统计（供 injector 使用）。"""
    try:
        from data.tushare_client import get_price_df
        from knowledge.kline_patterns import detect_all_patterns, classify_position

        df, _err = get_price_df(stock_code, days=60)
        if df is None or len(df) < 10:
            return []

        patterns = detect_all_patterns(df)
        if not patterns:
            return []

        position = classify_position(df)
        pattern_ids = [p.pattern_id for p in patterns]

        stats = get_pattern_stats(pattern_ids, regime=regime, position=position)
        # 没有精确匹配时放宽到只按形态查
        if not stats:
            stats = get_pattern_stats(pattern_ids)

        return stats
    except Exception as exc:
        logger.warning("[kline_diary] cross stock pattern query failed: %r", exc)
        return []


# ── 4. 形态组合自发现（纯统计）────────────────────────────────────

def discover_combo_patterns(min_samples: int = 5, min_deviation: float = 15.0) -> int:
    """从已验证观察中发现高胜率/低胜率的形态组合。

    按 [排序后的形态组合 × 环境 × 位置 × 量能] 分桶，
    找胜率偏离基线≥min_deviation个百分点且样本≥min_samples的组合。
    纯统计，零 AI 成本。

    返回新发现的组合数量。
    """
    import uuid
    from knowledge.kb_db import get_manager
    mgr = get_manager()

    with mgr.read("kline_diary") as conn:
        rows = conn.execute(
            "SELECT patterns, regime, position, volume_state, actual_return_5d, hit "
            "FROM kline_observations WHERE actual_return_5d IS NOT NULL",
        ).fetchall()

    if len(rows) < min_samples * 2:
        return 0

    # 计算基线胜率
    total_hits = sum(1 for r in rows if r[5])
    baseline = total_hits / len(rows) * 100 if rows else 50

    # 按组合键分桶
    combos = {}  # combo_key → {samples, wins, returns}
    for patterns_json, regime, position, vol_state, ret_5d, hit in rows:
        patterns = sorted(json.loads(patterns_json)) if patterns_json else []
        if not patterns:
            continue

        # 组合键：排序后的形态+环境+位置+量能
        combo_key = "+".join(patterns) + f"|{regime}|{position}|{vol_state}"

        if combo_key not in combos:
            combos[combo_key] = {
                "patterns": patterns, "regime": regime,
                "position": position, "volume_state": vol_state,
                "samples": 0, "wins": 0, "returns": [],
            }
        combos[combo_key]["samples"] += 1
        if hit:
            combos[combo_key]["wins"] += 1
        combos[combo_key]["returns"].append(ret_5d)

    # 筛选显著偏离基线的组合
    now = datetime.now().isoformat(timespec="seconds")
    new_count = 0

    with mgr.write("kline_diary") as conn:
        for combo_key, data in combos.items():
            n = data["samples"]
            if n < min_samples:
                continue

            win_rate = data["wins"] / n * 100
            avg_ret = sum(data["returns"]) / n
            deviation = abs(win_rate - baseline)

            if deviation < min_deviation:
                continue

            # 检查是否已发现
            exists = conn.execute(
                "SELECT 1 FROM discovered_patterns WHERE combo_key=?",
                (combo_key,),
            ).fetchone()

            if exists:
                # 更新统计
                conn.execute(
                    "UPDATE discovered_patterns SET sample_count=?, win_rate_5d=?, "
                    "avg_return_5d=? WHERE combo_key=?",
                    (n, round(win_rate, 1), round(avg_ret, 2), combo_key),
                )
            else:
                did = str(uuid.uuid4())[:8]
                conn.execute(
                    "INSERT INTO discovered_patterns VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (did, combo_key,
                     json.dumps(data["patterns"]), data["regime"],
                     data["position"], data["volume_state"],
                     n, round(win_rate, 1), round(avg_ret, 2),
                     "", "", now, 0),
                )
                new_count += 1
                logger.info("[kline_diary] discovered: %s (胜率%.0f%% %d样本)",
                            combo_key, win_rate, n)

    logger.info("[kline_diary] discovery complete: %d new combos found", new_count)
    return new_count


def get_discovered_patterns(only_verified: bool = False) -> list[dict]:
    """获取所有发现的形态组合。"""
    from knowledge.kb_db import get_manager
    mgr = get_manager()
    with mgr.read("kline_diary") as conn:
        query = "SELECT * FROM discovered_patterns"
        if only_verified:
            query += " WHERE verified=1"
        query += " ORDER BY win_rate_5d DESC"
        rows = conn.execute(query).fetchall()

    from knowledge.kline_patterns import PATTERN_INFO
    results = []
    for r in rows:
        pattern_ids = json.loads(r[2]) if r[2] else []
        pattern_names = [PATTERN_INFO.get(p, (p, ""))[0] for p in pattern_ids]
        results.append({
            "discovered_id": r[0], "combo_key": r[1],
            "patterns": pattern_names, "pattern_ids": pattern_ids,
            "regime": r[3], "position": r[4], "volume_state": r[5],
            "sample_count": r[6], "win_rate_5d": r[7], "avg_return_5d": r[8],
            "ai_name": r[9], "ai_explanation": r[10],
            "discovered_at": r[11], "verified": bool(r[12]),
        })
    return results


# ── 5. AI 解释发现的形态（月度，Claude Sonnet）──────────────────

INTERPRET_SYSTEM = (
    "你是林铛，一个正在成长的 AI 投研分析师。"
    "你在分析自己从实战数据中发现的K线形态组合，判断其背后的市场逻辑。"
    "用第一人称，语气冷峻务实。只输出 JSON 数组。"
)

INTERPRET_PROMPT = """以下是我从实战数据中统计发现的形态组合，胜率显著偏离基线。
请为每个组合：(1)起一个简洁的中文名字 (2)解释市场逻辑 (3)判断是否有持续性

基线胜率：约50%

{discoveries_text}

输出严格 JSON 数组：
[
  {{
    "combo_key": "原始combo_key",
    "ai_name": "简洁中文名（4-8字，如'底部缩量犹豫星'）",
    "ai_explanation": "市场逻辑解释（50-100字）",
    "verified": true或false（你认为这个规律是否有结构性原因，值得持续跟踪）
  }}
]
"""


def ai_interpret_discoveries(max_interpret: int = 5) -> int:
    """用 Claude Sonnet 解释新发现的形态组合。月度调用。

    返回成功解释的数量。
    """
    from ai.client import call_ai, get_ai_client
    from knowledge.kb_utils import parse_ai_json

    # 找未解释的发现
    discoveries = get_discovered_patterns()
    uninterpreted = [d for d in discoveries if not d["ai_name"] and d["sample_count"] >= 5]

    if not uninterpreted:
        logger.info("[kline_diary] no new discoveries to interpret")
        return 0

    uninterpreted = uninterpreted[:max_interpret]

    # 构建 prompt
    lines = []
    for d in uninterpreted:
        patterns_str = " + ".join(d["patterns"])
        lines.append(
            f"- combo_key: {d['combo_key']}\n"
            f"  形态: {patterns_str}\n"
            f"  环境: {d['regime']} | 位置: {d['position']} | 量能: {d['volume_state']}\n"
            f"  统计: {d['sample_count']}样本, 胜率{d['win_rate_5d']:.0f}%, "
            f"均收益{d['avg_return_5d']:+.1f}%\n"
        )

    prompt = INTERPRET_PROMPT.format(discoveries_text="\n".join(lines))

    model_name = "⚡ Claude Sonnet（MAX）"
    client, cfg, err = get_ai_client(model_name)
    if err and not cfg:
        logger.warning("[kline_diary] Claude Sonnet unavailable: %s", err)
        return 0

    cfg_no_search = {**cfg, "supports_search": False}
    text, call_err = call_ai(client, cfg_no_search, prompt, system=INTERPRET_SYSTEM, max_tokens=1500)
    if call_err:
        logger.warning("[kline_diary] AI interpret failed: %s", call_err)
        return 0

    # 解析 JSON
    results = parse_ai_json(text)
    if not isinstance(results, list):
        logger.warning("[kline_diary] AI interpret JSON parse failed or not a list")
        return 0

    # 写回数据库
    from knowledge.kb_db import get_manager
    updated = 0
    mgr = get_manager()
    with mgr.write("kline_diary") as conn:
        for item in results:
            combo_key = item.get("combo_key", "")
            ai_name = item.get("ai_name", "")
            ai_explanation = item.get("ai_explanation", "")
            verified = 1 if item.get("verified") else 0

            if combo_key and ai_name:
                conn.execute(
                    "UPDATE discovered_patterns SET ai_name=?, ai_explanation=?, verified=? "
                    "WHERE combo_key=?",
                    (ai_name, ai_explanation, verified, combo_key),
                )
                updated += 1
                logger.info("[kline_diary] interpreted: %s → %s (verified=%d)",
                            combo_key[:30], ai_name, verified)

    logger.info("[kline_diary] AI interpretation complete: %d/%d", updated, len(uninterpreted))
    return updated
