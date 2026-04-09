"""持仓存储层 — SQLite。"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from pathlib import Path

from portfolio.models import Position

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent / "storage" / "portfolio.db"

_conn: sqlite3.Connection | None = None


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _conn.execute("PRAGMA journal_mode=WAL")
        _init_schema(_conn)
    return _conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            position_id  TEXT PRIMARY KEY,
            stock_code   TEXT NOT NULL,
            stock_name   TEXT NOT NULL,
            entry_price  REAL NOT NULL,
            entry_date   TEXT NOT NULL,
            shares       INTEGER NOT NULL,
            stop_loss    REAL NOT NULL DEFAULT 0,
            take_profit  REAL NOT NULL DEFAULT 0,
            thesis       TEXT NOT NULL DEFAULT '',
            report_id    TEXT NOT NULL DEFAULT '',
            status       TEXT NOT NULL DEFAULT 'open',
            close_date   TEXT NOT NULL DEFAULT '',
            close_price  REAL NOT NULL DEFAULT 0,
            close_reason TEXT NOT NULL DEFAULT '',
            created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.commit()


def add_position(pos: Position) -> str:
    """建仓。返回 position_id。"""
    conn = _get_conn()
    pid = pos.position_id or str(uuid.uuid4())[:8]
    conn.execute(
        "INSERT INTO positions "
        "(position_id, stock_code, stock_name, entry_price, entry_date, "
        "shares, stop_loss, take_profit, thesis, report_id, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')",
        (pid, pos.stock_code, pos.stock_name, pos.entry_price,
         pos.entry_date, pos.shares, pos.stop_loss, pos.take_profit,
         pos.thesis, pos.report_id),
    )
    conn.commit()
    logger.info("[portfolio] 建仓: %s %s %.2f × %d", pid, pos.stock_name, pos.entry_price, pos.shares)
    return pid


def close_position(position_id: str, close_price: float, close_date: str, reason: str = "") -> bool:
    """平仓。"""
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE positions SET status='closed', close_price=?, close_date=?, close_reason=? "
        "WHERE position_id=? AND status='open'",
        (close_price, close_date, reason, position_id),
    )
    conn.commit()
    return cur.rowcount > 0


def _query_rows(sql: str, params: tuple = ()) -> list[dict]:
    """线程安全查询：用独立 cursor 避免 row_factory 竞态。"""
    conn = _get_conn()
    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    try:
        rows = cur.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        cur.close()


def get_open_positions() -> list[dict]:
    """获取所有未平仓持仓。"""
    return _query_rows("SELECT * FROM positions WHERE status='open' ORDER BY entry_date DESC")


def get_all_positions(limit: int = 50) -> list[dict]:
    """获取全部持仓（含已平仓）。"""
    return _query_rows("SELECT * FROM positions ORDER BY created_at DESC LIMIT ?", (limit,))


def get_position(position_id: str) -> dict | None:
    """获取单个持仓。"""
    rows = _query_rows("SELECT * FROM positions WHERE position_id=?", (position_id,))
    return rows[0] if rows else None
