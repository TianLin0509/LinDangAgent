import unittest

from services.prebuilt_kline_service import ensure_research_dataset


class KlineSmokeTests(unittest.TestCase):
    def test_research_dataset_available(self):
        result = ensure_research_dataset()
        self.assertTrue(result["dataset_ready"])
        self.assertIn("metadata", result)


if __name__ == "__main__":
    unittest.main()
