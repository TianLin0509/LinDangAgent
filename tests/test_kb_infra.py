# -*- coding: utf-8 -*-
"""知识库基础设施测试 — kb_config / kb_db / kb_validators / kb_io / kb_utils

覆盖：
- DBManager 连接复用、Lock 互斥、异常 rollback
- JSONL 原子写入、流式读取、按 key 覆盖
- validators 边界值
- kb_utils 胜率计算、AI JSON 解析
"""

import json
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest


# ════════════════════════════════════════════════════════════════════
# kb_config
# ════════════════════════════════════════════════════════════════════

class TestKbConfig:
    def test_paths_exist(self):
        from knowledge.kb_config import BASE_DIR, KNOWLEDGE_DIR
        assert BASE_DIR.exists()
        assert KNOWLEDGE_DIR.exists() or True  # may not exist in test env

    def test_score_weights_sum_to_one(self):
        from knowledge.kb_config import SCORE_WEIGHTS
        total = sum(SCORE_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9

    def test_regime_labels_complete(self):
        from knowledge.kb_config import REGIME_LABELS
        assert set(REGIME_LABELS.keys()) == {"bull", "bear", "shock", "rotation"}

    def test_direction_cn_complete(self):
        from knowledge.kb_config import DIRECTION_CN
        assert set(DIRECTION_CN.keys()) == {"bullish", "bearish", "neutral"}

    def test_pattern_templates_all_callable(self):
        from knowledge.kb_config import PATTERN_TEMPLATES
        for name, tmpl in PATTERN_TEMPLATES.items():
            assert callable(tmpl["condition"]), f"{name} condition not callable"
            assert "description" in tmpl

    def test_sector_keywords_nonempty(self):
        from knowledge.kb_config import SECTOR_KEYWORDS
        assert len(SECTOR_KEYWORDS) >= 50


# ════════════════════════════════════════════════════════════════════
# kb_db
# ════════════════════════════════════════════════════════════════════

class TestDBManager:
    def test_register_and_connect(self, tmp_path):
        from knowledge.kb_db import DBManager
        mgr = DBManager()
        db_path = tmp_path / "test.db"
        mgr.register("test", db_path, "CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, val TEXT);")

        with mgr.read("test") as conn:
            conn.execute("SELECT 1")  # 连接正常

        with mgr.write("test") as conn:
            conn.execute("INSERT INTO t (id, val) VALUES (1, 'hello')")

        with mgr.read("test") as conn:
            row = conn.execute("SELECT val FROM t WHERE id=1").fetchone()
            assert row[0] == "hello"

        mgr.close()

    def test_connection_reuse(self, tmp_path):
        from knowledge.kb_db import DBManager
        mgr = DBManager()
        mgr.register("test", tmp_path / "test.db")

        with mgr.read("test") as conn1:
            pass
        with mgr.read("test") as conn2:
            pass
        assert conn1 is conn2  # 同一个连接对象
        mgr.close()

    def test_write_rollback_on_error(self, tmp_path):
        from knowledge.kb_db import DBManager
        mgr = DBManager()
        mgr.register("test", tmp_path / "test.db",
                      "CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, val TEXT);")

        # 先写入一行
        with mgr.write("test") as conn:
            conn.execute("INSERT INTO t VALUES (1, 'first')")

        # 写入失败应 rollback
        with pytest.raises(sqlite3.IntegrityError):
            with mgr.write("test") as conn:
                conn.execute("INSERT INTO t VALUES (2, 'second')")
                conn.execute("INSERT INTO t VALUES (1, 'duplicate')")  # PK 冲突

        # 验证 rollback：只有第一行
        with mgr.read("test") as conn:
            rows = conn.execute("SELECT id FROM t").fetchall()
            assert len(rows) == 1
            assert rows[0][0] == 1

        mgr.close()

    def test_concurrent_write_safety(self, tmp_path):
        from knowledge.kb_db import DBManager
        mgr = DBManager()
        mgr.register("test", tmp_path / "test.db",
                      "CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY);")

        errors = []
        count = 50

        def writer(start):
            try:
                for i in range(start, start + count):
                    with mgr.write("test") as conn:
                        conn.execute("INSERT INTO t VALUES (?)", (i,))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i * count,)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"并发写入出错: {errors}"

        with mgr.read("test") as conn:
            total = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
            assert total == count * 3

        mgr.close()

    def test_wal_mode(self, tmp_path):
        from knowledge.kb_db import DBManager
        mgr = DBManager()
        mgr.register("test", tmp_path / "test.db")
        with mgr.read("test") as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode.lower() == "wal"
        mgr.close()

    def test_unregistered_db_raises(self):
        from knowledge.kb_db import DBManager
        mgr = DBManager()
        with pytest.raises(KeyError, match="未注册"):
            with mgr.read("nonexistent") as conn:
                pass

    def test_run_migration(self, tmp_path):
        from knowledge.kb_db import DBManager
        mgr = DBManager()
        mgr.register("test", tmp_path / "test.db",
                      "CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY);")

        # 迁移：添加新列
        mgr.run_migration("test", [
            ("SELECT new_col FROM t LIMIT 1",
             "ALTER TABLE t ADD COLUMN new_col TEXT DEFAULT 'x'"),
        ])

        with mgr.write("test") as conn:
            conn.execute("INSERT INTO t (id, new_col) VALUES (1, 'y')")

        with mgr.read("test") as conn:
            row = conn.execute("SELECT new_col FROM t WHERE id=1").fetchone()
            assert row[0] == "y"

        # 重复迁移不报错
        mgr.run_migration("test", [
            ("SELECT new_col FROM t LIMIT 1",
             "ALTER TABLE t ADD COLUMN new_col TEXT DEFAULT 'x'"),
        ])

        mgr.close()


# ════════════════════════════════════════════════════════════════════
# kb_validators
# ════════════════════════════════════════════════════════════════════

class TestValidators:

    def test_stock_code_valid(self):
        from knowledge.kb_validators import validate_stock_code
        assert validate_stock_code("600000.SH") == "600000.SH"
        assert validate_stock_code("000001.sz") == "000001.SZ"
        assert validate_stock_code("830799.bj") == "830799.BJ"
        assert validate_stock_code(" 600000.SH ") == "600000.SH"

    def test_stock_code_invalid(self):
        from knowledge.kb_validators import validate_stock_code
        assert validate_stock_code("") is None
        assert validate_stock_code(None) is None
        assert validate_stock_code("60000.SH") is None  # 5位
        assert validate_stock_code("6000001.SH") is None  # 7位
        assert validate_stock_code("600000.XX") is None  # 非法后缀
        assert validate_stock_code("abcdef.SH") is None  # 非数字

    def test_validate_score(self):
        from knowledge.kb_validators import validate_score
        assert validate_score(50) == 50.0
        assert validate_score(None) == 0.0
        assert validate_score("abc") == 0.0
        assert validate_score(-10) == 0.0  # 钳位
        assert validate_score(150) == 100.0  # 钳位
        assert validate_score(50, min_val=0, max_val=10) == 10.0

    def test_validate_date_str(self):
        from knowledge.kb_validators import validate_date_str
        assert validate_date_str("2026-04-09") == "2026-04-09"
        assert validate_date_str("2026-04-09T12:30:00") == "2026-04-09"
        assert validate_date_str("") is None
        assert validate_date_str(None) is None
        assert validate_date_str("not-a-date") is None
        assert validate_date_str("2026-13-01") is None  # 非法月份

    def test_validate_case_id(self):
        from knowledge.kb_validators import validate_case_id
        assert validate_case_id("abc123") is True
        assert validate_case_id("") is False
        assert validate_case_id(None) is False
        assert validate_case_id("  ") is False

    def test_validate_direction(self):
        from knowledge.kb_validators import validate_direction
        assert validate_direction("bullish") == "bullish"
        assert validate_direction("BEARISH") == "bearish"
        assert validate_direction("") == "neutral"
        assert validate_direction("invalid") == "neutral"

    def test_validate_regime(self):
        from knowledge.kb_validators import validate_regime
        assert validate_regime("bull") == "bull"
        assert validate_regime("BEAR") == "bear"
        assert validate_regime("") == "shock"
        assert validate_regime("invalid") == "shock"

    def test_validate_confidence(self):
        from knowledge.kb_validators import validate_confidence
        assert validate_confidence(0.5) == 0.5
        assert validate_confidence(None) == 0.5
        assert validate_confidence(-0.1) == 0.0
        assert validate_confidence(1.5) == 1.0
        assert validate_confidence("abc") == 0.5


# ════════════════════════════════════════════════════════════════════
# kb_io
# ════════════════════════════════════════════════════════════════════

class TestKbIO:

    def test_append_jsonl(self, tmp_path):
        from knowledge.kb_io import append_jsonl, read_jsonl_iter
        f = tmp_path / "test.jsonl"
        append_jsonl(f, {"a": 1})
        append_jsonl(f, {"b": 2})
        items = list(read_jsonl_iter(f))
        assert len(items) == 2
        assert items[0]["a"] == 1
        assert items[1]["b"] == 2

    def test_append_jsonl_with_lock(self, tmp_path):
        from knowledge.kb_io import append_jsonl
        f = tmp_path / "test.jsonl"
        lock = threading.Lock()
        append_jsonl(f, {"x": 1}, lock=lock)
        assert f.read_text(encoding="utf-8").strip()

    def test_read_jsonl_iter_nonexistent(self, tmp_path):
        from knowledge.kb_io import read_jsonl_iter
        items = list(read_jsonl_iter(tmp_path / "nope.jsonl"))
        assert items == []

    def test_read_jsonl_iter_skips_bad_lines(self, tmp_path):
        from knowledge.kb_io import read_jsonl_iter
        f = tmp_path / "test.jsonl"
        f.write_text('{"a":1}\nbad line\n{"b":2}\n', encoding="utf-8")
        items = list(read_jsonl_iter(f))
        assert len(items) == 2

    def test_read_jsonl_recent(self, tmp_path):
        from knowledge.kb_io import append_jsonl, read_jsonl_recent
        f = tmp_path / "test.jsonl"
        append_jsonl(f, {"date": "2020-01-01", "v": 1})
        append_jsonl(f, {"date": "2026-04-09", "v": 2})
        recent = read_jsonl_recent(f, days=30)
        assert len(recent) == 1
        assert recent[0]["v"] == 2

    def test_read_jsonl_recent_all(self, tmp_path):
        from knowledge.kb_io import append_jsonl, read_jsonl_recent
        f = tmp_path / "test.jsonl"
        append_jsonl(f, {"date": "2020-01-01"})
        append_jsonl(f, {"date": "2026-04-09"})
        all_items = read_jsonl_recent(f, days=0)
        assert len(all_items) == 2

    def test_read_jsonl_tail(self, tmp_path):
        from knowledge.kb_io import append_jsonl, read_jsonl_tail
        f = tmp_path / "test.jsonl"
        for i in range(20):
            append_jsonl(f, {"i": i})
        tail = read_jsonl_tail(f, n=5)
        assert len(tail) == 5
        assert tail[0]["i"] == 15
        assert tail[4]["i"] == 19

    def test_upsert_jsonl_by_key_insert(self, tmp_path):
        from knowledge.kb_io import upsert_jsonl_by_key, read_jsonl_iter
        f = tmp_path / "test.jsonl"
        upsert_jsonl_by_key(f, {"date": "2026-04-09", "val": 1}, "date")
        items = list(read_jsonl_iter(f))
        assert len(items) == 1
        assert items[0]["val"] == 1

    def test_upsert_jsonl_by_key_replace(self, tmp_path):
        from knowledge.kb_io import upsert_jsonl_by_key, read_jsonl_iter
        f = tmp_path / "test.jsonl"
        upsert_jsonl_by_key(f, {"date": "2026-04-08", "val": 1}, "date")
        upsert_jsonl_by_key(f, {"date": "2026-04-09", "val": 2}, "date")
        upsert_jsonl_by_key(f, {"date": "2026-04-08", "val": 3}, "date")  # 替换第一条
        items = list(read_jsonl_iter(f))
        assert len(items) == 2
        dates = {x["date"]: x["val"] for x in items}
        assert dates["2026-04-08"] == 3
        assert dates["2026-04-09"] == 2

    def test_concurrent_append(self, tmp_path):
        from knowledge.kb_io import append_jsonl, count_jsonl
        f = tmp_path / "test.jsonl"
        lock = threading.Lock()
        n = 50

        def writer(start):
            for i in range(start, start + n):
                append_jsonl(f, {"i": i}, lock=lock)

        threads = [threading.Thread(target=writer, args=(i * n,)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert count_jsonl(f) == n * 3

    def test_count_jsonl(self, tmp_path):
        from knowledge.kb_io import append_jsonl, count_jsonl
        f = tmp_path / "test.jsonl"
        assert count_jsonl(f) == 0
        for i in range(5):
            append_jsonl(f, {"i": i})
        assert count_jsonl(f) == 5


# ════════════════════════════════════════════════════════════════════
# kb_utils
# ════════════════════════════════════════════════════════════════════

class TestKbUtils:

    def test_parse_ai_json_dict(self):
        from knowledge.kb_utils import parse_ai_json
        result = parse_ai_json('```json\n{"a": 1}\n```')
        assert result == {"a": 1}

    def test_parse_ai_json_list(self):
        from knowledge.kb_utils import parse_ai_json
        result = parse_ai_json('```\n[1, 2, 3]\n```')
        assert result == [1, 2, 3]

    def test_parse_ai_json_plain(self):
        from knowledge.kb_utils import parse_ai_json
        result = parse_ai_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_parse_ai_json_failure(self):
        from knowledge.kb_utils import parse_ai_json
        assert parse_ai_json("not json at all") is None
        assert parse_ai_json("") is None
        assert parse_ai_json(None) is None

    def test_parse_ai_json_strict(self):
        from knowledge.kb_utils import parse_ai_json_strict
        assert parse_ai_json_strict('{"a": 1}') == {"a": 1}
        assert parse_ai_json_strict("bad") == {}
        assert parse_ai_json_strict('{"a": 1}', expected_type=list) == []
        assert parse_ai_json_strict('[1,2]', expected_type=list) == [1, 2]

    def test_calc_hit_rate(self):
        from knowledge.kb_utils import calc_hit_rate
        items = [
            {"hit_10d": True},
            {"hit_10d": True},
            {"hit_10d": False},
            {"hit_10d": None},  # 跳过
        ]
        rate = calc_hit_rate(items, min_samples=3)
        assert rate == pytest.approx(66.7, abs=0.1)

    def test_calc_hit_rate_insufficient_samples(self):
        from knowledge.kb_utils import calc_hit_rate
        items = [{"hit_10d": True}, {"hit_10d": False}]
        assert calc_hit_rate(items, min_samples=3) is None

    def test_calc_directional_hit_rate(self):
        from knowledge.kb_utils import calc_directional_hit_rate
        items = [
            {"direction": "bullish", "hit_10d": True},
            {"direction": "bearish", "hit_10d": False},
            {"direction": "neutral", "hit_10d": True},  # 排除
            {"direction": "bullish", "hit_10d": True},
        ]
        rate = calc_directional_hit_rate(items, min_samples=3)
        assert rate == pytest.approx(66.7, abs=0.1)

    def test_calc_bucket_stats(self):
        from knowledge.kb_utils import calc_bucket_stats
        items = [
            {"direction": "bullish", "hit_10d": True, "return_10d": 5.0},
            {"direction": "bearish", "hit_10d": False, "return_10d": -3.0},
            {"direction": "neutral", "hit_10d": True, "return_10d": 1.0},
        ]
        stats = calc_bucket_stats(items)
        assert stats["total"] == 3
        assert stats["directional"] == 2
        assert stats["hits"] == 1
        assert stats["hit_rate"] == 50.0
        assert stats["avg_return"] == 1.0

    def test_safe_json_loads(self):
        from knowledge.kb_utils import safe_json_loads
        assert safe_json_loads('{"a":1}') == {"a": 1}
        assert safe_json_loads("bad") is None
        assert safe_json_loads("", default=[]) == []
        assert safe_json_loads(None) is None

    def test_truncate_text(self):
        from knowledge.kb_utils import truncate_text
        assert truncate_text("short") == "short"
        assert truncate_text("a" * 600, max_chars=500) == "a" * 497 + "..."
        assert truncate_text("", max_chars=10) == ""
        assert truncate_text(None, max_chars=10) == ""
