# -*- coding: utf-8 -*-
"""语音转文字 — Whisper large-v3 本地 GPU 推理。

使用 transformers pipeline，充分利用 CUDA GPU 加速，零 API 成本。
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# 全局缓存，避免重复加载模型（约 3GB）
_pipe = None
_loaded_model_size = None


@dataclass
class TranscriptResult:
    text: str = ""
    segments: list[dict] = field(default_factory=list)  # [{start, end, text}]
    language: str = "zh"
    duration: float = 0.0


def _load_pipeline(model_size: str = "large-v3"):
    """懒加载 Whisper pipeline，复用已加载的模型。"""
    global _pipe, _loaded_model_size

    if _pipe is not None and _loaded_model_size == model_size:
        return _pipe

    # 切换模型时释放旧模型 GPU 内存
    if _pipe is not None and _loaded_model_size != model_size:
        logger.info("[transcriber] 释放旧模型 %s GPU 内存 ...", _loaded_model_size)
        import torch
        del _pipe
        _pipe = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    import torch
    from transformers import pipeline

    model_id = f"openai/whisper-{model_size}"
    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32

    logger.info("[transcriber] 加载模型 %s (device=%s, dtype=%s) ...", model_id, device, dtype)

    _pipe = pipeline(
        "automatic-speech-recognition",
        model=model_id,
        torch_dtype=dtype,
        device=device,
    )
    _loaded_model_size = model_size
    logger.info("[transcriber] 模型加载完成")
    return _pipe


def transcribe(audio_path: Path, model_size: str = "large-v3") -> TranscriptResult:
    """Whisper 转录音频文件。

    audio_path: WAV 16kHz mono 文件。
    model_size: large-v3（最佳中文）/ medium / small。
    返回 TranscriptResult。
    """
    cache_path = audio_path.parent / "transcript.json"
    if cache_path.exists():
        try:
            data = json.loads(cache_path.read_text("utf-8"))
            # 校验缓存有效性：model_size 和音频 mtime 必须匹配
            cached_model = data.get("_model_size", "")
            cached_audio_mtime = data.get("_audio_mtime", 0)
            audio_mtime = audio_path.stat().st_mtime if audio_path.exists() else 0
            if cached_model == model_size and abs(cached_audio_mtime - audio_mtime) < 1:
                logger.info("[transcriber] 使用缓存: %s (model=%s)", cache_path, model_size)
                return TranscriptResult(
                    text=data.get("text", ""),
                    segments=data.get("segments", []),
                    language=data.get("language", "zh"),
                    duration=data.get("duration", 0.0),
                )
            else:
                logger.info("[transcriber] 缓存失效 (model: %s→%s)，重新转录", cached_model, model_size)
        except Exception as exc:
            logger.warning("[transcriber] 缓存读取失败 %s: %s，重新转录", cache_path, exc)

    pipe = _load_pipeline(model_size)

    logger.info("[transcriber] 转录中: %s (模型: whisper-%s) ...", audio_path.name, model_size)

    result = pipe(
        str(audio_path),
        generate_kwargs={"language": "zh", "task": "transcribe"},
        chunk_length_s=30,
        batch_size=16,
        return_timestamps=True,
    )

    # 构建结果
    text = result.get("text", "")
    chunks = result.get("chunks", [])
    segments = []
    for chunk in chunks:
        ts = chunk.get("timestamp", (0, 0))
        segments.append({
            "start": ts[0] if ts[0] is not None else 0,
            "end": ts[1] if ts[1] is not None else 0,
            "text": chunk.get("text", "").strip(),
        })

    duration = segments[-1]["end"] if segments else 0

    transcript = TranscriptResult(
        text=text.strip(),
        segments=segments,
        language="zh",
        duration=duration,
    )

    # 缓存到磁盘（含 model_size 和 audio_mtime 用于失效校验）
    audio_mtime = audio_path.stat().st_mtime if audio_path.exists() else 0
    cache_path.write_text(
        json.dumps({
            "text": transcript.text,
            "segments": transcript.segments,
            "language": transcript.language,
            "duration": transcript.duration,
            "_model_size": model_size,
            "_audio_mtime": audio_mtime,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("[transcriber] 转录完成: %d 字, %.1fs", len(text), duration)
    return transcript
