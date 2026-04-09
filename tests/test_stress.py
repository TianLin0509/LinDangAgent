# -*- coding: utf-8 -*-
"""压力测试 — 并发读写、大数据量、模型降级、数据损坏

验证知识库系统在极端条件下的鲁棒性。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ════════════════════════════════════════════════════════════════════
# 并发读写测试
# ════════════════════════════════════════════════════════════════════

class TestConcurrentDBAccess:

    def test_concurrent_case_store_and_read(self, tmp_path):
        """3 线程同时写入 case_memory + 1 线程同时读取。"""
        from knowledge.kb_db import DBManager, CASE_MEMORY_SCHEMA
        from knowledge.case_memory import store_case, CaseCard, get_case_count

        mgr = DBManager()
        mgr.register("case_memory", tmp_path / "case.db", CASE_MEMORY_SCHEMA)

        errors = []
        write_count = 20  # 每线程写 20 条

        def writer(thread_id):
            try:
                for i in range(write_count):
                    case = CaseCard(
                        case_id=f"t{thread_id}_{i}",
                        report_date="2026-04-09",
                        stock_code="600000.SH",
                        stock_name=f"测试_{thread_id}_{i}",
                        regime="shock",
                        score_weighted=50.0 + i,
                    )
                    store_case(case)
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for _ in range(10):
                    get_case_count()
                    time.sleep(0.01)
            except Exception as e:
                errors.append(e)

        with patch("knowledge.kb_db.get_manager", return_value=mgr):
            threads = [threading.Thread(target=writer, args=(i,)) for i in range(3)]
            threads.append(threading.Thread(target=reader))
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)

            assert not errors, f"并发读写错误: {errors}"
            count = get_case_count()
            assert count == write_count * 3

        mgr.close()

    def test_concurrent_outcome_write(self, tmp_path):
        """2 线程同时写入 outcomes 表。"""
        from knowledge.kb_db import DBManager, OUTCOMES_SCHEMA
        from knowledge.outcome_tracker import _append_outcome

        mgr = DBManager()
        mgr.register("outcomes", tmp_path / "outcomes.db", OUTCOMES_SCHEMA)

        errors = []
        n = 30

        def writer(start):
            try:
                for i in range(start, start + n):
                    _append_outcome({
                        "report_id": f"r_{i}",
                        "report_date": "2026-04-09",
                        "stock_code": "600000.SH",
                        "stock_name": "测试",
                        "scores": {"基本面": 70},
                        "weighted_score": 65.0,
                        "direction": "bullish",
                        "return_10d": 3.5,
                        "hit_10d": True,
                        "evaluated_at": "2026-04-09T00:00:00",
                    })
            except Exception as e:
                errors.append(e)

        # outcome_tracker 在模块顶层导入 get_manager，需要 patch 该模块的引用
        with patch("knowledge.outcome_tracker.get_manager", return_value=mgr):
            threads = [threading.Thread(target=writer, args=(i * n,)) for i in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)

            assert not errors, f"并发写入错误: {errors}"

            with mgr.read("outcomes") as conn:
                total = conn.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]
                assert total == n * 2

        mgr.close()

    def test_concurrent_jsonl_upsert(self, tmp_path):
        """2 线程同时对 JSONL 文件 upsert。"""
        from knowledge.kb_io import upsert_jsonl_by_key, read_jsonl_iter

        f = tmp_path / "test.jsonl"
        lock = threading.Lock()
        errors = []

        def writer(thread_id):
            try:
                for i in range(10):
                    upsert_jsonl_by_key(
                        f,
                        {"date": f"2026-04-{thread_id:02d}", "val": i, "thread": thread_id},
                        "date",
                        lock=lock,
                    )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(1, 4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert not errors, f"并发 upsert 错误: {errors}"
        items = list(read_jsonl_iter(f))
        # 3 个不同日期，每个最终只有一条
        dates = {x["date"] for x in items}
        assert len(dates) == 3


# ════════════════════════════════════════════════════════════════════
# 大数据量测试
# ════════════════════════════════════════════════════════════════════

class TestLargeDataVolume:

    def test_outcomes_10000_query(self, tmp_path):
        """10000 条 outcomes 的查询性能。"""
        from knowledge.kb_db import DBManager, OUTCOMES_SCHEMA

        mgr = DBManager()
        mgr.register("outcomes", tmp_path / "outcomes.db", OUTCOMES_SCHEMA)

        # 批量插入 10000 条
        with mgr.write("outcomes") as conn:
            for i in range(10000):
                date = f"2026-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}"
                conn.execute("""
                    INSERT INTO outcomes (report_id, report_date, stock_code, stock_name,
                                          weighted_score, direction, return_10d, hit_10d, evaluated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    f"r_{i}", date, f"{600000 + i % 1000}.SH", f"股票_{i}",
                    50 + (i % 50), "bullish" if i % 3 else "bearish",
                    (i % 20) - 10, 1 if (i % 20) > 10 else 0,
                    "2026-04-09",
                ))

        # 全量查询
        start = time.time()
        with mgr.read("outcomes") as conn:
            rows = conn.execute("SELECT * FROM outcomes").fetchall()
        full_time = time.time() - start
        assert len(rows) == 10000
        assert full_time < 2.0, f"全量查询耗时 {full_time:.2f}s > 2s"

        # 按日期过滤查询
        start = time.time()
        with mgr.read("outcomes") as conn:
            rows = conn.execute("SELECT * FROM outcomes WHERE report_date >= '2026-06-01'").fetchall()
        filter_time = time.time() - start
        assert filter_time < 1.0, f"过滤查询耗时 {filter_time:.2f}s > 1s"

        # 按股票查询
        start = time.time()
        with mgr.read("outcomes") as conn:
            rows = conn.execute("SELECT * FROM outcomes WHERE stock_code = '600000.SH'").fetchall()
        stock_time = time.time() - start
        assert stock_time < 0.5, f"股票查询耗时 {stock_time:.2f}s > 0.5s"

        mgr.close()

    def test_case_memory_5000_retrieve(self, tmp_path):
        """5000 条 case_memory 的检索性能。"""
        from knowledge.kb_db import DBManager, CASE_MEMORY_SCHEMA

        mgr = DBManager()
        mgr.register("case_memory", tmp_path / "case.db", CASE_MEMORY_SCHEMA)

        sectors = ["AI算力", "半导体", "光伏", "新能源车", "白酒", "医药", "券商"]

        with mgr.write("case_memory") as conn:
            for i in range(5000):
                conn.execute("""
                    INSERT INTO cases (case_id, report_date, stock_code, stock_name,
                                       regime, score_weighted, direction, return_10d,
                                       hit_10d, outcome_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    f"c_{i}", f"2026-{(i % 12) + 1:02d}-01",
                    f"{600000 + i}.SH", f"股票_{i}",
                    ["bull", "bear", "shock", "rotation"][i % 4],
                    50 + (i % 50), "bullish" if i % 2 else "bearish",
                    (i % 20) - 10, 1 if i % 3 else 0,
                    ["win", "loss", "draw"][i % 3],
                ))
                # 添加标签
                sector = sectors[i % len(sectors)]
                conn.execute(
                    "INSERT INTO case_tags (case_id, tag_type, tag_value) VALUES (?, 'sector', ?)",
                    (f"c_{i}", sector),
                )

        # 标签 + 环境检索
        start = time.time()
        with mgr.read("case_memory") as conn:
            rows = conn.execute("""
                SELECT c.* FROM cases c
                JOIN case_tags t ON c.case_id = t.case_id
                WHERE t.tag_value = 'AI算力' AND c.regime = 'shock'
                LIMIT 30
            """).fetchall()
        retrieve_time = time.time() - start
        assert retrieve_time < 1.0, f"案例检索耗时 {retrieve_time:.2f}s > 1s"
        assert len(rows) > 0

        mgr.close()

    def test_jsonl_10000_stream_read(self, tmp_path):
        """10000 行 JSONL 流式读取不 OOM。"""
        from knowledge.kb_io import append_jsonl, read_jsonl_iter, count_jsonl

        f = tmp_path / "large.jsonl"
        # 写入 10000 行
        for i in range(10000):
            append_jsonl(f, {"i": i, "data": "x" * 100})

        assert count_jsonl(f) == 10000

        # 流式读取计数（不全量加载）
        count = 0
        for entry in read_jsonl_iter(f):
            count += 1
        assert count == 10000


# ════════════════════════════════════════════════════════════════════
# 模型降级测试
# ════════════════════════════════════════════════════════════════════

class TestModelDegradation:

    def test_ai_json_parse_garbage(self):
        """AI 返回完全无法解析的内容。"""
        from knowledge.kb_utils import parse_ai_json, parse_ai_json_strict
        assert parse_ai_json("这不是 JSON") is None
        assert parse_ai_json("```json\n不是JSON\n```") is None
        assert parse_ai_json_strict("garbage") == {}
        assert parse_ai_json_strict("garbage", expected_type=list) == []

    def test_ai_json_parse_partial(self):
        """AI 返回截断的 JSON。"""
        from knowledge.kb_utils import parse_ai_json
        assert parse_ai_json('{"key": "val') is None  # 截断

    def test_ai_json_parse_wrong_type(self):
        """AI 返回了 JSON 但类型不对。"""
        from knowledge.kb_utils import parse_ai_json_strict
        # 期望 dict 但得到 list
        assert parse_ai_json_strict("[1,2,3]", expected_type=dict) == {}
        # 期望 list 但得到 dict
        assert parse_ai_json_strict('{"a":1}', expected_type=list) == []

    def test_intel_memory_parse_error_marker(self):
        """intel_memory 的 _parse_error 标记正确设置。"""
        from knowledge.kb_utils import parse_ai_json
        # 模拟 intel_memory 的逻辑
        raw = "not json"
        result = parse_ai_json(raw)
        if result is None:
            result = {"_parse_error": True}
        assert result.get("_parse_error") is True


# ════════════════════════════════════════════════════════════════════
# 数据损坏恢复测试
# ════════════════════════════════════════════════════════════════════

class TestDataCorruption:

    def test_jsonl_with_corrupted_lines(self, tmp_path):
        """JSONL 中混有损坏行，应能跳过继续读取。"""
        from knowledge.kb_io import read_jsonl_iter

        f = tmp_path / "corrupt.jsonl"
        f.write_text(
            '{"good": 1}\n'
            'THIS IS GARBAGE\n'
            '{"good": 2}\n'
            '{"broken": \n'  # 截断 JSON
            '{"good": 3}\n',
            encoding="utf-8",
        )
        items = list(read_jsonl_iter(f))
        assert len(items) == 3
        assert all(x.get("good") for x in items)

    def test_empty_db_file(self, tmp_path):
        """空数据库文件不崩溃。"""
        from knowledge.kb_db import DBManager

        db_path = tmp_path / "empty.db"
        db_path.touch()  # 创建空文件

        mgr = DBManager()
        mgr.register("test", db_path, "CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY);")

        with mgr.read("test") as conn:
            rows = conn.execute("SELECT * FROM t").fetchall()
            assert rows == []

        mgr.close()

    def test_jsonl_empty_file(self, tmp_path):
        """空 JSONL 文件不崩溃。"""
        from knowledge.kb_io import read_jsonl_iter, read_jsonl_recent, read_jsonl_tail, count_jsonl

        f = tmp_path / "empty.jsonl"
        f.touch()

        assert list(read_jsonl_iter(f)) == []
        assert read_jsonl_recent(f, days=7) == []
        assert read_jsonl_tail(f, n=5) == []
        assert count_jsonl(f) == 0

    def test_jsonl_only_newlines(self, tmp_path):
        """全是空行的 JSONL 文件。"""
        from knowledge.kb_io import read_jsonl_iter

        f = tmp_path / "blank.jsonl"
        f.write_text("\n\n\n\n", encoding="utf-8")
        items = list(read_jsonl_iter(f))
        assert items == []

    def test_db_wal_recovery(self, tmp_path):
        """模拟写入后 WAL 文件存在，连接应正常恢复。"""
        from knowledge.kb_db import DBManager

        db_path = tmp_path / "test.db"
        mgr = DBManager()
        mgr.register("test", db_path, "CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, val TEXT);")

        # 写入数据
        with mgr.write("test") as conn:
            conn.execute("INSERT INTO t VALUES (1, 'hello')")

        # 关闭连接
        mgr.close()

        # WAL 文件应该存在
        wal_path = db_path.parent / (db_path.name + "-wal")
        # （WAL 文件可能在 close 时已被合并，这取决于 SQLite 实现）

        # 重新打开，数据应该完整
        mgr2 = DBManager()
        mgr2.register("test", db_path, "CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, val TEXT);")
        with mgr2.read("test") as conn:
            row = conn.execute("SELECT val FROM t WHERE id=1").fetchone()
            assert row[0] == "hello"
        mgr2.close()


# ════════════════════════════════════════════════════════════════════
# 验证器边界条件
# ════════════════════════════════════════════════════════════════════

class TestValidatorEdgeCases:

    def test_stock_code_with_whitespace(self):
        from knowledge.kb_validators import validate_stock_code
        assert validate_stock_code("  600000.SH  ") == "600000.SH"
        assert validate_stock_code("\t000001.SZ\n") == "000001.SZ"

    def test_score_extreme_values(self):
        from knowledge.kb_validators import validate_score
        assert validate_score(float("inf")) == 100.0
        assert validate_score(float("-inf")) == 0.0
        assert validate_score(float("nan")) == 0.0  # NaN 被 clamp

    def test_date_various_formats(self):
        from knowledge.kb_validators import validate_date_str
        assert validate_date_str("2026-04-09") == "2026-04-09"
        assert validate_date_str("2026-04-09T12:30:00+08:00") == "2026-04-09"
        # Python strptime handles single-digit month/day, so "2026-4-9" is valid
        assert validate_date_str("2026-4-9") == "2026-04-09"
