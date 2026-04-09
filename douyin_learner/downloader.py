# -*- coding: utf-8 -*-
"""视频下载 — yt-dlp 首选，Playwright 备选，本地文件兜底。"""

import hashlib
import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

from knowledge.kb_config import DOUYIN_STORAGE_DIR as STORAGE_DIR, MIN_VIDEO_SIZE


@dataclass
class DownloadResult:
    video_path: Path
    title: str = ""
    duration: float = 0.0
    video_id: str = ""
    is_local: bool = False
    url: str = ""
    meta: dict = field(default_factory=dict)


def _video_id_from_url(url: str) -> str:
    """从 URL 生成稳定的短 ID。"""
    # 尝试提取抖音 video id
    m = re.search(r'/video/(\d+)', url)
    if m:
        return m.group(1)
    # fallback: hash
    return hashlib.md5(url.encode()).hexdigest()[:12]


def _video_id_from_path(path: Path) -> str:
    return hashlib.md5(str(path).encode()).hexdigest()[:12]


def download_video(source: str) -> DownloadResult:
    """下载或验证视频文件。

    source: 抖音 URL（含分享短链）或本地文件路径。
    返回 DownloadResult。
    """
    # 本地文件
    local = Path(source)
    if local.exists() and local.is_file():
        vid = _video_id_from_path(local)
        out_dir = STORAGE_DIR / vid
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / f"video{local.suffix}"
        if not dest.exists():
            shutil.copy2(str(local), str(dest))
        return DownloadResult(
            video_path=dest, title=local.stem, video_id=vid,
            is_local=True, url=str(local),
        )

    # URL → yt-dlp 下载
    vid = _video_id_from_url(source)
    out_dir = STORAGE_DIR / vid
    out_dir.mkdir(parents=True, exist_ok=True)

    dest = out_dir / "video.mp4"
    if dest.exists() and dest.stat().st_size > MIN_VIDEO_SIZE:
        logger.info("[downloader] 已有缓存: %s", dest)
        meta = _load_meta(out_dir)
        return DownloadResult(
            video_path=dest, title=meta.get("title", ""),
            duration=meta.get("duration", 0),
            video_id=vid, url=source, meta=meta,
        )

    # 尝试 yt-dlp
    err = _try_ytdlp(source, dest, out_dir)
    if err is None:
        meta = _load_meta(out_dir)
        return DownloadResult(
            video_path=dest, title=meta.get("title", ""),
            duration=meta.get("duration", 0),
            video_id=vid, url=source, meta=meta,
        )

    logger.warning("[downloader] yt-dlp 失败: %s，尝试 Playwright ...", err)

    # 尝试 Playwright
    err2 = _try_playwright(source, dest)
    if err2 is None and dest.exists():
        return DownloadResult(
            video_path=dest, title="", video_id=vid, url=source,
        )

    raise RuntimeError(
        f"视频下载失败。\n"
        f"  yt-dlp: {err}\n"
        f"  playwright: {err2}\n"
        f"你可以手动下载后，提供本地路径再试。"
    )


def _try_ytdlp(url: str, dest: Path, out_dir: Path) -> str | None:
    """用 yt-dlp 下载。成功返回 None，失败返回错误信息。"""
    try:
        import yt_dlp
    except ImportError:
        return "yt-dlp 未安装"

    info_path = out_dir / "info.json"
    ydl_opts = {
        "format": "best[ext=mp4]/best",
        "outtmpl": str(dest),
        "writeinfojson": True,
        "infojson_filename": str(info_path),
        "quiet": True,
        "no_warnings": True,
        # 使用浏览器 cookies 应对登录限制
        "cookiesfrombrowser": ("chrome",),
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        if dest.exists() and dest.stat().st_size > MIN_VIDEO_SIZE:
            return None
        if dest.exists():
            dest.unlink()  # 清理不完整文件
        return "下载后文件为空或太小"
    except Exception as e:
        logger.debug("[downloader] yt-dlp 完整错误: %s", e)
        return str(e)[:200]


def _try_playwright(url: str, dest: Path) -> str | None:
    """用 Playwright 拦截视频流 URL 下载。"""
    try:
        import asyncio
        return asyncio.run(_playwright_download(url, dest))
    except Exception as e:
        logger.debug("[downloader] playwright 完整错误: %s", e)
        return str(e)[:200]


async def _playwright_download(url: str, dest: Path) -> str | None:
    """Playwright 打开抖音页面，拦截 video src，下载。"""
    try:
        from playwright.async_api import async_playwright
        import httpx
    except ImportError:
        return "playwright 或 httpx 未安装"

    video_urls = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = None
        try:
            page = await browser.new_page()

            # 拦截视频请求
            async def on_response(response):
                ct = response.headers.get("content-type", "")
                if "video" in ct:
                    video_urls.append(response.url)

            page.on("response", on_response)

            try:
                await page.goto(url, wait_until="networkidle", timeout=30000)
                await page.wait_for_timeout(5000)
            except Exception as exc:
                logger.debug("[downloader] playwright 页面加载异常（可能已拦截到视频）: %r", exc)
        finally:
            if page:
                await page.close()
            await browser.close()

    if not video_urls:
        return "未拦截到视频流 URL"

    # 下载第一个视频（校验响应）
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
            resp = await client.get(video_urls[0])
            if resp.status_code != 200:
                return f"视频下载 HTTP {resp.status_code}"
            ct = resp.headers.get("content-type", "")
            if "text/html" in ct:
                return "视频 URL 返回 HTML（可能是登录/403页面）"
            if len(resp.content) < MIN_VIDEO_SIZE:
                return f"视频文件太小: {len(resp.content)} 字节"
            dest.write_bytes(resp.content)
        return None
    except Exception as e:
        logger.debug("[downloader] playwright 下载完整错误: %s", e)
        return str(e)[:200]


def _load_meta(out_dir: Path) -> dict:
    """读取 yt-dlp 保存的 info.json 元数据。"""
    info_path = out_dir / "info.json"
    if info_path.exists():
        try:
            data = json.loads(info_path.read_text("utf-8"))
            return {
                "title": data.get("title", ""),
                "duration": data.get("duration", 0),
                "uploader": data.get("uploader", ""),
                "description": data.get("description", ""),
                "upload_date": data.get("upload_date", ""),
            }
        except Exception as exc:
            logger.warning("[downloader] 无法读取元数据 %s: %s", info_path, exc)
    return {}
