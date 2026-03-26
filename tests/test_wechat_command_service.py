import unittest
import hashlib

from services.wechat_command_service import (
    MessageDeduplicator,
    build_text_reply,
    is_balance_query,
    is_top10_generate_command,
    is_top10_query,
    is_top100_query,
    is_top100_review_generate_command,
    is_top100_review_query,
    normalize_text_command,
    parse_kline_predict_command,
    split_text_content,
    verify_signature,
)


class WechatCommandServiceTests(unittest.TestCase):
    def test_verify_signature(self):
        token = "abc"
        items = [token, "123", "456"]
        items.sort()
        signature = hashlib.sha1("".join(items).encode("utf-8")).hexdigest()
        self.assertTrue(verify_signature(token, signature, "123", "456"))
        self.assertFalse(verify_signature(token, "bad", "123", "456"))

    def test_text_commands(self):
        self.assertEqual(normalize_text_command(" Top10 \n"), "top10")
        self.assertTrue(is_balance_query("token balance"))
        self.assertTrue(is_top10_query("top10"))
        self.assertTrue(is_top10_generate_command("生成top10"))
        self.assertTrue(is_top100_query("top100"))
        self.assertTrue(is_top100_review_query("top100-review"))
        self.assertTrue(is_top100_review_generate_command("更新top100复盘"))

    def test_text_commands_case_insensitive(self):
        self.assertTrue(is_top10_query("TOP10"))
        self.assertTrue(is_top10_query("Top10"))
        self.assertTrue(is_top100_query("TOP100"))

    def test_split_text_content(self):
        chunks = split_text_content("a" * 8 + "\n\n" + "b" * 8, max_chars=10)
        self.assertGreaterEqual(len(chunks), 2)
        self.assertTrue(all(len(chunk) <= 10 for chunk in chunks))

    def test_build_text_reply(self):
        reply = build_text_reply("user", "server", "hello", "1")
        self.assertIn("<ToUserName><![CDATA[user]]></ToUserName>", reply)
        self.assertIn("<Content><![CDATA[hello]]></Content>", reply)

    def test_message_deduplicator(self):
        deduper = MessageDeduplicator(window_seconds=1)
        self.assertFalse(deduper.is_duplicate("1", now_ts=10))
        self.assertTrue(deduper.is_duplicate("1", now_ts=10.5))
        self.assertFalse(deduper.is_duplicate("1", now_ts=12))

    def test_parse_kline_predict_command(self):
        self.assertEqual(parse_kline_predict_command("k线预测 600519"), "600519")
        self.assertEqual(parse_kline_predict_command("K线预测 贵州茅台"), "贵州茅台")
        self.assertEqual(parse_kline_predict_command("K线预测"), None)
        self.assertIsNone(parse_kline_predict_command("top10"))
        self.assertEqual(parse_kline_predict_command("贵州茅台k线预测"), "贵州茅台")

    def test_generate_command_not_confused_with_query(self):
        self.assertFalse(is_top10_query("生成top10"))
        self.assertTrue(is_top10_generate_command("生成top10"))


if __name__ == "__main__":
    unittest.main()
