import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import main


def _sign(token: str, timestamp: str, nonce: str) -> str:
    items = [token, timestamp, nonce]
    items.sort()
    return hashlib.sha1("".join(items).encode("utf-8")).hexdigest()


class MainRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self.signature = _sign(main.TOKEN, "1", "2")

    def test_wechat_verify(self):
        response = self.client.get(
            "/wechat",
            params={"signature": self.signature, "timestamp": "1", "nonce": "2", "echostr": "ok"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "ok")

    def test_wechat_post_dispatch(self):
        xml = """<xml>
<ToUserName><![CDATA[gh_123]]></ToUserName>
<FromUserName><![CDATA[user_1]]></FromUserName>
<CreateTime>1</CreateTime>
<MsgType><![CDATA[text]]></MsgType>
<Content><![CDATA[top10]]></Content>
<MsgId>100</MsgId>
</xml>"""
        with patch.object(
            main.wechat_dispatch_service,
            "dispatch_text_message",
            return_value=main.wechat_dispatch_service.DispatchResult("hello"),
        ):
            response = self.client.post(
                "/wechat",
                params={"signature": self.signature, "timestamp": "1", "nonce": "2"},
                content=xml.encode("utf-8"),
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn("hello", response.text)

    def test_report_route(self):
        with patch.object(main, "load_report", return_value={"markdown_text": "# Demo"}):
            response = self.client.get("/report/demo-id")
        self.assertEqual(response.status_code, 200)
        self.assertIn("LinDangAgent AI Report", response.text)

    def test_top10_route_with_temp_cache(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir)
            (cache_dir / "result.json").write_text(
                '{"model":"demo","tokens_used":1,"summary":"ok","results":[{"stock_name":"A","ts_code":"000001","match_score":80}]}',
                encoding="utf-8",
            )
            with patch.object(main, "TOP10_CACHE_DIR", cache_dir):
                response = self.client.get("/top10/latest")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Top10", response.text)


if __name__ == "__main__":
    unittest.main()
