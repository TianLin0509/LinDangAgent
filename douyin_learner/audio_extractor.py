# -*- coding: utf-8 -*-
"""音频提取 — FFmpeg 从视频中提取 WAV 16kHz 单声道（Whisper 最佳输入）。"""

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def _get_ffmpeg() -> str:
    """获取已安装的 FFmpeg 路径（复用 imageio-ffmpeg）。"""
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def extract_audio(video_path: Path, output_path: Path | None = None) -> Path:
    """从视频提取音频为 WAV 16kHz mono。

    output_path 默认为同目录下 audio.wav。
    """
    if output_path is None:
        output_path = video_path.parent / "audio.wav"

    from knowledge.kb_config import MIN_AUDIO_SIZE as _MIN_AUDIO
    if output_path.exists() and output_path.stat().st_size >= _MIN_AUDIO:
        logger.info("[audio] 已有缓存: %s", output_path)
        return output_path

    ffmpeg = _get_ffmpeg()
    cmd = [
        ffmpeg, "-i", str(video_path),
        "-vn",                    # 不要视频
        "-acodec", "pcm_s16le",   # 16-bit PCM
        "-ar", "16000",           # 16kHz（Whisper 标准）
        "-ac", "1",               # 单声道
        "-y",                     # 覆盖
        str(output_path),
    ]

    from knowledge.kb_config import MIN_AUDIO_SIZE

    logger.info("[audio] 提取音频: %s → %s", video_path.name, output_path.name)
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
    except subprocess.TimeoutExpired:
        if output_path.exists():
            output_path.unlink()
        raise RuntimeError(f"FFmpeg 音频提取超时 (>120s): {video_path}")

    if result.returncode != 0:
        if output_path.exists():
            output_path.unlink()
        raise RuntimeError(f"FFmpeg 音频提取失败: {result.stderr[:300]}")

    if not output_path.exists() or output_path.stat().st_size < MIN_AUDIO_SIZE:
        if output_path.exists():
            output_path.unlink()
        raise RuntimeError("FFmpeg 输出文件无效或太小")

    logger.info("[audio] 提取完成: %.1f MB", output_path.stat().st_size / 1024 / 1024)
    return output_path
