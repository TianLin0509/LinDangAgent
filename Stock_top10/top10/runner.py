"""后台分析调度 — 数据增强 + 并行评分"""

import json
import logging
import os
import threading
from datetime import date
import pandas as pd
from ai.client import get_ai_client, call_ai, get_token_usage
from Stock_top10.top10.prompts import SYSTEM_SUMMARY, build_summary_prompt


def _get_ss():
    """安全获取 Streamlit session_state，Actions 环境下返回空 dict"""
    try:
        import streamlit as st
        return st.session_state
    except Exception:
        return {}
from Stock_top10.top10.scorer import score_all

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# 持久化缓存（session_state + JSON 文件双重保险）
# ══════════════════════════════════════════════════════════════════════════════

_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cache")
os.makedirs(_CACHE_DIR, exist_ok=True)


def _cache_key(model_name: str) -> str:
    return f"top10_result_{date.today().isoformat()}_{model_name}"


def _summary_key(model_name: str) -> str:
    return f"top10_summary_{date.today().isoformat()}_{model_name}"


def _file_path(model_name: str) -> str:
    return os.path.join(_CACHE_DIR, f"{date.today().isoformat()}_{model_name}.json")


def _lock_path(model_name: str) -> str:
    return os.path.join(_CACHE_DIR, f"{date.today().isoformat()}_{model_name}.lock")


def _acquire_lock(model_name: str, username: str) -> bool:
    lp = _lock_path(model_name)
    try:
        if os.path.exists(lp):
            import time as _time
            age = _time.time() - os.path.getmtime(lp)
            if age < 900:
                return False
            logger.warning("[top10] 清理僵尸锁（%.0f秒前创建）", age)
        with open(lp, "w", encoding="utf-8") as f:
            json.dump({"user": username, "ts": date.today().isoformat()}, f)
        return True
    except Exception:
        return True


def _release_lock(model_name: str):
    lp = _lock_path(model_name)
    try:
        if os.path.exists(lp):
            os.remove(lp)
    except Exception:
        pass


def is_locked(model_name: str) -> dict | None:
    lp = _lock_path(model_name)
    if not os.path.exists(lp):
        return None
    try:
        import time as _time
        if _time.time() - os.path.getmtime(lp) >= 900:
            return None
        with open(lp, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _meta_key(model_name: str) -> str:
    return f"top10_meta_{date.today().isoformat()}_{model_name}"


def _load_from_data(data: dict, model_name: str) -> pd.DataFrame | None:
    """从 JSON data dict 解析结果并写入 session_state，返回 DataFrame"""
    try:
        df = pd.DataFrame(data["results"])
        df.index = df.index + 1
        df.index.name = "推荐排名"
        _get_ss()[_cache_key(model_name)] = df
        if "summary" in data:
            _get_ss()[_summary_key(model_name)] = data["summary"]
        if "triggered_by" in data:
            _get_ss()[_meta_key(model_name)] = {
                "user": data["triggered_by"],
                "tokens": data.get("tokens_used", 0),
            }
        return df
    except Exception:
        return None


def get_cached_result(model_name: str) -> pd.DataFrame | None:
    """优先 session_state → 本地 JSON → GitHub 远程拉取"""
    key = _cache_key(model_name)
    if key in _get_ss():
        return _get_ss()[key]

    # 本地文件
    fp = _file_path(model_name)
    if os.path.exists(fp):
        try:
            with open(fp, "r", encoding="utf-8") as f:
                data = json.load(f)
            return _load_from_data(data, model_name)
        except Exception:
            pass

    # GitHub 远程拉取
    try:
        from Stock_top10.core.github_store import pull_json, is_enabled
        if is_enabled():
            filename = f"{date.today().isoformat()}_{model_name}.json"
            data = pull_json(filename)
            if data:
                # 写入本地缓存（下次直接读本地）
                try:
                    with open(fp, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
                except Exception:
                    pass
                return _load_from_data(data, model_name)
    except Exception as e:
        logger.debug("[get_cached_result] GitHub 拉取失败: %s", e)

    return None


def get_cached_meta(model_name: str) -> dict | None:
    mkey = _meta_key(model_name)
    if mkey in _get_ss():
        return _get_ss()[mkey]
    get_cached_result(model_name)
    return _get_ss().get(mkey)


def get_cached_summary(model_name: str) -> str | None:
    skey = _summary_key(model_name)
    if skey in _get_ss():
        return _get_ss()[skey]
    get_cached_result(model_name)
    return _get_ss().get(skey)


def save_cached_result(model_name: str, df: pd.DataFrame, summary: str = "",
                       triggered_by: str = "", tokens_used: int = 0):
    # 1. session_state
    try:
        _get_ss()[_cache_key(model_name)] = df
        if summary:
            _get_ss()[_summary_key(model_name)] = summary
        if triggered_by:
            _get_ss()[_meta_key(model_name)] = {
                "user": triggered_by, "tokens": tokens_used,
            }
    except Exception:
        pass

    # 2. 本地 JSON
    save_cols = [c for c in df.columns if c != "K线摘要"]
    data = {
        "results": df[save_cols].to_dict(orient="records"),
        "summary": summary,
        "model": model_name,
        "date": date.today().isoformat(),
        "triggered_by": triggered_by,
        "tokens_used": tokens_used,
    }
    try:
        with open(_file_path(model_name), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    except Exception:
        pass

    # 3. 推送到 GitHub（后台线程，不阻塞）
    try:
        from Stock_top10.core.github_store import push_json, is_enabled
        if is_enabled():
            filename = f"{date.today().isoformat()}_{model_name}.json"
            import threading
            threading.Thread(
                target=push_json, args=(filename, data), daemon=True
            ).start()
            logger.info("[save_cached_result] 后台推送 %s 到 GitHub", filename)
    except Exception as e:
        logger.debug("[save_cached_result] GitHub 推送失败: %s", e)


def get_all_cached_models() -> list[str]:
    """获取今日所有已缓存的模型名称（本地 + GitHub 合并）"""
    import time as _time
    _ss_key = "_top10_all_models_cache"
    _ts_key = "_top10_all_models_ts"
    try:
        cached = _get_ss().get(_ss_key)
        cached_ts = _get_ss().get(_ts_key, 0)
        if cached is not None and (_time.time() - cached_ts) < 60:
            return cached
    except Exception:
        pass

    today_str = date.today().isoformat()
    models = set()

    # 本地文件
    for fn in os.listdir(_CACHE_DIR):
        if fn.startswith(today_str) and fn.endswith(".json"):
            model = fn[len(today_str) + 1:-5]
            models.add(model)

    # GitHub 远程目录
    try:
        from Stock_top10.core.github_store import list_today_files, is_enabled
        if is_enabled():
            for fn in list_today_files(today_str):
                model = fn[len(today_str) + 1:-5]
                models.add(model)
    except Exception:
        pass

    result = list(models)
    try:
        _get_ss()[_ss_key] = result
        _get_ss()[_ts_key] = _time.time()
    except Exception:
        pass
    return result


# ══════════════════════════════════════════════════════════════════════════════
# 后台任务管理
# ══════════════════════════════════════════════════════════════════════════════

def get_job(ss) -> dict:
    return ss.get("top10_bg_job", {})


def is_running(ss) -> bool:
    return get_job(ss).get("status") == "running"


def is_done(ss) -> bool:
    return get_job(ss).get("status") == "done"


def _send_top10_email(summary: str, scored_df: pd.DataFrame,
                      model_name: str, triggered_by: str, tokens_used: int):
    try:
        from utils.email_sender import smtp_configured, _get_smtp_config, _md_to_html_simple
        if not smtp_configured():
            logger.debug("[top10] SMTP 未配置，跳过邮件发送")
            return

        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        RECIPIENT = "290045045@qq.com"
        today_str = date.today().strftime("%Y-%m-%d")
        subject = f"🏆 今日 Top10 推荐 — {today_str} | by {triggered_by}"

        top10 = scored_df.head(10)
        rows_html = ""
        for i, (_, r) in enumerate(top10.iterrows(), 1):
            change = r.get("涨跌幅", 0)
            change_color = "#ef4444" if change < 0 else "#22c55e"
            advice = r.get("短线建议", "—")
            advice_colors = {
                "强烈推荐": "#dc2626", "推荐": "#f59e0b",
                "观望": "#6b7280", "回避": "#9ca3af",
            }
            advice_color = advice_colors.get(advice, "#6b7280")
            rows_html += f"""
            <tr style="border-bottom:1px solid #e5e7eb;">
                <td style="padding:8px;text-align:center;font-weight:700;color:#6366f1;">{i}</td>
                <td style="padding:8px;">{r['股票名称']}<br><span style="color:#9ca3af;font-size:11px;">{r['代码']}</span></td>
                <td style="padding:8px;text-align:center;">{r.get('最新价','—')}</td>
                <td style="padding:8px;text-align:center;color:{change_color};">{change:+.2f}%</td>
                <td style="padding:8px;text-align:center;font-weight:700;color:#6366f1;">{r.get('综合匹配度', 0):.1f}分</td>
                <td style="padding:8px;text-align:center;">{r.get('行业','—')}</td>
                <td style="padding:8px;text-align:center;"><span style="background:{advice_color};color:#fff;padding:2px 8px;border-radius:10px;font-size:11px;">{advice}</span></td>
            </tr>"""

        summary_html = _md_to_html_simple(summary) if summary else ""

        if tokens_used >= 10000:
            tokens_display = f"{tokens_used / 10000:.1f}万"
        else:
            tokens_display = f"{tokens_used:,}"

        html_body = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:'Microsoft YaHei','Helvetica Neue',sans-serif;max-width:720px;
             margin:0 auto;padding:20px;background:#f6f8ff;">

<div style="background:linear-gradient(135deg,#6366f1,#a855f7);color:#fff;
    border-radius:16px;padding:20px;text-align:center;">
    <h1 style="margin:0;font-size:22px;">🏆 今日 Top10 推荐</h1>
    <p style="margin:4px 0 0;opacity:0.9;">{today_str} | 模型：{model_name}</p>
    <p style="margin:4px 0 0;opacity:0.8;font-size:13px;">分析来自 {triggered_by} 用户，共消耗 {tokens_display} token</p>
</div>

<div style="background:#fff;border-radius:12px;padding:16px;margin:12px 0;">
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
        <thead>
            <tr style="background:#f8f9ff;border-bottom:2px solid #6366f1;">
                <th style="padding:8px;">排名</th>
                <th style="padding:8px;">股票</th>
                <th style="padding:8px;">最新价</th>
                <th style="padding:8px;">涨跌幅</th>
                <th style="padding:8px;">评分</th>
                <th style="padding:8px;">行业</th>
                <th style="padding:8px;">建议</th>
            </tr>
        </thead>
        <tbody>{rows_html}</tbody>
    </table>
</div>

<div style="background:#fff;border-radius:12px;padding:16px;margin:12px 0;
            border-left:4px solid #6366f1;">
    <h3 style="margin:0 0 8px;color:#1e1b4b;">📝 每日总结</h3>
    <div style="font-size:13px;color:#374151;line-height:1.7;">{summary_html}</div>
</div>

<div style="text-align:center;color:#9ca3af;font-size:11px;margin-top:20px;">
    ⚠️ 本报告仅供学习研究，不构成投资建议。<br>
    Generated by 呆瓜方后援会专属投研助手 · 立花道雪
</div>
</body></html>"""

        from utils.email_sender import send_html_email
        send_html_email(subject, html_body)
        logger.info("[top10] Top10 报告邮件已发送")
    except Exception as e:
        logger.warning("[top10] 邮件发送失败: %s", e)


def start_scoring(ss, candidates_df: pd.DataFrame, model_name: str,
                  username: str = ""):
    if is_running(ss):
        return

    if not _acquire_lock(model_name, username):
        lock_info = is_locked(model_name)
        who = lock_info.get("user", "其他用户") if lock_info else "其他用户"
        ss["top10_bg_job"] = {
            "status": "done", "error": None,
            "progress": [f"⏳ {who} 正在分析中，请稍等片刻后刷新页面查看结果"],
            "result": None, "summary": None,
        }
        return

    client, cfg, err = get_ai_client(model_name)
    if err:
        _release_lock(model_name)
        ss["top10_bg_job"] = {"status": "done", "error": err, "progress": [f"❌ {err}"]}
        return

    tokens_before = get_token_usage()["total"]
    min_expected = max(5, len(candidates_df) // 3)

    job = {
        "status": "running",
        "progress": [],
        "error": None,
        "result": None,
        "summary": None,
        "model": model_name,
        "total": len(candidates_df),
        "current": 0,
        "triggered_by": username,
    }
    ss["top10_bg_job"] = job

    def _run():
        try:
            job["progress"].append("📊 正在从 Tushare 获取增强数据（PE/PB/K线）...")
            try:
                from Stock_top10.top10.tushare_data import enrich_candidates, ts_ok
                if ts_ok():
                    enriched_df = enrich_candidates(
                        candidates_df,
                        progress_callback=lambda msg: job["progress"].append(f"  {msg}")
                    )
                    job["progress"].append(f"✅ 数据增强完成（行业/PE/PB/K线摘要）")
                else:
                    enriched_df = candidates_df
                    job["progress"].append("⚠️ Tushare 不可用，使用基础数据分析")
            except Exception as e:
                enriched_df = candidates_df
                job["progress"].append(f"⚠️ 数据增强失败({e})，使用基础数据")

            total = len(enriched_df)
            job["progress"].append(f"🤖 开始并行为候选股生成价值投机研报，共 {total} 只（3路并发）...")

            def progress_cb(current, total, msg):
                job["current"] = current
                job["progress"].append(f"[{current}/{total}] {msg}")

            scored = score_all(client, cfg, enriched_df,
                               model_name=model_name,
                               progress_callback=progress_cb,
                               max_workers=3)

            job["progress"].append(f"✅ 研报生成完成！共完成 {len(scored)} 只股票")

            if len(scored) < min_expected:
                job["error"] = f"评分结果不完整（仅 {len(scored)}/{len(candidates_df)} 只），不缓存，请重试"
                job["progress"].append(f"⚠️ 评分结果不完整（{len(scored)}/{len(candidates_df)}），本次不缓存")
                job["result"] = scored
                job["status"] = "done"
                _release_lock(model_name)
                return

            job["result"] = scored

            job["progress"].append("📝 正在生成每日总结报告...")
            summary = ""
            try:
                top10 = scored.head(10)
                stock_lines = []
                for _, r in top10.iterrows():
                    line = (f"- {r['股票名称']}({r['代码']}) "
                            f"行业:{r.get('行业','未知')} "
                            f"综合匹配度{r.get('综合匹配度', 0):.1f}分 "
                            f"操作评级:{r.get('操作评级','未知')} "
                            f"链接:{r.get('报告链接','')}")
                    stock_lines.append(line)
                stocks_text = "\n".join(stock_lines)

                try:
                    from Stock_top10.top10.tushare_data import get_sector_rotation
                    sectors = get_sector_rotation()
                    if sectors.get("概念板块"):
                        stocks_text += "\n\n今日概念板块涨幅Top5：" + "、".join(sectors["概念板块"])
                    if sectors.get("行业板块"):
                        stocks_text += "\n今日行业板块涨幅Top5：" + "、".join(sectors["行业板块"])
                except Exception:
                    pass

                summary_prompt = build_summary_prompt(stocks_text, total)
                summary, s_err = call_ai(
                    client, cfg, summary_prompt,
                    system=SYSTEM_SUMMARY, max_tokens=4000
                )
                if s_err:
                    summary = f"总结生成失败：{s_err}"
            except Exception as se:
                summary = f"总结生成失败：{se}"

            tokens_after = get_token_usage()["total"]
            tokens_used = tokens_after - tokens_before

            job["summary"] = summary
            job["tokens_used"] = tokens_used
            job["progress"].append(f"✅ 全部完成！（消耗 {tokens_used:,} token）")
            job["status"] = "done"

            save_cached_result(model_name, scored, summary,
                               triggered_by=username, tokens_used=tokens_used)

            _release_lock(model_name)

            job["progress"].append("📧 正在发送 Top10 报告邮件...")
            _send_top10_email(summary, scored, model_name, username, tokens_used)

        except Exception as e:
            _release_lock(model_name)
            job["error"] = str(e)
            job["progress"].append(f"❌ 分析出错：{e}")
            job["status"] = "done"

    t = threading.Thread(target=_run, daemon=True)
    t.start()
