# -*- coding: utf-8 -*-
"""流水线编排 — 从视频 URL 到知识入库的完整流程。"""

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def run_video_learn(source: str, model_name: str = "") -> dict:
    """完整流水线：下载 → 提取音频 → 转录 → 提炼 → 入库。

    source: 抖音 URL 或本地视频路径。
    model_name: 用于 AI 提炼的模型（默认豆包 Lite）。
    返回与 cmd_intel_analyze 兼容的结果格式。
    """
    from .downloader import download_video
    from .audio_extractor import extract_audio
    from .transcriber import transcribe
    from .distiller import distill_knowledge

    result = {"status": "ok", "source": source, "steps": {}}

    # ── Step 1: 下载视频 ──
    logger.info("[pipeline] Step 1/4: 下载视频 ...")
    try:
        dl = download_video(source)
        result["steps"]["download"] = "ok"
        result["video_id"] = dl.video_id
        result["title"] = dl.title
    except Exception as e:
        return {"status": "error", "message": f"下载失败: {e}", "source": source}

    # 进度文件
    progress_path = dl.video_path.parent / "progress.json"
    _save_progress(progress_path, "downloading", "completed")

    # ── Step 2: 提取音频 ──
    logger.info("[pipeline] Step 2/4: 提取音频 ...")
    try:
        audio_path = extract_audio(dl.video_path)
        result["steps"]["audio"] = "ok"
        _save_progress(progress_path, "audio_extract", "completed")
    except Exception as e:
        _save_progress(progress_path, "audio_extract", "failed")
        return {"status": "error", "message": f"音频提取失败: {e}", "source": source}

    # ── Step 3: Whisper 转录 ──
    logger.info("[pipeline] Step 3/4: Whisper 转录 ...")
    _save_progress(progress_path, "transcribing", "in_progress")
    try:
        transcript = transcribe(audio_path)
        result["steps"]["transcribe"] = "ok"
        result["transcript_length"] = len(transcript.text)
        result["duration"] = transcript.duration
        _save_progress(progress_path, "transcribing", "completed")
    except Exception as e:
        _save_progress(progress_path, "transcribing", "failed")
        return {"status": "error", "message": f"转录失败: {e}", "source": source}

    if not transcript.text.strip():
        _save_progress(progress_path, "transcribing", "failed")
        return {"status": "error", "message": "转录结果为空（视频可能无语音）", "source": source}

    # ── Step 4: AI 提炼 + 入库 ──
    logger.info("[pipeline] Step 4/4: AI 提炼知识 ...")
    _save_progress(progress_path, "distilling", "in_progress")

    video_meta = {
        "title": dl.title or "抖音视频",
        "uploader": dl.meta.get("uploader", ""),
        "duration": dl.duration or transcript.duration,
    }

    structured = distill_knowledge(transcript.text, video_meta, model_name)

    if not structured:
        _save_progress(progress_path, "distilling", "failed")
        return {"status": "error", "message": "AI 提炼失败", "source": source}

    # 保存提炼结果到本地
    distilled_path = dl.video_path.parent / "distilled.json"
    distilled_path.write_text(
        json.dumps(structured, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    # 构建完整的分析文本（用于 intel_memory 的 full_analysis）
    summary = structured.get("summary", "")
    implications = structured.get("implications", "")
    analysis_text = (
        f"## 视频学习笔记\n\n"
        f"**来源**: {source}\n"
        f"**标题**: {video_meta['title']}\n"
        f"**作者**: {video_meta['uploader']}\n"
        f"**时长**: {video_meta['duration']:.0f}秒\n\n"
        f"### 核心观点\n{summary}\n\n"
        f"### 市场影响\n{implications}\n\n"
        f"### 提及股票\n{', '.join(structured.get('mentioned_stocks', []))}\n\n"
        f"### 关键事实\n" +
        '\n'.join(f"- {f}" for f in structured.get('key_facts', []))
    )

    # 存入 intel_memory
    from knowledge.intel_memory import store_intel
    entry_id = store_intel(
        url=source,
        title=f"[视频] {video_meta['title']}"[:80],
        model=model_name or "🟡 豆包 · Seed 2.0 Lite",
        analysis=analysis_text,
        structured=structured,
        source_type="douyin_video",
    )

    result["entry_id"] = entry_id
    result["steps"]["distill"] = "ok"
    _save_progress(progress_path, "distilling", "completed")
    result["themes"] = structured.get("themes", [])
    result["sectors"] = structured.get("affected_sectors", [])
    result["sentiment"] = structured.get("sentiment", "neutral")
    result["summary"] = summary[:200]
    result["analysis"] = analysis_text

    _save_progress(progress_path, "completed", "completed")
    logger.info("[pipeline] 完成! entry_id=%s, themes=%s", entry_id, result["themes"])

    return result


def _save_progress(path: Path, step: str, status: str):
    """更新进度文件。"""
    try:
        data = {}
        if path.exists():
            data = json.loads(path.read_text("utf-8"))
        data[step] = status
        data["updated_at"] = datetime.now().isoformat(timespec="seconds")
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.debug("[pipeline] 进度文件更新失败: %r", exc)
