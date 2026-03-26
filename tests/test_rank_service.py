import json
import tempfile
import unittest
from pathlib import Path

from services import rank_service


class RankServiceTests(unittest.TestCase):
    def test_snapshot_and_summary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir)
            result_path = cache_dir / "2026-03-27_test.json"
            status_path = cache_dir / "2026-03-27_deep_status.json"
            result_path.write_text(
                json.dumps(
                    {
                        "model": "demo-model",
                        "tokens_used": 123,
                        "summary": "demo summary",
                        "results": [
                            {
                                "stock_name": "A",
                                "ts_code": "000001",
                                "match_score": 88,
                                "report_url": "/report/12345678-1234-1234-1234-1234567890ab",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            status_path.write_text(
                json.dumps({"status": "done", "finished": "2026-03-27 10:00:00"}, ensure_ascii=False),
                encoding="utf-8",
            )

            snapshot = rank_service.get_latest_rank_snapshot(
                top10_cache_dir=cache_dir,
                base_url="https://example.com",
                limit=10,
            )

            self.assertIsNotNone(snapshot)
            assert snapshot is not None
            self.assertEqual(snapshot["actual_count"], 1)
            self.assertEqual(
                snapshot["rows"][0]["报告链接"],
                "https://example.com/report/12345678-1234-1234-1234-1234567890ab",
            )
            summary = rank_service.build_rank_summary_text(
                snapshot,
                label="Top10",
                path="/top10/latest",
                base_url="https://example.com",
            )
            self.assertIn("Top10", summary)
            self.assertIn("https://example.com/top10/latest", summary)

    def test_render_rank_html(self):
        snapshot = {
            "model": "demo",
            "finished": "2026-03-27 10:00:00",
            "actual_count": 1,
            "summary": "测试摘要",
            "rows": [
                {
                    "rank": 1,
                    "股票名称": "贵州茅台",
                    "代码": "600519.SH",
                    "综合匹配度": 9.5,
                    "报告链接": "https://example.com/report/abc",
                }
            ],
        }
        html = rank_service.render_rank_html(snapshot, title="Top10", heading="Top10 最新结果")
        self.assertIn("贵州茅台", html)
        self.assertIn("600519.SH", html)
        self.assertIn("查看报告", html)
        self.assertIn("测试摘要", html)

    def test_format_money_and_int(self):
        self.assertEqual(rank_service.format_money(1234.5), "1,234.50")
        self.assertEqual(rank_service.format_money(None), "N/A")
        self.assertEqual(rank_service.format_int(12345), "12,345")
        self.assertEqual(rank_service.format_int(None), "0")


if __name__ == "__main__":
    unittest.main()
