"""experience_db 单元测试"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


# ── helpers ──────────────────────────────────────────────────────────

def _make_entry(**kwargs) -> dict:
    base = {
        "date": "2026-04-01",
        "stock_code": "000001",
        "stock_name": "平安银行",
        "industry": "银行",
        "catalyst_type": ["财报超预期"],
        "pattern_tags": ["放量突破"],
        "prediction": {"score": 70, "direction": "做多", "target_pct": 10},
        "actual": {"return_5d": 2.0, "return_20d": 5.0, "max_drawdown": -3.0},
        "lesson": "测试教训",
        "tags": [],
    }
    base.update(kwargs)
    return base


# ══════════════════════════════════════════════════════════════════════
# Tests
# ══════════════════════════════════════════════════════════════════════

class TestAddExperience:
    def test_add_experience(self, tmp_path):
        """add one, verify it has ID starting with 'EXP-'"""
        from knowledge.experience_db import add_experience

        db_file = tmp_path / "exp.json"
        db_file.write_text("[]", encoding="utf-8")

        exp_id = add_experience(_make_entry(), db_path=db_file)
        assert exp_id.startswith("EXP-")

        stored = json.loads(db_file.read_text(encoding="utf-8"))
        assert len(stored) == 1
        assert stored[0]["id"] == exp_id


class TestRetrieveSameStock:
    def test_retrieve_same_stock(self, tmp_path):
        """add entry with stock_code '300750', retrieve for same code, verify lesson appears"""
        from knowledge.experience_db import add_experience, retrieve_lessons

        db_file = tmp_path / "exp.json"
        db_file.write_text("[]", encoding="utf-8")

        add_experience(_make_entry(
            stock_code="300750",
            stock_name="宁德时代",
            industry="锂电池",
            lesson="资金面与催化背离时高分不可信",
        ), db_path=db_file)

        result = retrieve_lessons(
            ts_code="300750",
            stock_name="宁德时代",
            db_path=db_file,
        )
        assert "资金面与催化背离时高分不可信" in result
        assert "⚠️" in result


class TestRetrieveSameIndustry:
    def test_retrieve_same_industry(self, tmp_path):
        """add entry with industry '锂电池', retrieve for different stock but same industry"""
        from knowledge.experience_db import add_experience, retrieve_lessons

        db_file = tmp_path / "exp.json"
        db_file.write_text("[]", encoding="utf-8")

        add_experience(_make_entry(
            stock_code="300750",
            stock_name="宁德时代",
            industry="锂电池",
            lesson="锂电行业拐点难判断",
        ), db_path=db_file)

        result = retrieve_lessons(
            ts_code="002594",  # 比亚迪，不同股票
            stock_name="比亚迪",
            current_industry="锂电池",
            db_path=db_file,
        )
        assert "锂电行业拐点难判断" in result
        assert "📌" in result  # different stock → 参考案例


class TestRetrieveEmptyDb:
    def test_retrieve_empty_db(self, tmp_path):
        """retrieve from empty DB returns empty string"""
        from knowledge.experience_db import retrieve_lessons

        db_file = tmp_path / "exp.json"
        db_file.write_text("[]", encoding="utf-8")

        result = retrieve_lessons(
            ts_code="300750",
            stock_name="宁德时代",
            db_path=db_file,
        )
        assert result == ""


class TestRetrieveTopK:
    def test_retrieve_top_k(self, tmp_path):
        """add 10 entries, retrieve with top_k=5, verify at most 5 lessons"""
        from knowledge.experience_db import add_experience, retrieve_lessons

        db_file = tmp_path / "exp.json"
        db_file.write_text("[]", encoding="utf-8")

        for i in range(10):
            add_experience(_make_entry(
                stock_code="300750",
                stock_name="宁德时代",
                industry="锂电池",
                lesson=f"教训{i}",
            ), db_path=db_file)

        result = retrieve_lessons(
            ts_code="300750",
            stock_name="宁德时代",
            current_industry="锂电池",
            top_k=5,
            db_path=db_file,
        )
        # Count lesson lines (lines starting with ⚠️ or 📌)
        lesson_lines = [ln for ln in result.splitlines() if ln.startswith("⚠️") or ln.startswith("📌")]
        assert len(lesson_lines) <= 5
        assert "共5条相关经验" in result
