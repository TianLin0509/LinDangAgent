from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from hashlib import sha1
from threading import Lock
from xml.etree import ElementTree as ET

from data.tushare_client import resolve_stock


INVALID_STOCK_WORDS = {
    "hello",
    "hi",
    "test",
    "你好",
    "在吗",
    "谢谢",
    "您好",
}

BALANCE_COMMANDS = {
    "token余额",
    "token balance",
    "余额",
    "豆包余额",
    "查询余额",
}
TOP10_QUERY_COMMANDS = {"top10", "查看top10"}
TOP100_QUERY_COMMANDS = {"top100", "查看top100"}
TOP100_REVIEW_QUERY_COMMANDS = {
    "top100review",
    "top100-review",
    "top100复盘",
    "查看top100复盘",
}
TOP100_REVIEW_GENERATE_COMMANDS = {
    "生成top100复盘",
    "更新top100复盘",
    "刷新top100复盘",
}
TOP10_GENERATE_COMMANDS = {
    "生成top10",
    "刷新top10",
    "更新top10",
}
KLINE_PREDICT_PREFIXES = (
    "k线预测",
    "K线预测",
    "预测k线",
)


def verify_signature(token: str, signature: str, timestamp: str, nonce: str) -> bool:
    items = [token, timestamp, nonce]
    items.sort()
    digest = sha1("".join(items).encode("utf-8")).hexdigest()
    return digest == signature


def xml_text(root: ET.Element, tag: str, default: str = "") -> str:
    node = root.find(tag)
    return node.text if node is not None and node.text is not None else default


def is_valid_stock_input(content: str) -> bool:
    value = (content or "").strip()
    if len(value) < 2 or len(value) > 12:
        return False
    if value.lower() in INVALID_STOCK_WORDS:
        return False
    if re.fullmatch(r"[A-Za-z]+", value):
        return False
    return True


def normalize_text_command(content: str) -> str:
    return re.sub(r"\s+", "", (content or "").strip().lower())


def is_balance_query(content: str) -> bool:
    raw = (content or "").strip()
    normalized = normalize_text_command(raw)
    if normalized in {item.lower().replace(" ", "") for item in BALANCE_COMMANDS}:
        return True
    return "token" in normalized and "balance" in normalized


def is_top10_generate_command(content: str) -> bool:
    normalized = normalize_text_command(content).replace("-", "")
    return normalized in {item.replace("-", "") for item in TOP10_GENERATE_COMMANDS}


def is_top10_query(content: str) -> bool:
    normalized = normalize_text_command(content).replace("-", "")
    return normalized in {item.replace("-", "") for item in TOP10_QUERY_COMMANDS} and not is_top10_generate_command(content)


def is_top100_query(content: str) -> bool:
    normalized = normalize_text_command(content).replace("-", "")
    return normalized in {item.replace("-", "") for item in TOP100_QUERY_COMMANDS}


def is_top100_review_query(content: str) -> bool:
    normalized = normalize_text_command(content).replace("-", "")
    return normalized in {item.replace("-", "") for item in TOP100_REVIEW_QUERY_COMMANDS}


def is_top100_review_generate_command(content: str) -> bool:
    normalized = normalize_text_command(content).replace("-", "")
    return normalized in {item.replace("-", "") for item in TOP100_REVIEW_GENERATE_COMMANDS}


def parse_kline_predict_command(content: str) -> str | None:
    raw = (content or "").strip()
    normalized = normalize_text_command(raw)
    for prefix in KLINE_PREDICT_PREFIXES:
        compact_prefix = normalize_text_command(prefix)
        if normalized.startswith(compact_prefix):
            stock_text = raw[len(prefix):].strip(" ：:;；，,")
            return stock_text or None

    natural_patterns = [
        r"^(?P<stock>.+?)\s*k线预测$",
        r"^(?P<stock>.+?)\s*预测k线$",
        r"^帮我预测\s*(?P<stock>.+?)\s*k线$",
        r"^分析\s*(?P<stock>.+?)\s*k线$",
    ]
    for pattern in natural_patterns:
        match = re.match(pattern, raw, flags=re.IGNORECASE)
        if match:
            stock_text = (match.group("stock") or "").strip(" ：:;；，,")
            return stock_text or None
    return None


def build_text_reply(to_user: str, from_user: str, content: str, timestamp: str) -> str:
    return f"""<xml>
<ToUserName><![CDATA[{to_user}]]></ToUserName>
<FromUserName><![CDATA[{from_user}]]></FromUserName>
<CreateTime>{timestamp}</CreateTime>
<MsgType><![CDATA[text]]></MsgType>
<Content><![CDATA[{content}]]></Content>
</xml>"""


def split_text_content(content: str, max_chars: int) -> list[str]:
    if len(content) <= max_chars:
        return [content]

    chunks: list[str] = []
    current = ""
    for block in content.split("\n\n"):
        candidate = block if not current else current + "\n\n" + block
        if len(candidate) <= max_chars:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = ""

        while len(block) > max_chars:
            chunks.append(block[:max_chars])
            block = block[max_chars:]
        current = block

    if current:
        chunks.append(current)

    return chunks


def precheck_stock_input(content: str) -> tuple[bool, str | None]:
    raw = (content or "").strip()
    if not raw:
        return False, "请输入股票名称或代码，例如“贵州茅台”或“600519”。"

    ts_code, _, resolve_warn = resolve_stock(raw)
    if ts_code:
        return True, None

    if resolve_warn:
        return False, f"暂时无法识别“{raw}”对应的股票，请检查输入是否准确。"
    return False, f"没有找到“{raw}”对应的股票，请换一个名称或代码再试。"


@dataclass
class MessageDeduplicator:
    window_seconds: int = 600
    _items: dict[str, float] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def is_duplicate(self, message_id: str, now_ts: float | None = None) -> bool:
        if not message_id:
            return False
        seen_at = now_ts if now_ts is not None else time.time()

        with self._lock:
            expired = [
                key for key, timestamp in self._items.items()
                if seen_at - timestamp > self.window_seconds
            ]
            for key in expired:
                self._items.pop(key, None)

            if message_id in self._items:
                return True

            self._items[message_id] = seen_at
            return False
