"""Persist Top10 research reports into Stock_lite storage."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path

from utils.app_config import get_secret as _get_secret


def _resolve_stock_lite_storage_dir() -> Path:
    repo_root = Path(__file__).resolve().parent.parent
    candidates = [
        repo_root.parent / "app" / "storage",
        repo_root.parent / "Stock_lite" / "storage",
        repo_root / "storage",
    ]
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


STORAGE_DIR = _resolve_stock_lite_storage_dir()
TOP10_REPORTS_DIR = STORAGE_DIR / "top10"
DB_PATH = STORAGE_DIR / "reports.db"
PUBLIC_BASE_URL = _get_secret("BASE_URL", _get_secret("PUBLIC_BASE_URL", "http://8.130.158.231")).rstrip("/")


def _ensure_storage() -> None:
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    TOP10_REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def init_db() -> None:
    _ensure_storage()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reports (
                report_id TEXT PRIMARY KEY,
                openid TEXT NOT NULL,
                stock_name TEXT NOT NULL,
                stock_code TEXT NOT NULL,
                summary TEXT NOT NULL,
                markdown_path TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def _safe_slug(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", value or "")
    cleaned = cleaned.strip("._-")
    return cleaned[:40] or fallback


def save_top10_report(
    *,
    report_id: str,
    owner: str,
    stock_name: str,
    stock_code: str,
    summary: str,
    markdown_text: str,
) -> tuple[str, str]:
    """Save report markdown and register it in Stock_lite's report index."""
    init_db()

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = _safe_slug(stock_name, "stock")
    safe_code = _safe_slug(stock_code, "code")
    owner_suffix = _safe_slug(owner[-16:], "top10")
    filename = f"{timestamp}_{safe_name}_{safe_code}_{owner_suffix}_{report_id}.md"
    markdown_path = TOP10_REPORTS_DIR / filename
    markdown_path.write_text(markdown_text, encoding="utf-8")

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO reports (
                report_id, openid, stock_name, stock_code, summary, markdown_path, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report_id,
                owner,
                stock_name,
                stock_code,
                summary,
                str(markdown_path),
                created_at,
            ),
        )
        conn.commit()

    report_url = f"{PUBLIC_BASE_URL}/report/{report_id}"
    return str(markdown_path), report_url
