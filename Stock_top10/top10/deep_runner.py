"""Async Top10 pipeline based on full research reports."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import date, datetime

import pandas as pd


logger = logging.getLogger(__name__)

_STATUS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cache")
os.makedirs(_STATUS_DIR, exist_ok=True)

_running_lock = threading.Lock()
_is_running = False
_STALE_STATUS_SECONDS = 20 * 60

# Unicode escape to avoid emoji encoding issues on Windows
_DEFAULT_MODEL = "\U0001f7e3 \u8c46\u5305 \u00b7 Seed 2.0 Pro"  # 🟣 豆包 · Seed 2.0 Pro


def _status_file() -> str:
    return os.path.join(_STATUS_DIR, f"{date.today().isoformat()}_deep_status.json")


def _is_status_stale(fp: str, status: dict | None) -> bool:
    if not status or status.get("status") != "running":
        return False
    try:
        last_update = os.path.getmtime(fp)
    except OSError:
        return False
    return (time.time() - last_update) >= _STALE_STATUS_SECONDS


def _mark_status_stale(fp: str, status: dict | None) -> dict:
    stale_status = dict(status or {})
    stale_status["status"] = "error"
    stale_status["error"] = stale_status.get("error") or "Top10 task heartbeat timed out; previous run was interrupted"
    stale_status["phase"] = stale_status.get("phase") or "interrupted"
    stale_status["finished"] = datetime.now().isoformat()
    try:
        with open(fp, "w", encoding="utf-8") as handle:
            json.dump(stale_status, handle, ensure_ascii=False, default=str)
    except Exception:
        pass
    return stale_status


def _write_status(status: dict):
    try:
        with open(_status_file(), "w", encoding="utf-8") as handle:
            json.dump(status, handle, ensure_ascii=False, default=str)
    except Exception:
        pass


def get_deep_status() -> dict | None:
    fp = _status_file()
    if not os.path.exists(fp):
        return None
    try:
        with open(fp, "r", encoding="utf-8") as handle:
            status = json.load(handle)
    except Exception:
        return None
    if _is_status_stale(fp, status):
        logger.warning("[deep_top10] stale running status detected: %s", fp)
        return _mark_status_stale(fp, status)
    return status


def is_deep_running() -> bool:
    global _is_running
    with _running_lock:
        if _is_running:
            return True
    status = get_deep_status()
    return bool(status and status.get("status") == "running")


def _fallback_last_trade_day(top_n: int = 100) -> tuple[pd.DataFrame, str]:
    """非交易日兜底：获取上一个交易日的全市场行情作为候选池。

    返回 (candidates_df, trade_date_str)。
    """
    from Stock_top10.top10.stock_filter import apply_filters

    # 方案1：Tushare daily_basic 获取上一个交易日全市场数据
    try:
        from data.tushare_client import get_pro
        pro = get_pro()
        if pro is not None:
            from datetime import datetime, timedelta
            # 往前找最近5天内的交易日
            for days_back in range(1, 6):
                trade_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y%m%d")
                df = pro.daily_basic(
                    trade_date=trade_date,
                    fields="ts_code,close,pct_chg,turnover_rate,volume_ratio,pe_ttm,total_mv,amount"
                )
                if df is not None and len(df) > 50:
                    # 按成交额排序取 Top N
                    df = df.sort_values("amount", ascending=False).head(top_n * 2).reset_index(drop=True)
                    result = pd.DataFrame({
                        "代码": df["ts_code"].str.replace(r"\.(SH|SZ|BJ)", "", regex=True),
                        "股票名称": "",  # Tushare daily_basic 没有名称，后面 enrich 会补
                        "最新价": df["close"],
                        "涨跌幅": df["pct_chg"],
                        "成交额(亿)": (df["amount"] / 1000).round(2),  # amount 单位千元
                        "换手率": df["turnover_rate"],
                        "量比": df["volume_ratio"],
                        "市盈率": df["pe_ttm"],
                        "总市值(亿)": (df["total_mv"] / 10000).round(1),  # total_mv 单位万元
                        "成交额排名": range(1, len(df) + 1),
                    })
                    # 补股票名称（Tushare → load_stock_list 兜底）
                    try:
                        stock_list = None
                        try:
                            stock_list = pro.stock_basic(fields="ts_code,name")
                        except Exception:
                            pass
                        if stock_list is None or stock_list.empty:
                            from data.tushare_client import load_stock_list
                            stock_list, _ = load_stock_list()
                        if stock_list is not None and not stock_list.empty:
                            name_map = dict(zip(
                                stock_list["ts_code"].str.replace(r"\.(SH|SZ|BJ)", "", regex=True),
                                stock_list["name"]
                            ))
                            result["股票名称"] = result["代码"].map(name_map).fillna("")
                    except Exception:
                        pass

                    result = apply_filters(result)
                    readable_date = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"
                    logger.info("[fallback] 使用 %s 交易日数据，%d 只候选", readable_date, len(result))
                    return result.head(top_n), readable_date

    except Exception as exc:
        logger.warning("[fallback] Tushare 回退失败: %r", exc)

    # 方案2：东方财富成交额榜（通常即使非交易日也有最近的数据缓存）
    try:
        from Stock_top10.top10.hot_rank import _get_volume_rank_eastmoney
        vol_df, err = _get_volume_rank_eastmoney(top_n)
        if not vol_df.empty:
            vol_df = apply_filters(vol_df)
            return vol_df, "最近交易日(东财)"
    except Exception:
        pass

    return pd.DataFrame(), ""


def run_deep_top10(
    model_name: str = _DEFAULT_MODEL,
    candidate_count: int = 100,
    username: str = "auto_scheduler",
    progress_callback=None,
    war_room_preset: str = "gemini",
):
    global _is_running

    # 国内数据源（akshare/东财）不走代理，但不清除全局代理以免影响 Claude API 等外网调用
    import os
    os.environ["NO_PROXY"] = "localhost,127.0.0.1,ark.cn-beijing.volces.com,dashscope.aliyuncs.com,open.bigmodel.cn,api.deepseek.com"

    from ai.client import call_ai, get_ai_client, get_token_usage
    from Stock_top10.top10.hot_rank import get_hot_rank, get_volume_rank, get_xueqiu_hot, merge_candidates
    from Stock_top10.top10.prompts import SYSTEM_SUMMARY, build_summary_prompt
    from Stock_top10.top10.runner import _send_top10_email, save_cached_result
    from Stock_top10.top10.scorer import score_all, score_all_war_room
    from Stock_top10.top10.stock_filter import apply_filters
    from Stock_top10.top10.tushare_data import enrich_candidates, get_sector_rotation, ts_ok

    with _running_lock:
        if _is_running:
            logger.warning("[deep_top10] 已有任务在运行（进程内），跳过")
            return
        current = get_deep_status()
        if current and current.get("status") == "running":
            logger.warning("[deep_top10] 已有任务在运行（跨进程），跳过")
            return
        _is_running = True

    def _log(message: str):
        logger.info("[deep_top10] %s", message)
        if progress_callback:
            progress_callback(message)
        status["progress"].append(message)
        _write_status(status)

    status = {
        "status": "running",
        "started": datetime.now().isoformat(),
        "model": model_name,
        "username": username,
        "phase": "",
        "progress": [],
        "error": None,
        "scored_count": 0,
        "total_count": 0,
        "current_stock": "",
    }
    _write_status(status)

    tokens_before = get_token_usage()["total"]
    scored = None  # track partial results

    try:
        status["phase"] = "获取候选池"
        _write_status(status)
        _log("📡 Phase 1: 获取候选池...")

        hot_df, _ = get_hot_rank(50)
        xq_df, _ = get_xueqiu_hot(50)
        vol_df, _ = get_volume_rank(50)
        merged = merge_candidates(hot_df, vol_df, xq_df)
        filtered = apply_filters(merged)
        candidates = filtered.head(candidate_count)
        _log(f"  候选池: 东财{len(hot_df)} + 雪球{len(xq_df)} + 成交额{len(vol_df)} -> {len(candidates)} 只")

        # 非交易日兜底：三路数据都为空时，回退到上一个交易日的全市场行情
        if candidates.empty:
            _log("  ⚠️ 候选池为空（可能非交易日），尝试回退到上一个交易日...")
            candidates, trade_date = _fallback_last_trade_day(candidate_count)
            if candidates.empty:
                raise RuntimeError("候选池为空（非交易日兜底也失败）")
            _log(f"  ✅ 使用 {trade_date} 交易日数据，获取 {len(candidates)} 只候选")
            status["data_date"] = trade_date

        status["phase"] = "数据增强"
        _write_status(status)
        _log("📊 Phase 2: Tushare 数据增强...")
        if ts_ok():
            enriched = enrich_candidates(candidates, progress_callback=lambda msg: _log(f"  {msg}"))
            _log("  ✅ 数据增强完成")
        else:
            enriched = candidates
            _log("  ⚠️ Tushare 不可用，使用基础数据")

        status["phase"] = "生成研报"
        _write_status(status)

        client, cfg, err = get_ai_client(model_name)
        if err:
            raise RuntimeError(f"AI 客户端初始化失败: {err}")

        # ── 断点续跑：加载已完成的增量结果 ────────────────────────────
        from Stock_top10.top10.scorer import load_incremental_results, clear_incremental_results

        incremental_key = f"war_room_{war_room_preset}"
        previous_results = load_incremental_results(incremental_key)
        already_scored_codes = {str(r["代码"]) for r in previous_results}
        resumed_count = len(already_scored_codes)

        if already_scored_codes:
            original_count = len(enriched)
            enriched = enriched[~enriched["代码"].astype(str).isin(already_scored_codes)]
            _log(f"  ♻️ 发现 {resumed_count} 只已完成的研报（断点续跑），跳过，剩余 {len(enriched)} 只待分析")

        overall_total = len(enriched) + resumed_count
        status["total_count"] = overall_total
        status["scored_count"] = resumed_count
        _write_status(status)
        # ── Phase 2.5: 侦察兵快速评估 → Top 20 进深度 ────────────────
        # ★ Scout 用 Gemini CLI（免费+快+无并发限制），Claude 留给深度分析
        from Stock_top10.top10.scorer import scout_all

        _log(f"🔍 Phase 2.5: 侦察兵快速评估 {len(enriched)} 只候选股...")
        status["phase"] = "scouting"
        _write_status(status)

        scout_client, scout_cfg = client, cfg
        # 优先 Gemini CLI（免费、快、无并发限制），回退到主模型
        _scout_model_order = ["🔮 Gemini CLI（免费）", "🟡 豆包 · Seed 2.0 Lite"]
        for _sm in _scout_model_order:
            try:
                from ai.client import get_ai_client
                _sc, _scfg, _serr = get_ai_client(_sm)
                if _scfg:
                    scout_client, scout_cfg = _sc, _scfg
                    _log(f"  侦察兵模型: {_sm}")
                    break
            except Exception:
                continue
        else:
            _log(f"  侦察兵回退到主模型: {model_name}")

        def scout_progress(current, total, msg):
            status["current_stock"] = msg.split("→")[0].replace("🔍", "").replace("❌", "").strip() if "→" in msg else ""
            _log(f"  [侦察 {current}/{total}] {msg}")

        # CLI 模型用 max_workers=1 避免子进程并发冲突
        _scout_workers = 1 if scout_cfg.get("provider", "").endswith("_cli") else 3
        scouted = scout_all(
            scout_client, scout_cfg, enriched,
            progress_callback=scout_progress,
            max_workers=_scout_workers,
            username=username,
        )

        # 取 Top 20 进入深度分析
        deep_count = 20
        top_for_deep = scouted.head(deep_count)
        _log(f"✅ 侦察完成: {len(scouted)} 只 → Top {deep_count} 进入深度分析")
        _log(f"   侦察前3: {', '.join(top_for_deep['股票名称'].head(3).tolist())}")

        # 更新计数（深度只做 top_for_deep 这些）
        enriched = top_for_deep
        overall_total = resumed_count + len(enriched)
        status["total_count"] = overall_total
        status["scored_count"] = resumed_count
        _write_status(status)

        # ── Phase 3: 四野指挥部深度分析（仅 Top 20）─────────────────
        _log(f"⚔️ Phase 3: 四野指挥部逐只分析 Top {len(enriched)}（阵容: {war_room_preset}）...")
        status["phase"] = "war_room"

        def score_progress(current, total, msg):
            status["scored_count"] = resumed_count + current
            status["total_count"] = overall_total
            if "→" in msg:
                status["current_stock"] = msg.split("→")[0].replace("✅", "").replace("❌", "").strip()
            _log(f"  [{resumed_count + current}/{overall_total}] {msg}")

        scored = score_all_war_room(
            enriched,
            preset=war_room_preset,
            progress_callback=score_progress,
            max_workers=1,  # 指挥部内部已有三将领并行
            username=username,
        )

        # ── 合并断点续跑的结果 ────────────────────────────────────────
        if previous_results:
            previous_df = pd.DataFrame(previous_results)
            if not scored.empty:
                scored = pd.concat([previous_df, scored], ignore_index=True)
            else:
                scored = previous_df
            scored = scored.sort_values("综合匹配度", ascending=False).reset_index(drop=True)
            scored.index = scored.index + 1
            scored.index.name = "推荐排名"

        _log(f"  ✅ 研报生成完成，共 {len(scored)} 只")

        status["phase"] = "总结保存"
        _write_status(status)
        _log("📋 Phase 4: 生成 Top10 总结并保存结果...")

        top10 = scored.head(10)
        stock_lines = []
        for _, row in top10.iterrows():
            line = (
                f"- {row['股票名称']}({row['代码']}) "
                f"行业:{row.get('行业', '未知')} "
                f"综合匹配度{row.get('综合匹配度', 0):.1f}分 "
                f"操作评级:{row.get('操作评级', '未知')} "
                f"链接:{row.get('报告链接', '')}"
            )
            if row.get("核心摘要"):
                line += f" 摘要:{row['核心摘要']}"
            stock_lines.append(line)
        stocks_text = "\n".join(stock_lines)

        try:
            sectors = get_sector_rotation()
            if sectors.get("概念板块"):
                stocks_text += "\n\n今日概念板块涨幅Top5：" + "、".join(sectors["概念板块"])
            if sectors.get("行业板块"):
                stocks_text += "\n今日行业板块涨幅Top5：" + "、".join(sectors["行业板块"])
        except Exception:
            pass

        summary_prompt = build_summary_prompt(stocks_text, len(candidates))
        summary, summary_err = call_ai(
            client,
            cfg,
            summary_prompt,
            system=SYSTEM_SUMMARY,
            max_tokens=4000,
            username=username,
        )
        if summary_err:
            summary = f"总结生成失败：{summary_err}"

        tokens_after = get_token_usage()["total"]
        tokens_used = tokens_after - tokens_before

        save_cached_result(
            model_name,
            scored,
            summary,
            triggered_by=username,
            tokens_used=tokens_used,
        )
        clear_incremental_results(incremental_key)

        _log(f"✅ 全部完成！共消耗 {tokens_used:,} token")
        _log("📧 发送 Top10 报告邮件...")
        _send_top10_email(summary, scored, model_name, username, tokens_used)

        status["status"] = "done"
        status["phase"] = "完成"
        status["finished"] = datetime.now().isoformat()
        status["tokens_used"] = tokens_used
        status["scored_count"] = len(scored)
        status["top10_links"] = top10["报告链接"].tolist() if "报告链接" in top10.columns else []
        _write_status(status)

    except Exception as exc:
        logger.error("[deep_top10] 异常: %s", exc, exc_info=True)

        # Save partial results if any stocks were scored
        tokens_after = get_token_usage()["total"]
        tokens_used = tokens_after - tokens_before
        if scored is not None and not scored.empty:
            _log(f"⚠️ 中途失败，但已完成 {len(scored)} 只研报，保存部分结果...")
            try:
                save_cached_result(
                    model_name,
                    scored,
                    f"（部分结果）生成过程中出错：{exc}",
                    triggered_by=username,
                    tokens_used=tokens_used,
                )
                status["status"] = "done"
                status["phase"] = "部分完成"
                status["error"] = str(exc)
                status["scored_count"] = len(scored)
            except Exception as save_exc:
                logger.error("[deep_top10] 保存部分结果失败: %s", save_exc)
                status["status"] = "error"
                status["error"] = str(exc)
        else:
            status["status"] = "error"
            status["error"] = str(exc)

        status["finished"] = datetime.now().isoformat()
        status["tokens_used"] = tokens_used
        _write_status(status)
    finally:
        with _running_lock:
            _is_running = False


def start_deep_top10_async(
    model_name: str = _DEFAULT_MODEL,
    candidate_count: int = 100,
    username: str = "auto_scheduler",
    war_room_preset: str = "gemini",
):
    if is_deep_running():
        return False
    thread = threading.Thread(
        target=run_deep_top10,
        kwargs={
            "model_name": model_name,
            "candidate_count": candidate_count,
            "username": username,
            "war_room_preset": war_room_preset,
        },
        daemon=True,
    )
    thread.start()
    return True
