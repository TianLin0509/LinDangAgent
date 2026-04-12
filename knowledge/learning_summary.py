# -*- coding: utf-8 -*-
"""统一学习引擎 — 摘要生成器。

生成易读的 markdown 报告，包含：
- Round 1 回测统计 + 典型案例
- Round 2 Opus 建议详情
- Round 3 审视结果
- Round 4-5 应用和验证
- 一句话结论
"""

from datetime import datetime


STATUS_LABEL = {
    "adopted": "✅ 新配置已采纳",
    "not_adopted": "⚠️ 未达标，已回退",
    "no_proposals": "💤 Opus 认为无需调整",
    "all_rejected": "❌ 所有建议被风控否决",
    "pending_prompt_approval": "⏳ 仅 prompt 变更，待人工审批",
    "failed_round1": "💥 Round 1 失败",
}


def _format_case_detail(r: dict, idx: int) -> list[str]:
    """生成单个案例的详情段落（markdown）。"""
    lines = []
    verdict = r.get("verdict", "?")
    icon = "✅" if verdict == "hit" else "❌"
    stock_name = r.get("stock_name", "?")
    ts_code = r.get("ts_code", "?")
    exam_date = r.get("exam_date", "?")
    weighted = r.get("weighted", "?")
    direction_cn = r.get("direction_cn", "?")
    actual = r.get("actual_return_10d", 0)
    alpha = r.get("excess_return", 0)
    market_ret = r.get("market_return_10d", 0)
    sector_name = r.get("sector_name", "")
    sector_ret = r.get("sector_return_10d", 0)
    source = r.get("source", "?")
    category = r.get("category", "?")

    # 标题行：核心判断 vs 实际
    lines.append(f"### {icon} 案例#{idx} {stock_name} ({ts_code}) @ {exam_date}")
    lines.append("")

    # 一目了然的对比表
    lines.append("| 项目 | 值 |")
    lines.append("|------|------|")
    lines.append(f"| 选题来源 | {source} ({category}) |")
    lines.append(f"| AI 综合评分 | **{weighted}** |")
    lines.append(f"| AI 方向判断 | **{direction_cn}** |")
    lines.append(f"| 实际 T+10 收益 | {actual:+.2f}% |")
    lines.append(f"| 大盘同期(上证) | {market_ret:+.2f}% |")
    lines.append(f"| 板块({sector_name})同期 | {sector_ret:+.2f}% |")
    lines.append(f"| **个股超额 α** | **{alpha:+.2f}%** |")
    lines.append(f"| 判定 | {icon} {verdict} |")
    lines.append("")

    # 四维评分
    scores = r.get("scores", {})
    if scores:
        score_parts = []
        for dim in ["基本面", "预期差", "资金面", "技术面"]:
            if dim in scores:
                score_parts.append(f"{dim} {scores[dim]}")
        if score_parts:
            lines.append(f"**四维评分**: {' | '.join(score_parts)} → 综合 {scores.get('综合加权', weighted)}")
            lines.append("")

    # Round 1 原始评分（如果和最终不同，说明 Round 2 修正了）
    r1_scores = r.get("round1_scores", {})
    if r1_scores and r1_scores != scores:
        r1_parts = []
        for dim in ["基本面", "预期差", "资金面", "技术面"]:
            if dim in r1_scores:
                r1_parts.append(f"{dim} {r1_scores[dim]}")
        if r1_parts:
            lines.append(f"**Round 1 原始评分**: {' | '.join(r1_parts)} → Round 2 做了修正")
            lines.append("")

    # AI 分析摘要
    summary = r.get("analysis_summary", "")
    if summary:
        lines.append("**AI 分析摘要**:")
        lines.append("")
        lines.append(f"> {summary[:500]}")
        lines.append("")

    # 完整分析（可折叠，HTML 里会自动展开）
    combined = r.get("combined_markdown", "")
    if combined:
        lines.append("<details>")
        lines.append(f"<summary>📄 查看完整分析（{len(combined)} 字符）</summary>")
        lines.append("")
        lines.append("```markdown")
        lines.append(combined[:6000])
        lines.append("```")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    lines.append("---")
    lines.append("")
    return lines


def build_summary_markdown(result: dict) -> str:
    """构建完整的学习报告 markdown。"""
    lines = []
    mode = result.get("mode", "?")
    count = result.get("count", 0)
    status = result.get("status", "?")

    lines.append(f"# 统一学习引擎报告")
    lines.append("")
    lines.append(f"**模式**: {mode} | **规模**: {count} 只 | **时间**: {result.get('started_at', '?')[:19]}")
    lines.append(f"**状态**: {STATUS_LABEL.get(status, status)}")
    lines.append(f"**结论**: {result.get('summary', '?')}")
    lines.append("")

    rounds = result.get("rounds", {})

    # ── Round 1: 回测统计 ─────────────────────────────────────
    r1 = rounds.get("round1", {})
    if r1:
        lines.append("## Round 1: 批量回测")
        lines.append("")
        lines.append(f"- 训练集: {r1.get('total', 0)} 只 | 命中: {r1.get('hits', 0)} 只 | **胜率: {r1.get('hit_rate', 0)}%**")

        by_dir = r1.get("by_direction", {})
        if by_dir:
            dir_parts = [f"{d} {v.get('hits', 0)}/{v.get('total', 0)}({v.get('hit_rate', 0)}%)"
                         for d, v in by_dir.items()]
            lines.append(f"- 分方向: {' | '.join(dir_parts)}")

        by_cat = r1.get("by_category", {})
        if by_cat:
            cat_parts = [f"{c} {v.get('hits', 0)}/{v.get('total', 0)}"
                         for c, v in by_cat.items()]
            lines.append(f"- 分类别: {' | '.join(cat_parts)}")

        by_sector = r1.get("by_sector", {})
        if by_sector:
            weak = sorted(
                [(s, v) for s, v in by_sector.items() if v.get("total", 0) >= 1],
                key=lambda x: x[1].get("hit_rate", 100),
            )[:3]
            if weak:
                w_parts = [f"{s} {v.get('hits', 0)}/{v.get('total', 0)}"
                           for s, v in weak]
                lines.append(f"- 弱项板块 Top3: {' | '.join(w_parts)}")
        lines.append("")

    # ── Round 1 案例详情（调试用）────────────────────────────
    train_results = r1.get("train_results", []) if r1 else []
    if train_results:
        lines.append("## Round 1 案例详情（调试排查）")
        lines.append("")
        lines.append("> 💡 用于排查：选股是否合理 / AI 分析方向是否错误 / 数据是否异常 / 代码是否有 bug")
        lines.append("")

        # 按超额收益排序，最差的在前（最需要排查的）
        sorted_cases = sorted(train_results, key=lambda r: r.get("excess_return", 0))
        for idx, r in enumerate(sorted_cases):
            lines.extend(_format_case_detail(r, idx + 1))

    # ── Round 2: Opus 反思建议 ────────────────────────────────
    r2 = rounds.get("round2", {})
    proposals = r2.get("proposals", [])
    if proposals:
        lines.append("## Round 2: Opus 反思建议")
        lines.append("")
        lines.append(f"共产出 **{len(proposals)}** 条建议：")
        lines.append("")
        type_icons = {"weight": "⚖️", "rule": "📐", "tree": "🌳", "prompt": "💬"}
        for p in proposals:
            pid = p.get("id", "?")
            ptype = p.get("type", "?")
            icon = type_icons.get(ptype, "•")
            target = p.get("target", "?")
            current = p.get("current_value", "?")
            proposed = p.get("proposed_value", "?")
            confidence = p.get("confidence", "?")
            lines.append(f"### {icon} [{pid}] {ptype}: {target}")
            lines.append(f"- **变更**: `{str(current)[:80]}` → `{str(proposed)[:80]}`")
            lines.append(f"- **置信度**: {confidence}")
            evidence = p.get("evidence", "")
            if evidence:
                lines.append(f"- **证据**: {evidence[:200]}")
            expected = p.get("expected_effect", "")
            if expected:
                lines.append(f"- **预期效果**: {expected[:200]}")
            risk = p.get("risk", "")
            if risk:
                lines.append(f"- **风险**: {risk[:200]}")
            lines.append("")

    # ── Round 3: 交叉审视 ────────────────────────────────────
    r3 = rounds.get("round3", {})
    if r3:
        lines.append("## Round 3: 交叉审视")
        lines.append("")
        original = r3.get("original_count", 0)
        adopted = r3.get("adopted_count", 0)
        rejected = original - adopted
        lines.append(f"- 风控官审查: **{original}** 条 → 通过 **{adopted}** 条, 否决 **{rejected}** 条")
        lines.append("")
        if r3.get("adopted_proposals"):
            lines.append("**通过审视的建议:**")
            for p in r3["adopted_proposals"]:
                lines.append(f"- [{p.get('id')}] {p.get('type')}: {p.get('target', '?')[:60]}")
            lines.append("")

    # ── Round 4: 应用候选配置 ─────────────────────────────────
    r4 = rounds.get("round4", {})
    if r4:
        lines.append("## Round 4: 应用候选配置")
        lines.append("")
        applied = r4.get("applied_count", 0)
        errors = r4.get("errors", [])
        has_prompt = r4.get("has_prompt_changes", False)
        lines.append(f"- 成功应用: **{applied}** 条")
        if errors:
            lines.append(f"- 安全边界拒绝: **{len(errors)}** 条")
            for e in errors[:3]:
                lines.append(f"  - [{e.get('proposal_id')}] {'; '.join(e.get('errors', []))}")
        if has_prompt:
            lines.append(f"- ⏳ 有 prompt 变更 → 已发邮件待你人工审批")
        lines.append("")

    # ── Round 5: 验证集对比 ───────────────────────────────────
    r5 = rounds.get("round5", {})
    if r5:
        lines.append("## Round 5: 验证集对比")
        lines.append("")
        old_stats = r5.get("old_stats", {})
        new_stats = r5.get("new_stats", {})
        old_hr = old_stats.get("hit_rate", 0)
        new_hr = new_stats.get("hit_rate", 0)
        delta = new_hr - old_hr
        arrow = "📈" if delta > 0 else ("📉" if delta < 0 else "➡️")
        lines.append(f"- 旧配置: 胜率 {old_hr}% | 校准度 {old_stats.get('calibration', 0)}")
        lines.append(f"- 新配置: 胜率 {new_hr}% | 校准度 {new_stats.get('calibration', 0)}")
        lines.append(f"- 变化: {arrow} **{delta:+.1f}%**")
        lines.append("")
        if r5.get("adopted"):
            lines.append(f"**✅ 采纳判定**: 全部门槛达标，新配置已提升到生产")
        else:
            lines.append(f"**⚠️ 回退判定**: {r5.get('reason', '?')}")
        lines.append("")

    # ── 一句话结论 ──────────────────────────────────────────
    lines.append("---")
    lines.append("")
    lines.append(f"> {result.get('summary', '')}")

    return "\n".join(lines)


def save_summary_html(result: dict, mode: str, count: int) -> str:
    """保存 HTML 摘要到 learning_log 目录，返回文件路径。"""
    from knowledge.learning_config import LEARNING_LOG_DIR, ensure_staging
    from utils.html_render import md_to_html
    ensure_staging()
    md_content = build_summary_markdown(result)
    title = f"学习引擎报告 [{result.get('mode', '?')}×{result.get('count', 0)}]"
    html_content = md_to_html(md_content, title=title)
    # 还原被转义的 <details>/<summary> 标签和代码块（用于折叠案例详情）
    html_content = (
        html_content
        .replace("&lt;details&gt;", "<details>")
        .replace("&lt;/details&gt;", "</details>")
        .replace("&lt;summary&gt;", "<summary style='cursor:pointer;color:#0066cc;font-weight:bold;padding:8px 0;'>")
        .replace("&lt;/summary&gt;", "</summary>")
    )
    filename = f"{datetime.now().strftime('%Y-%m-%d_%H%M')}_{mode}_{count}_summary.html"
    path = LEARNING_LOG_DIR / filename
    path.write_text(html_content, encoding="utf-8")
    return str(path)


def open_in_browser(html_path: str):
    """在默认浏览器中打开 HTML 文件。"""
    import webbrowser
    from pathlib import Path
    url = Path(html_path).resolve().as_uri()
    try:
        webbrowser.open(url)
    except Exception:
        pass


def send_summary_email(result: dict, html_path: str = ""):
    """发送 HTML 摘要邮件。"""
    try:
        from utils.email_sender import send_html_email, smtp_configured
        if not smtp_configured():
            return
        from utils.html_render import md_to_html
        md_content = build_summary_markdown(result)
        title = f"学习引擎报告 [{result.get('mode', '?')}×{result.get('count', 0)}]"
        html_content = md_to_html(md_content, title=title)
        subject = f"学习引擎报告 [{result.get('status', '?')}] {result.get('mode', '?')}×{result.get('count', 0)}"
        send_html_email(subject, html_content)
    except Exception:
        pass
