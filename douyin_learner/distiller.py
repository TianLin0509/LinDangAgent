# -*- coding: utf-8 -*-
"""AI 知识提炼 — 从转录文本中提取结构化投研知识。"""

import logging

logger = logging.getLogger(__name__)

DISTILL_SYSTEM = (
    "你是资深A股投研情报分析师。从视频内容提取中，提炼投研价值。"
    "只输出 JSON，不要其他内容。"
)

DISTILL_PROMPT = """以下是一个投研类抖音视频的语音转录内容。请分析并提炼投研知识。

## 视频信息
标题：{title}
作者：{uploader}
时长：{duration}秒

## 语音转录
{transcript}

请输出严格 JSON 格式（不要 markdown 包裹）：
{{
  "themes": ["主题1", "主题2"],
  "affected_sectors": ["板块1", "板块2"],
  "sentiment": "bullish 或 bearish 或 neutral",
  "key_facts": ["事实1", "事实2", "事实3"],
  "implications": "对A股市场的核心影响（200字内）",
  "mentioned_stocks": ["股票1", "股票2"],
  "source_credibility": "high 或 medium 或 low",
  "knowledge_type": "opinion 或 data 或 analysis 或 news",
  "summary": "视频核心观点概述（300字内）"
}}

要求：
- themes: 2-5个核心主题关键词
- affected_sectors: 涉及的A股板块
- key_facts: 最重要的3-5个事实或数据点
- mentioned_stocks: 明确提及的股票名称
- summary: 浓缩视频核心观点，保留数据和逻辑
"""


def distill_knowledge(transcript_text: str, video_meta: dict,
                      model_name: str = "") -> dict:
    """AI 从转录文本中提炼结构化投研知识。

    transcript_text: Whisper 转录全文。
    video_meta: 视频元数据 (title, uploader, duration 等)。
    model_name: 用于提炼的模型名（默认用豆包 Lite）。
    返回结构化字段 dict。
    """
    from ai.client import call_ai, get_ai_client

    from knowledge.kb_config import (
        DISTILLER_PRIMARY_MODEL, DISTILLER_FALLBACK_MODEL,
        DISTILLER_MAX_TRANSCRIPT_LEN,
    )

    if not model_name:
        model_name = DISTILLER_PRIMARY_MODEL

    client, cfg, err = get_ai_client(model_name)
    if err and not cfg:
        logger.warning("[distiller] 模型 %s 不可用: %s，尝试 fallback", model_name, err)
        model_name = DISTILLER_FALLBACK_MODEL
        client, cfg, err = get_ai_client(model_name)
        if err and not cfg:
            logger.error("[distiller] fallback 也失败: %s", err)
            return {}

    # 截断过长的转录文本
    if len(transcript_text) > DISTILLER_MAX_TRANSCRIPT_LEN:
        transcript_text = (
            transcript_text[:DISTILLER_MAX_TRANSCRIPT_LEN]
            + "\n...(后续省略)...\n"
        )

    # 转义元数据中的花括号，防止 format 注入
    def _safe(val, max_len=200):
        s = str(val) if val else "未知"
        return s[:max_len].replace("{", "{{").replace("}", "}}")

    prompt = DISTILL_PROMPT.format(
        title=_safe(video_meta.get("title", "未知")),
        uploader=_safe(video_meta.get("uploader", "未知")),
        duration=video_meta.get("duration", 0),
        transcript=transcript_text,
    )

    # 关闭联网搜索，纯提炼
    cfg_no_search = {**cfg, "supports_search": False}

    text, call_err = call_ai(client, cfg_no_search, prompt,
                              system=DISTILL_SYSTEM, max_tokens=1500)
    if call_err:
        logger.error("[distiller] AI 调用失败: %s", call_err)
        return {}

    # 解析 JSON（使用统一工具）
    from knowledge.kb_utils import parse_ai_json
    parsed = parse_ai_json(text)
    if parsed is None or not isinstance(parsed, dict):
        logger.warning("[distiller] JSON 解析失败，回退为原文摘要")
        return {"summary": text.strip()[:500], "themes": [], "affected_sectors": []}
    return parsed
