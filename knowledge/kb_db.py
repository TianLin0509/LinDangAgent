# -*- coding: utf-8 -*-
"""统一数据库连接管理 — 线程安全的 SQLite 连接管理器

所有知识库模块共享此管理器，统一连接策略：
- 每个数据库一个长连接（WAL 模式，支持并发读写）
- threading.Lock 保护写操作
- contextmanager 接口确保 commit/rollback
- 统一 PRAGMA 设置
"""

import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)


class DBManager:
    """线程安全的 SQLite 连接管理器。

    每个已注册的数据库维护一个长连接和一个写锁。
    读操作不加锁（WAL 模式支持并发读），写操作通过 Lock 串行化。

    用法::

        db = DBManager()
        db.register("case_memory", Path("data/knowledge/case_memory.db"), schema_sql)

        # 读操作
        with db.read("case_memory") as conn:
            rows = conn.execute("SELECT ...").fetchall()

        # 写操作（自动 commit/rollback + 加锁）
        with db.write("case_memory") as conn:
            conn.execute("INSERT ...")
    """

    def __init__(self):
        self._conns: dict[str, sqlite3.Connection] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._schemas: dict[str, str] = {}
        self._db_paths: dict[str, Path] = {}
        self._init_lock = threading.Lock()

    def register(self, name: str, db_path: Path, schema_sql: str = "") -> None:
        """注册一个数据库（延迟初始化，首次使用时创建连接）。"""
        self._db_paths[name] = db_path
        self._schemas[name] = schema_sql
        self._locks[name] = threading.Lock()

    def _ensure_conn(self, name: str) -> sqlite3.Connection:
        """确保连接存在，首次调用时初始化。"""
        if name not in self._conns:
            with self._init_lock:
                if name not in self._conns:
                    if name not in self._db_paths:
                        raise KeyError(f"数据库 '{name}' 未注册，请先调用 register()")
                    db_path = self._db_paths[name]
                    db_path.parent.mkdir(parents=True, exist_ok=True)
                    conn = sqlite3.connect(
                        str(db_path),
                        timeout=10,
                        check_same_thread=False,
                    )
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("PRAGMA busy_timeout=5000")
                    conn.execute("PRAGMA foreign_keys=ON")
                    schema = self._schemas.get(name, "")
                    if schema:
                        conn.executescript(schema)
                    self._conns[name] = conn
                    logger.debug("[kb_db] 已初始化数据库连接: %s -> %s", name, db_path)
        return self._conns[name]

    @contextmanager
    def read(self, name: str) -> Iterator[sqlite3.Connection]:
        """获取只读连接（不加锁，WAL 模式支持并发读）。"""
        conn = self._ensure_conn(name)
        yield conn

    @contextmanager
    def write(self, name: str) -> Iterator[sqlite3.Connection]:
        """获取写连接（加锁 + 自动 commit/rollback）。"""
        conn = self._ensure_conn(name)
        lock = self._locks[name]
        with lock:
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def get_lock(self, name: str) -> threading.Lock:
        """获取指定数据库的写锁（供需要手动控制的场景）。"""
        if name not in self._locks:
            raise KeyError(f"数据库 '{name}' 未注册")
        return self._locks[name]

    def close(self, name: str | None = None) -> None:
        """关闭指定或所有数据库连接。"""
        if name:
            conn = self._conns.pop(name, None)
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
        else:
            for n in list(self._conns.keys()):
                self.close(n)

    def run_migration(self, name: str, migrations: list[tuple[str, str]]) -> None:
        """执行列迁移：检查列是否存在，不存在则 ALTER TABLE 添加。

        线程安全：使用写锁保护，避免并发初始化时 duplicate column 错误。

        Args:
            name: 数据库名称
            migrations: [(column_check_sql, alter_sql), ...]
                例如 [("SELECT source_type FROM intel_entries LIMIT 1",
                       "ALTER TABLE intel_entries ADD COLUMN source_type TEXT NOT NULL DEFAULT 'article'")]
        """
        conn = self._ensure_conn(name)
        lock = self._locks[name]
        with lock:
            for check_sql, alter_sql in migrations:
                try:
                    conn.execute(check_sql)
                except sqlite3.OperationalError:
                    try:
                        conn.execute(alter_sql)
                        conn.commit()
                        logger.info("[kb_db] 迁移完成: %s", alter_sql[:80])
                    except sqlite3.OperationalError:
                        pass  # 另一个线程已经完成迁移


# ── 全局单例 ──────────────────────────────────────────────────────

_manager: DBManager | None = None
_manager_lock = threading.Lock()


def get_manager() -> DBManager:
    """获取全局 DBManager 单例。"""
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = DBManager()
                _register_all_databases(_manager)
    return _manager


def _register_all_databases(mgr: DBManager) -> None:
    """注册所有知识库数据库。"""
    from knowledge.kb_config import (
        CASE_MEMORY_DB, INTEL_MEMORY_DB, KLINE_DIARY_DB,
        THESIS_JOURNAL_DB, WISDOM_DB, OUTCOMES_DB,
    )

    mgr.register("case_memory", CASE_MEMORY_DB, CASE_MEMORY_SCHEMA)
    mgr.register("intel_memory", INTEL_MEMORY_DB, INTEL_MEMORY_SCHEMA)
    mgr.register("kline_diary", KLINE_DIARY_DB, KLINE_DIARY_SCHEMA)
    mgr.register("thesis_journal", THESIS_JOURNAL_DB, THESIS_JOURNAL_SCHEMA)
    mgr.register("wisdom", WISDOM_DB, WISDOM_SCHEMA)
    mgr.register("outcomes", OUTCOMES_DB, OUTCOMES_SCHEMA)


# ── Schema 定义 ───────────────────────────────────────────────────

CASE_MEMORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY,
    report_date TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    stock_name TEXT NOT NULL,
    source TEXT DEFAULT 'report',
    regime TEXT,
    regime_label TEXT,
    score_fundamental REAL,
    score_expectation REAL,
    score_capital REAL,
    score_technical REAL,
    score_weighted REAL,
    direction TEXT,
    reasoning_summary TEXT,
    return_5d REAL,
    return_10d REAL,
    return_20d REAL,
    hit_10d INTEGER,
    outcome_type TEXT,
    lesson TEXT,
    lesson_generated_at TEXT,
    situation_summary TEXT,
    embedding BLOB,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS case_tags (
    case_id TEXT NOT NULL,
    tag_type TEXT NOT NULL,
    tag_value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cases_regime ON cases(regime);
CREATE INDEX IF NOT EXISTS idx_cases_report_date ON cases(report_date);
CREATE INDEX IF NOT EXISTS idx_cases_outcome_type ON cases(outcome_type);
CREATE INDEX IF NOT EXISTS idx_tags_type_value ON case_tags(tag_type, tag_value);
CREATE INDEX IF NOT EXISTS idx_tags_case ON case_tags(case_id);
"""

INTEL_MEMORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS intel_entries (
    entry_id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    analyzed_at TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    themes TEXT NOT NULL DEFAULT '[]',
    affected_sectors TEXT NOT NULL DEFAULT '[]',
    sentiment TEXT NOT NULL DEFAULT 'neutral',
    key_facts TEXT NOT NULL DEFAULT '[]',
    implications TEXT NOT NULL DEFAULT '',
    source_credibility TEXT NOT NULL DEFAULT 'medium',
    full_analysis TEXT NOT NULL DEFAULT '',
    source_type TEXT NOT NULL DEFAULT 'article',
    raw_text TEXT NOT NULL DEFAULT '',
    publish_time TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_intel_analyzed_at ON intel_entries(analyzed_at);

CREATE TABLE IF NOT EXISTS intel_themes (
    theme TEXT PRIMARY KEY,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    article_count INTEGER NOT NULL DEFAULT 1,
    sentiment_trend TEXT NOT NULL DEFAULT 'stable',
    related_sectors TEXT NOT NULL DEFAULT '[]'
);
"""

KLINE_DIARY_SCHEMA = """
CREATE TABLE IF NOT EXISTS kline_observations (
    obs_id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    stock_name TEXT NOT NULL,
    patterns TEXT NOT NULL DEFAULT '[]',
    regime TEXT NOT NULL DEFAULT 'shock',
    position TEXT NOT NULL DEFAULT '中部',
    volume_state TEXT NOT NULL DEFAULT '平量',
    prediction TEXT NOT NULL DEFAULT '',
    prediction_confidence REAL NOT NULL DEFAULT 0.5,
    actual_return_5d REAL,
    hit INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kline_obs_date ON kline_observations(date);
CREATE INDEX IF NOT EXISTS idx_kline_obs_stock ON kline_observations(stock_code);

CREATE TABLE IF NOT EXISTS kline_pattern_stats (
    pattern TEXT NOT NULL,
    regime TEXT NOT NULL,
    position TEXT NOT NULL,
    sample_count INTEGER NOT NULL DEFAULT 0,
    win_count INTEGER NOT NULL DEFAULT 0,
    win_rate_5d REAL NOT NULL DEFAULT 0,
    avg_return_5d REAL NOT NULL DEFAULT 0,
    last_updated TEXT NOT NULL,
    PRIMARY KEY (pattern, regime, position)
);

CREATE TABLE IF NOT EXISTS discovered_patterns (
    discovered_id TEXT PRIMARY KEY,
    combo_key TEXT NOT NULL UNIQUE,
    patterns TEXT NOT NULL DEFAULT '[]',
    regime TEXT NOT NULL DEFAULT '',
    position TEXT NOT NULL DEFAULT '',
    volume_state TEXT NOT NULL DEFAULT '',
    sample_count INTEGER NOT NULL DEFAULT 0,
    win_rate_5d REAL NOT NULL DEFAULT 0,
    avg_return_5d REAL NOT NULL DEFAULT 0,
    ai_name TEXT NOT NULL DEFAULT '',
    ai_explanation TEXT NOT NULL DEFAULT '',
    discovered_at TEXT NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0
);
"""

THESIS_JOURNAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS beliefs (
    belief_id TEXT PRIMARY KEY,
    category TEXT NOT NULL DEFAULT 'methodology',
    belief TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.5,
    evidence_count INTEGER NOT NULL DEFAULT 1,
    counter_evidence INTEGER NOT NULL DEFAULT 0,
    first_formed TEXT NOT NULL,
    last_updated TEXT NOT NULL,
    source_cases TEXT NOT NULL DEFAULT '[]',
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS belief_updates (
    update_id TEXT PRIMARY KEY,
    belief_id TEXT NOT NULL,
    update_date TEXT NOT NULL,
    old_confidence REAL,
    new_confidence REAL,
    reason TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (belief_id) REFERENCES beliefs(belief_id)
);
CREATE INDEX IF NOT EXISTS idx_updates_belief ON belief_updates(belief_id);
CREATE INDEX IF NOT EXISTS idx_updates_date ON belief_updates(update_date);
"""

WISDOM_SCHEMA = """
CREATE TABLE IF NOT EXISTS wisdom_entries (
    wisdom_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL DEFAULT 'book',
    source_name TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'general',
    wisdom TEXT NOT NULL,
    context TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    added_at TEXT NOT NULL,
    used_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_wisdom_category ON wisdom_entries(category);
"""

OUTCOMES_SCHEMA = """
CREATE TABLE IF NOT EXISTS outcomes (
    report_id TEXT PRIMARY KEY,
    report_date TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    stock_name TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'report',
    scores TEXT NOT NULL DEFAULT '{}',
    weighted_score REAL,
    direction TEXT,
    close_at_report REAL,
    return_5d REAL,
    return_10d REAL,
    return_20d REAL,
    hit_5d INTEGER,
    hit_10d INTEGER,
    hit_20d INTEGER,
    actual_trade_days INTEGER,
    evaluated_at TEXT,
    return_benchmark_10d REAL,
    beat_market_10d INTEGER,
    war_room_divergence REAL,
    war_room_generals TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_outcomes_date ON outcomes(report_date);
CREATE INDEX IF NOT EXISTS idx_outcomes_stock ON outcomes(stock_code);
CREATE INDEX IF NOT EXISTS idx_outcomes_direction ON outcomes(direction);
"""
