"""知识注入 v2 — AI 策展架构

两层设计：
  第一层（Python，零AI成本）：从所有知识库收集候选池 + 构建股票画像
  第二层（Claude Sonnet，~15-20s）：AI 从候选池中智能选取最相关的知识

回退：AI 策展失败时用规则拼接（保留旧逻辑）。
"""

import logging
from datetime import datetime

from knowledge.kb_config import DIRECTION_CN

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# 公开接口（保持向后兼容）
# ══════════════════════════════════════════════════════════════════

def build_knowledge_context(
    stock_code: str = "",
    stock_name: str = "",
    scores: dict | None = None,
    model_name: str = "",
    max_chars: int = 4000,
    price_snapshot: str = "",
    indicators: dict | None = None,
) -> str:
    """构建知识库参考段落（主入口）。

    优先走 AI 策展（第二层），失败时回退到规则拼接。
    新增 price_snapshot / indicators 参数，用于构建股票画像。
    """
    # 第一层：收集候选池 + 股票画像
    profile = _build_stock_profile(stock_code, stock_name, price_snapshot, indicators)
    candidates = _collect_knowledge_candidates(stock_code, stock_name, scores, model_name)

    if not candidates:
        return ""

    # 第二层：AI 策展
    try:
        curated = _ai_curate(profile, candidates, stock_name)
        if curated and len(curated) > 50:
            logger.info("[injector] AI curated: %d chars from %d candidates", len(curated), len(candidates))
            return curated
    except Exception as exc:
        logger.warning("[injector] AI curation failed, falling back to rules: %r", exc)

    # 回退：规则拼接
    return _fallback_rule_based(candidates, max_chars)


def build_pattern_context(scores: dict, max_chars: int = 400) -> str:
    """用实际评分做模式匹配（报告生成后调用）。保持不变。"""
    if not scores:
        return ""
    sections = []
    try:
        from knowledge.pattern_memory import match_current
        matched = match_current(scores)
        for m in matched[:2]:
            if m.get("sample_count", 0) >= 3:
                sections.append(
                    f"▸ 模式匹配：\"{m['description']}\"（{m['sample_count']}样本），"
                    f"10日胜率{m['win_rate_10d']:.0f}%，"
                    f"平均收益{m['avg_return_10d']:+.1f}%"
                )
            elif m.get("sample_count", 0) >= 1:
                sections.append(
                    f"▸ 模式匹配：\"{m['description']}\"（初步，仅{m['sample_count']}样本）"
                )
    except Exception as exc:
        logger.debug("[injector] build_pattern_context error: %r", exc)
    if not sections:
        return ""
    body = "\n".join(sections)
    return f"【系统模式匹配参考（基于本次评分）】\n{body}\n⚠️ 样本量有限，仅供辅助参考"[:max_chars]


# ══════════════════════════════════════════════════════════════════
# 第一层：股票画像 + 候选池收集
# ══════════════════════════════════════════════════════════════════

def _build_stock_profile(stock_code: str, stock_name: str,
                         price_snapshot: str = "", indicators: dict | None = None) -> str:
    """构建股票画像（~500字），给策展 AI 提供决策上下文。"""
    lines = [f"【待分析股票画像】{stock_name}（{stock_code}）"]

    # K线形态
    if stock_code:
        try:
            from data.tushare_client import get_price_df
            from knowledge.kline_patterns import detect_all_patterns, classify_position, classify_volume_state
            df, _err = get_price_df(stock_code, days=60)
            if df is not None and len(df) >= 10:
                patterns = detect_all_patterns(df)
                if patterns:
                    names = [p.name for p in patterns[:4]]
                    lines.append(f"K线形态: {', '.join(names)}")
                lines.append(f"位置: {classify_position(df)} | 量能: {classify_volume_state(df)}")
        except Exception as exc:
            logger.debug("[injector] kline analysis failed: %r", exc)

    # 价格快照
    if price_snapshot:
        # 取前200字精简
        lines.append(f"价格: {price_snapshot[:200]}")

    # 关键指标
    if indicators:
        parts = []
        if "rsi_14" in indicators:
            parts.append(f"RSI={indicators['rsi_14']:.0f}")
        if "macd_signal" in indicators:
            parts.append(f"MACD:{indicators['macd_signal']}")
        if "ma_score" in indicators:
            parts.append(f"MA评分={indicators['ma_score']}/5")
        if "adx_14" in indicators:
            parts.append(f"ADX={indicators['adx_14']:.0f}")
        if parts:
            lines.append(f"指标: {' | '.join(parts)}")

    # 板块
    try:
        from knowledge.case_memory import extract_sector_tags
        tags = extract_sector_tags(stock_name)
        if tags:
            lines.append(f"板块: {', '.join(tags[:3])}")
    except Exception as exc:
        logger.debug("[injector] sector tag extraction failed: %r", exc)

    # 市场环境
    try:
        from knowledge.regime_detector import get_current_regime
        regime = get_current_regime()
        if regime:
            lines.append(f"市场环境: {regime.get('regime_label', '未知')}")
    except Exception as exc:
        logger.debug("[injector] regime fetch failed: %r", exc)


    return "\n".join(lines)


def _collect_knowledge_candidates(stock_code: str, stock_name: str,
                                  scores: dict | None, model_name: str) -> list[dict]:
    """从所有知识库收集候选知识，返回带类型和内容的列表。"""
    candidates = []

    # 获取公共参数
    regime_code = ""
    regime_label = ""
    try:
        from knowledge.regime_detector import get_current_regime
        r = get_current_regime()
        if r:
            regime_code = r.get("regime", "")
            regime_label = r.get("regime_label", "")
    except Exception as exc:
        logger.debug("[injector] regime label fetch failed: %r", exc)

    sector_tags = []
    try:
        from knowledge.case_memory import extract_sector_tags
        sector_tags = extract_sector_tags(stock_name, stock_code=stock_code) if (stock_name or stock_code) else []
    except Exception as exc:
        logger.debug("[injector] sector tag failed: %r", exc)

    # ── 1. 该股历史（不可替代，AI策展时为必选参考）──────────────
    if stock_code:
        try:
            from knowledge.outcome_tracker import get_stock_history
            history = get_stock_history(stock_code)
            if history:
                recent = sorted(history, key=lambda x: x.get("report_date", ""), reverse=True)[:5]
                lines = []
                for h in recent:
                    dir_cn = DIRECTION_CN.get(h.get("direction", ""), "中性")
                    # 判断方向是否正确
                    hit_10d = h.get("hit_10d")
                    ret_5d = h.get("return_5d", 0)
                    ret_10d = h.get("return_10d", 0)
                    ret_20d = h.get("return_20d")
                    outcome_mark = ""
                    if hit_10d is True:
                        outcome_mark = " ✅判断正确"
                    elif hit_10d is False:
                        outcome_mark = " ❌判断错误"
                    ret_detail = f"5日{ret_5d:+.1f}%/10日{ret_10d:+.1f}%"
                    if ret_20d is not None:
                        ret_detail += f"/20日{ret_20d:+.1f}%"
                    rating = h.get("rating", "")
                    rating_str = f"({rating})" if rating else ""
                    lines.append(
                        f"{h.get('report_date', '?')} 评{h.get('weighted_score', '?')}分"
                        f"{rating_str}{dir_cn} → {ret_detail}{outcome_mark}"
                    )
                # 统计历史命中率
                hits = [h for h in history if h.get("hit_10d") is not None]
                hit_summary = ""
                if hits:
                    hit_count = sum(1 for h in hits if h.get("hit_10d"))
                    hit_summary = f"\n历史命中率: {hit_count}/{len(hits)}({hit_count/len(hits)*100:.0f}%)"
                candidates.append({
                    "type": "该股历史",
                    "priority": 10,
                    "must_include": True,  # AI策展时必选
                    "content": f"该股过去{len(history)}次分析记录（最近5次）：\n"
                               + "\n".join(lines) + hit_summary,
                })
        except Exception as exc:
            logger.debug("[candidates] stock history: %r", exc)

    # ── 2. 校准警示（不可替代）──────────────────────────────────
    try:
        from knowledge.analyst_scorecard import get_calibration_advice
        advices = get_calibration_advice(min_samples=5)
        if advices:
            candidates.append({
                "type": "校准警示",
                "priority": 9,
                "content": "\n".join(advices),
            })
    except Exception as exc:
        logger.debug("[candidates] calibration: %r", exc)

    # ── 3. 案例经验（高价值）──────────────────────────────────
    try:
        from knowledge.case_memory import retrieve_similar_cases
        cases = retrieve_similar_cases(
            regime=regime_code, sector_tags=sector_tags,
            current_scores=scores or {}, stock_code=stock_code, top_k=3,
        )
        if cases:
            lines = []
            for c in cases:
                mark = {"win": "✅", "loss": "❌", "draw": "➖"}.get(c.outcome_type, "")
                lines.append(
                    f"[{c.report_date} {c.regime_label} {c.stock_name}] "
                    f"评{c.score_weighted}分{c.direction_cn} → 10日{c.return_10d:+.1f}% {mark}"
                )
                if c.lesson:
                    lines.append(f"  教训: {c.lesson[:120]}")
            candidates.append({
                "type": "相似案例",
                "priority": 8,
                "content": "\n".join(lines),
            })

            # 板块经验
            if sector_tags:
                from knowledge.case_memory import get_sector_summary
                for tag in sector_tags[:2]:
                    summary = get_sector_summary(tag)
                    if summary:
                        mistakes = "、".join(summary["common_mistakes"][:3]) if summary["common_mistakes"] else "暂无"
                        candidates.append({
                            "type": f"板块经验[{tag}]",
                            "priority": 7,
                            "content": f"过去90天分析{summary['total_cases']}只，"
                                       f"10日胜率{summary['win_rate_10d']:.0f}%，常见失误：{mistakes}",
                        })
    except Exception as exc:
        logger.debug("[candidates] cases: %r", exc)

    # ── 4. 跨股盘感（高价值，NEW）──────────────────────────────
    if stock_code:
        try:
            from knowledge.kline_patterns import detect_all_patterns, PATTERN_INFO
            from knowledge.kline_diary import get_cross_stock_pattern_peers
            from data.tushare_client import get_price_df

            df, _err = get_price_df(stock_code, days=60)
            if df is not None and len(df) >= 10:
                patterns = detect_all_patterns(df)
                if patterns:
                    pattern_ids = [p.pattern_id for p in patterns]
                    peers = get_cross_stock_pattern_peers(
                        pattern_ids, regime=regime_code,
                        exclude_code=stock_code, days=14, limit=5,
                    )
                    if peers:
                        lines = []
                        verified = [p for p in peers if p.get("actual_return_5d") is not None]
                        for p in peers[:5]:
                            ret = f"5日{p['actual_return_5d']:+.1f}% {p['hit']}" if p.get("actual_return_5d") is not None else "待验证"
                            lines.append(f"{p['date']} {p['stock_name']}: {'+'.join(p['patterns'][:2])} → {ret}")

                        if verified:
                            hits = sum(1 for p in verified if p.get("hit") == "✅")
                            rate_str = f"已验证{len(verified)}只，胜率{hits}/{len(verified)}"
                        else:
                            rate_str = "均待验证"

                        candidates.append({
                            "type": "跨股盘感",
                            "priority": 8,
                            "content": f"近14天出现同类形态的其他个股：\n" + "\n".join(lines) + f"\n{rate_str}",
                        })
        except Exception as exc:
            logger.debug("[candidates] cross-stock: %r", exc)

    # ── 5. 盘感形态统计 ──────────────────────────────────────
    if stock_code:
        try:
            from knowledge.kline_diary import get_patterns_for_stock, get_discovered_patterns
            from knowledge.kline_patterns import PATTERN_INFO as _PI

            stats = get_patterns_for_stock(stock_code, regime=regime_code)
            if stats:
                lines = []
                for s in stats[:3]:
                    pname = _PI.get(s["pattern"], (s["pattern"], ""))[0]
                    lines.append(f"{pname}({s['regime']}{s['position']}): {s['sample_count']}样本 "
                                 f"胜率{s['win_rate_5d']:.0f}% 均{s['avg_return_5d']:+.1f}%")
                candidates.append({
                    "type": "盘感统计",
                    "priority": 7,
                    "content": "\n".join(lines),
                })

            # 自发现形态
            discoveries = get_discovered_patterns(only_verified=True)
            if discoveries:
                for d in discoveries[:2]:
                    if d.get("ai_name"):
                        candidates.append({
                            "type": "自发现形态",
                            "priority": 7,
                            "content": f"【{d['ai_name']}】{d['sample_count']}样本 "
                                       f"胜率{d['win_rate_5d']:.0f}% 均{d['avg_return_5d']:+.1f}%"
                                       + (f"\n解释: {d['ai_explanation'][:100]}" if d.get("ai_explanation") else ""),
                        })
        except Exception as exc:
            logger.debug("[candidates] kline stats: %r", exc)

    # ── 6. 市场环境+策略 ─────────────────────────────────────
    if regime_code:
        try:
            from knowledge.regime_detector import get_regime_accuracy
            regime_acc = get_regime_accuracy(regime_code)
            content = f"当前{regime_label}"
            if regime_acc.get("directional_count", 0) >= 3:
                content += f"，系统10日胜率{regime_acc['hit_rate_10d']:.0f}%（{regime_acc['directional_count']}样本）"

                # 策略建议
                hit_rate = regime_acc["hit_rate_10d"]
                n = regime_acc["directional_count"]
                if regime_code == "bear" and hit_rate < 40 and n >= 5:
                    content += "\n⚠️ 熊市纪律：看多胜率偏低，提高门槛（综合≥80），宁可错过不逆势"
                elif regime_code == "bull" and hit_rate > 55 and n >= 5:
                    content += "\n牛市策略：看多胜率较高，可适当降低门槛（综合≥65），错过代价大于犯错"
                elif regime_code == "shock" and hit_rate < 45 and n >= 5:
                    content += "\n震荡市策略：胜率一般，精选确定性高的标的，宁缺毋滥"

            candidates.append({"type": "市场环境", "priority": 6, "content": content})
        except Exception as exc:
            logger.debug("[injector] regime candidate failed: %r", exc)

    # ── 7. 投资信念 ──────────────────────────────────────────
    try:
        from knowledge.thesis_journal import get_beliefs_for_context
        beliefs = get_beliefs_for_context(sectors=sector_tags, regime=regime_code)
        if beliefs:
            lines = [f"[{b['confidence']*100:.0f}%] {b['belief']}" for b in beliefs[:6]]
            candidates.append({
                "type": "投资信念",
                "priority": 5,
                "content": "\n".join(lines),
            })
    except Exception as exc:
        logger.debug("[candidates] beliefs: %r", exc)

    # ── 8. 投资智慧 ──────────────────────────────────────────
    try:
        from knowledge.wisdom import get_wisdom_for_context
        wisdoms = get_wisdom_for_context(sectors=sector_tags, regime=regime_code, top_k=5)
        if wisdoms:
            lines = [f"[{w['source_name']}] {w['wisdom']}" for w in wisdoms]
            candidates.append({
                "type": "投资智慧",
                "priority": 4,
                "content": "\n".join(lines),
            })
    except Exception as exc:
        logger.debug("[candidates] wisdom: %r", exc)

    # ── 9. 近期情报 ──────────────────────────────────────────
    if sector_tags:
        try:
            from knowledge.intel_memory import query_by_sectors
            intels = query_by_sectors(sector_tags, days=30)
            if intels:
                lines = []
                for intel in intels[:3]:
                    sentiment_cn = DIRECTION_CN.get(intel.get("sentiment", ""), "中性")
                    themes = "、".join(intel.get("themes", [])[:3])
                    lines.append(f"{intel['analyzed_at'][:10]} [{themes}] {sentiment_cn}: "
                                 f"{intel.get('implications', '')[:80]}")
                candidates.append({
                    "type": "近期情报",
                    "priority": 4,
                    "content": "\n".join(lines),
                })
        except Exception as exc:
            logger.debug("[candidates] intel: %r", exc)

    # ── 10. 宏观简报 ─────────────────────────────────────────
    try:
        from data.macro_intel import get_macro_context
        _, macro_brief = get_macro_context()
        if macro_brief and len(macro_brief) > 20:
            candidates.append({
                "type": "宏观简报",
                "priority": 3,
                "content": macro_brief[:400],
            })
    except Exception as exc:
        logger.debug("[injector] belief candidate failed: %r", exc)

    return candidates


# ══════════════════════════════════════════════════════════════════
# 第二层：AI 策展
# ══════════════════════════════════════════════════════════════════

CURATE_SYSTEM = (
    "你是林铛的知识策展助手。你的任务是从候选知识池中挑选与当前待分析股票最相关的信息，"
    "重组为一份简洁、有针对性的分析参考。"
    "直接输出参考文本，不要输出任何额外解释或前言。"
)

CURATE_PROMPT = """即将分析以下股票，请从候选知识池中挑选最相关的 5-8 条信息，
重组为简洁的【历史知识库参考】段落。

{profile}

--- 必选参考（必须全部保留，不得省略） ---
{must_include_text}
--- 候选知识池 ---
{candidates_text}
--- 候选结束 ---

要求：
1. 必选参考中的内容必须全部保留，这些是不可替代的校准信息
2. 优先选择：校准警示、跨股盘感、相似案例教训
3. 选择性纳入：与当前股票技术形态/板块/环境相关的信念、智慧、情报
4. 如果候选池中有矛盾信息（如看多vs看空），都要保留并标注分歧
5. 输出格式：用 ▸ 开头的条目列表，每条 1-2 行，按重要性排序
6. 末尾加一行："⚠️ 以上为历史知识参考，样本量有限时仅供辅助判断"
7. 总长度控制在 1500-3000 字，信息少时可以更短
"""


def _ai_curate(profile: str, candidates: list[dict], stock_name: str) -> str:
    """用 Claude Sonnet 从候选池中智能选取知识。"""
    if not candidates:
        return ""

    from ai.client import call_ai, get_ai_client

    # 分离必选和候选
    must_include = [c for c in candidates if c.get("must_include")]
    optional = [c for c in candidates if not c.get("must_include")]

    must_parts = []
    for c in sorted(must_include, key=lambda x: x.get("priority", 0), reverse=True):
        must_parts.append(f"[{c['type']}]\n{c['content']}")
    must_include_text = "\n\n".join(must_parts) if must_parts else "（无）"

    candidates_parts = []
    for c in sorted(optional, key=lambda x: x.get("priority", 0), reverse=True):
        candidates_parts.append(f"[{c['type']}]\n{c['content']}")
    candidates_text = "\n\n".join(candidates_parts)

    prompt = CURATE_PROMPT.format(
        profile=profile,
        must_include_text=must_include_text,
        candidates_text=candidates_text,
    )

    # 用 Claude Sonnet
    model_name = "⚡ Claude Sonnet（MAX）"
    client, cfg, err = get_ai_client(model_name)
    if err and not cfg:
        logger.warning("[injector] Sonnet unavailable for curation: %s", err)
        return ""

    cfg_no_search = {**cfg, "supports_search": False}
    text, call_err = call_ai(client, cfg_no_search, prompt, system=CURATE_SYSTEM, max_tokens=2000)
    if call_err:
        logger.warning("[injector] AI curation call failed: %s", call_err)
        return ""

    text = text.strip()
    if not text:
        return ""

    # 确保有标题
    if not text.startswith("【"):
        text = "【历史知识库参考】\n" + text

    logger.info("[injector] AI curated for %s: %d chars", stock_name, len(text))
    return text


# ══════════════════════════════════════════════════════════════════
# 回退：规则拼接（旧逻辑简化版）
# ══════════════════════════════════════════════════════════════════

def _fallback_rule_based(candidates: list[dict], max_chars: int = 4000) -> str:
    """AI 策展失败时的回退方案：按优先级拼接。"""
    if not candidates:
        return ""

    # 按优先级降序排列
    sorted_candidates = sorted(candidates, key=lambda x: x.get("priority", 0), reverse=True)

    header = "【历史知识库参考】"
    footer = "⚠️ 以上为统计参考，样本量有限时仅供辅助判断"

    sections = []
    current_len = len(header) + len(footer) + 2

    for c in sorted_candidates:
        section = f"▸ {c['type']}：{c['content']}"
        if current_len + len(section) + 1 > max_chars:
            break
        sections.append(section)
        current_len += len(section) + 1

    if not sections:
        return ""

    return f"{header}\n" + "\n".join(sections) + f"\n{footer}"


def _short_model(model_name: str) -> str:
    """缩写模型名称。"""
    if "豆包" in model_name:
        return "豆包Pro" if "Pro" in model_name else "豆包Mini"
    if "Qwen" in model_name or "千问" in model_name:
        return "Qwen"
    if "GLM" in model_name or "智谱" in model_name:
        return "GLM-5"
    if "DeepSeek" in model_name:
        return "DeepSeek"
    if "Gemini" in model_name:
        return "Gemini3" if "3" in model_name else "Gemini2.5"
    if "GPT-5" in model_name:
        return "GPT-5.2"
    if "GPT-4" in model_name:
        return "GPT-4o"
    return model_name[:10]
