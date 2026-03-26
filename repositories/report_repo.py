"""Local persistent storage for generated reports."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
STORAGE_DIR = BASE_DIR / "storage"
REPORTS_DIR = STORAGE_DIR / "reports"
DB_PATH = STORAGE_DIR / "reports.db"


def _ensure_storage() -> None:
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)


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


def save_report(
    *,
    report_id: str,
    openid: str,
    stock_name: str,
    stock_code: str,
    summary: str,
    markdown_text: str,
) -> str:
    init_db()

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = _safe_slug(stock_name, "stock")
    safe_code = _safe_slug(stock_code, "code")
    openid_suffix = _safe_slug(openid[-8:], "user")
    filename = f"{timestamp}_{safe_name}_{safe_code}_{openid_suffix}_{report_id}.md"
    markdown_path = REPORTS_DIR / filename
    markdown_path.write_text(markdown_text, encoding="utf-8")

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO reports (
                report_id, openid, stock_name, stock_code, summary, markdown_path, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report_id,
                openid,
                stock_name,
                stock_code,
                summary,
                str(markdown_path),
                created_at,
            ),
        )
        conn.commit()

    return str(markdown_path)


def get_report(report_id: str) -> dict | None:
    init_db()

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT report_id, openid, stock_name, stock_code, summary, markdown_path, created_at
            FROM reports
            WHERE report_id = ?
            """,
            (report_id,),
        ).fetchone()

    if row is None:
        return None

    markdown_path = Path(row["markdown_path"])
    if not markdown_path.exists():
        return None

    return {
        "report_id": row["report_id"],
        "openid": row["openid"],
        "stock_name": row["stock_name"],
        "stock_code": row["stock_code"],
        "summary": row["summary"],
        "markdown_path": row["markdown_path"],
        "created_at": row["created_at"],
        "markdown_text": markdown_path.read_text(encoding="utf-8"),
    }
