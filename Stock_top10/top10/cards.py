"""Top 10 精简卡片 — 三分类 Tab 展示"""

import math
import pandas as pd
import streamlit as st


def _render_cards(df: pd.DataFrame, max_cards: int = 10, key_prefix: str = "all"):
    if df.empty:
        st.info("暂无符合条件的推荐结果")
        return

    top = df.head(max_cards)

    for i, (_, row) in enumerate(top.iterrows(), 1):
        score = row.get("综合评分", 0)
        match_score = row.get("综合匹配度", None)
        name = row.get("股票名称", "")
        code = row.get("代码", "")
        price = row.get("最新价", 0)
        change = row.get("涨跌幅", 0)
        advice = row.get("短线建议", "")
        mid_advice = row.get("中期建议", "")
        industry = row.get("行业", "")
        s_fund = row.get("基本面")
        s_theme = row.get("题材热度")
        s_tech = row.get("技术面")

        if score >= 8:
            border_color = "#22c55e"
            badge_bg = "#f0fdf4"
            badge_color = "#16a34a"
        elif score >= 6:
            border_color = "#6366f1"
            badge_bg = "#eef2ff"
            badge_color = "#6366f1"
        else:
            border_color = "#f97316"
            badge_bg = "#fff7ed"
            badge_color = "#c2410c"

        _change_safe = change if (isinstance(change, float) and not math.isnan(change)) else 0.0
        change_color = "#22c55e" if _change_safe >= 0 else "#ef4444"
        change_sign = "+" if _change_safe >= 0 else ""
        price_str = f"{price:.2f}" if isinstance(price, (int, float)) and not (isinstance(price, float) and math.isnan(price)) else str(price)
        change_str = f"{change_sign}{_change_safe:.2f}"

        advice_map = {
            "强烈推荐": ("#dc2626", "#fef2f2", "🔥 强烈推荐"),
            "推荐": ("#16a34a", "#f0fdf4", "👍 推荐"),
            "观望": ("#d97706", "#fffbeb", "👀 观望"),
            "回避": ("#6b7280", "#f3f4f6", "⛔ 回避"),
        }
        adv_color, adv_bg, adv_text = advice_map.get(advice, ("#6b7280", "#f3f4f6", ""))
        advice_html = f"""<span style="
            background:{adv_bg}; color:{adv_color};
            border-radius:50px; padding:2px 10px;
            font-weight:700; font-size:0.78rem; margin-left:10px;
        ">{adv_text}</span>""" if adv_text else ""

        mid_map = {
            "强烈推荐": ("#7c3aed", "#f5f3ff", "📈 中期强推"),
            "推荐": ("#2563eb", "#eff6ff", "📊 中期推荐"),
            "观望": ("#d97706", "#fffbeb", "⏳ 中期观望"),
            "回避": ("#6b7280", "#f3f4f6", "📉 中期回避"),
        }
        mid_color, mid_bg, mid_text = mid_map.get(mid_advice, ("#6b7280", "#f3f4f6", ""))
        mid_html = f"""<span style="
            background:{mid_bg}; color:{mid_color};
            border-radius:50px; padding:2px 10px;
            font-weight:700; font-size:0.78rem; margin-left:4px;
        ">{mid_text}</span>""" if mid_text else ""

        industry_html = f"""<span style="
            background:#f0f9ff; color:#0369a1;
            border-radius:50px; padding:2px 8px;
            font-size:0.72rem; margin-left:6px;
        ">{industry}</span>""" if industry else ""

        def _bar(label, val, color):
            if val is None or (isinstance(val, float) and math.isnan(val)):
                return ""
            pct = min(val * 10, 100)
            return (
                f'<div style="display:flex;align-items:center;gap:4px;margin-top:2px;">'
                f'<span style="font-size:0.7rem;color:#6b7280;min-width:40px;">{label}</span>'
                f'<div style="flex:1;background:#f1f5f9;border-radius:4px;height:7px;overflow:hidden;">'
                f'<div style="width:{pct}%;background:{color};height:100%;border-radius:4px;"></div></div>'
                f'<span style="font-size:0.7rem;color:#374151;min-width:20px;text-align:right;">{val:.0f}</span>'
                f'</div>'
            )

        sub_bars = _bar("基本面", s_fund, "#3b82f6") + _bar("题材", s_theme, "#f59e0b") + _bar("技术面", s_tech, "#22c55e")

        quant_total = row.get("量化总分")
        quant_signal = row.get("量化信号", "")
        report_url = row.get("报告链接", "")
        quant_html = ""
        if quant_total and not (isinstance(quant_total, float) and math.isnan(quant_total)):
            q_color = "#16a34a" if quant_total >= 65 else "#f59e0b" if quant_total >= 50 else "#ef4444"
            quant_html = (
                f'<div style="margin-top:6px;display:flex;align-items:center;gap:8px;">'
                f'<span style="font-size:0.72rem;color:#6b7280;">量化预评分</span>'
                f'<span style="background:{q_color}15;color:{q_color};border-radius:4px;'
                f'padding:1px 8px;font-size:0.72rem;font-weight:700;">{int(quant_total)}/100 {quant_signal}</span>'
                f'</div>'
            )

        sub_section = f'<div style="margin-top:8px;">{sub_bars}{quant_html}</div>' if (sub_bars or quant_html) else ""

        score_label = f"{match_score:.1f}分" if isinstance(match_score, (int, float)) else f"{score}/10"
        link_html = (
            f'<div style="margin-top:8px;"><a href="{report_url}" target="_blank" '
            f'style="color:#2563eb;font-size:0.8rem;font-weight:700;text-decoration:none;">查看完整研报</a></div>'
            if report_url else ""
        )

        card_html = (
            f'<div style="background:#fff;border:2px solid {border_color};border-radius:12px;'
            f'padding:0.7rem 0.9rem;margin:0.3rem 0;box-shadow:0 2px 8px rgba(0,0,0,0.05);">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.3rem;">'
            f'<div style="min-width:0;overflow:hidden;">'
            f'<span style="font-size:1.05rem;font-weight:800;color:#1e1b4b;">#{i} {name}</span>'
            f'<span style="font-size:0.78rem;color:#6b7280;margin-left:6px;">{code}</span>'
            f'{industry_html}</div>'
            f'<div style="background:{badge_bg};color:{badge_color};border-radius:50px;padding:3px 10px;'
            f'font-weight:800;font-size:0.9rem;flex-shrink:0;">{score_label}</div></div>'
            f'<div style="display:flex;gap:8px;align-items:center;font-size:0.8rem;color:#6b7280;flex-wrap:wrap;">'
            f'<span>💰 {price_str}元</span>'
            f'<span style="color:{change_color};font-weight:600;">{change_str}%</span>'
            f'{advice_html}{mid_html}</div>'
            f'{sub_section}{link_html}</div>'
        )
        st.markdown(card_html, unsafe_allow_html=True)

        # 深度分析按钮
        if st.button(f"🔍 深度分析 {name}", key=f"top10_{key_prefix}_{code}_{i}", use_container_width=True):
            st.session_state["_top10_pick"] = code


def _filter_short_term(df: pd.DataFrame, n: int = 5) -> pd.DataFrame:
    if df.empty:
        return df
    tmp = df.copy()
    advice_weight = {"强烈推荐": 4, "推荐": 3, "观望": 1, "回避": 0}
    tmp["_short_w"] = tmp["短线建议"].map(advice_weight).fillna(1)
    tech_col = tmp["技术面"].fillna(0) if "技术面" in tmp.columns else 0
    tmp["_short_score"] = tmp["_short_w"] * 2.5 + tech_col
    good = tmp[tmp["短线建议"].isin(["强烈推荐", "推荐"])]
    if len(good) >= n:
        result = good.nlargest(n, "_short_score")
    else:
        result = tmp.nlargest(n, "_short_score")
    return result.drop(columns=["_short_w", "_short_score"]).reset_index(drop=True)


def _filter_mid_term(df: pd.DataFrame, n: int = 5) -> pd.DataFrame:
    if df.empty:
        return df
    tmp = df.copy()
    advice_weight = {"强烈推荐": 4, "推荐": 3, "观望": 1, "回避": 0}
    tmp["_mid_w"] = tmp["中期建议"].map(advice_weight).fillna(1) if "中期建议" in tmp.columns else 1
    fund_col = tmp["基本面"].fillna(0) if "基本面" in tmp.columns else 0
    tmp["_mid_score"] = tmp["_mid_w"] * 2.5 + fund_col
    good = tmp[tmp.get("中期建议", pd.Series(dtype=str)).isin(["强烈推荐", "推荐"])] if "中期建议" in tmp.columns else pd.DataFrame()
    if len(good) >= n:
        result = good.nlargest(n, "_mid_score")
    else:
        result = tmp.nlargest(n, "_mid_score")
    return result.drop(columns=["_mid_w", "_mid_score"]).reset_index(drop=True)


def show_top10_cards(df: pd.DataFrame):
    if df.empty:
        st.info("暂无推荐结果")
        return

    tab_all, tab_short, tab_mid = st.tabs([
        "📊 综合 Top10", "⚡ 短线精选 Top5", "🏗️ 中期布局 Top5"
    ])

    with tab_all:
        _render_cards(df, max_cards=10, key_prefix="all")

    with tab_short:
        short_df = _filter_short_term(df, 5)
        if short_df.empty or (short_df["短线建议"] == "观望").all():
            st.caption("今日暂无明确短线机会，以下为技术面评分最高的 5 只")
        else:
            st.caption("按技术面评分 + 短线建议综合筛选，适合短线交易者")
        _render_cards(short_df, max_cards=5, key_prefix="short")

    with tab_mid:
        mid_df = _filter_mid_term(df, 5)
        if mid_df.empty:
            st.caption("今日暂无明确中期布局标的")
        else:
            st.caption("按基本面评分 + 中期建议综合筛选，适合中线持仓布局")
        _render_cards(mid_df, max_cards=5, key_prefix="mid")


def show_progress(job: dict):
    if not job:
        return

    status = job.get("status", "")
    progress = job.get("progress", [])
    current = job.get("current", 0)
    total = job.get("total", 1)

    if status == "running":
        pct = current / total if total > 0 else 0
        st.progress(pct, text=f"分析进度：{current}/{total}")
        with st.status("🔍 AI 正在深度分析...", expanded=True, state="running"):
            for msg in progress[-8:]:
                st.write(msg)
    elif status == "done" and job.get("error"):
        st.error(f"分析失败：{job['error']}")
    elif status == "done":
        st.success(f"✅ 分析完成！共评分 {total} 只股票")
