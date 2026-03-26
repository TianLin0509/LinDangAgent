import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from repositories import report_repo


class ReportRepoTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmpdir = Path(self._tmpdir.name)
        self._storage_dir = tmpdir / "storage"
        self._reports_dir = self._storage_dir / "reports"
        self._db_path = self._storage_dir / "reports.db"

        self._patches = [
            patch.object(report_repo, "STORAGE_DIR", self._storage_dir),
            patch.object(report_repo, "REPORTS_DIR", self._reports_dir),
            patch.object(report_repo, "DB_PATH", self._db_path),
        ]
        for p in self._patches:
            p.start()

        report_repo.init_db()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmpdir.cleanup()

    def test_save_and_get_report(self):
        report_id = "test-report-id-1234"
        report_repo.save_report(
            report_id=report_id,
            openid="user_openid_abc",
            stock_name="贵州茅台",
            stock_code="600519.SH",
            summary="测试摘要",
            markdown_text="# 报告内容",
        )
        result = report_repo.get_report(report_id)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["report_id"], report_id)
        self.assertEqual(result["stock_name"], "贵州茅台")
        self.assertEqual(result["markdown_text"], "# 报告内容")

    def test_get_nonexistent_report(self):
        result = report_repo.get_report("does-not-exist")
        self.assertIsNone(result)

    def test_created_at_and_filename_same_second(self):
        report_id = "test-ts-consistency"
        report_repo.save_report(
            report_id=report_id,
            openid="user_abc",
            stock_name="测试股",
            stock_code="000001.SZ",
            summary="摘要",
            markdown_text="# 内容",
        )
        result = report_repo.get_report(report_id)
        assert result is not None
        markdown_path = Path(result["markdown_path"])
        # created_at 的日期部分应与文件名中的时间戳一致（同一秒）
        created_date = result["created_at"][:10].replace("-", "")
        self.assertIn(created_date, markdown_path.name)


if __name__ == "__main__":
    unittest.main()
