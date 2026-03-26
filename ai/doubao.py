"""豆包 Responses API — 联网搜索专属调用逻辑"""

import json
import requests


def _build_request(cfg, messages, max_tokens, stream=False):
    """构建豆包 responses API 请求参数"""
    url = cfg["base_url"].rstrip("/") + "/responses"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {cfg['api_key']}",
    }
    body = {
        "model": cfg["model"],
        "input": messages,
        "tools": [{"type": "web_search", "max_keyword": 3}],
        "stream": stream,
        "max_output_tokens": max_tokens,
    }
    return url, headers, body


def _extract_text(data: dict) -> str:
    """从豆包 responses API 返回值中提取纯文本"""
    parts = []
    for item in data.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    parts.append(c.get("text", ""))
        if "text" in item:
            parts.append(item["text"])
    if parts:
        return "\n".join(parts)
    if "output_text" in data:
        return data["output_text"]
    return ""


def doubao_call(cfg, messages, max_tokens, _retry=0) -> tuple[str, str | None]:
    """豆包非流式 responses API 调用（含自动重试）"""
    import time as _time
    url, headers, body = _build_request(cfg, messages, max_tokens, stream=False)
    try:
        resp = requests.post(url, headers=headers, json=body, timeout=180)
        resp.encoding = "utf-8"
        if resp.status_code != 200:
            # 429/503 → 重试一次
            if resp.status_code in (429, 503) and _retry < 2:
                _time.sleep(2 + _retry * 2)
                return doubao_call(cfg, messages, max_tokens, _retry=_retry + 1)
            return "", f"豆包 API 错误 {resp.status_code}：{resp.text[:200]}"
        data = resp.json()
        text = _extract_text(data)
        if not text and _retry < 2:
            # 空响应 → 重试（豆包并发时偶发）
            _time.sleep(1 + _retry)
            return doubao_call(cfg, messages, max_tokens, _retry=_retry + 1)
        return text or "", ("豆包返回空内容（已重试）" if not text else None)
    except requests.exceptions.Timeout:
        return "", "豆包 API 请求超时，请稍后重试"
    except requests.exceptions.ConnectionError as e:
        if _retry < 2:
            _time.sleep(2)
            return doubao_call(cfg, messages, max_tokens, _retry=_retry + 1)
        return "", f"豆包连接失败：{str(e)[:120]}"
    except Exception as e:
        return "", f"豆包调用异常：{str(e)[:120]}"


def doubao_stream(cfg, messages, max_tokens):
    """豆包流式 responses API 调用，yield 文本片段"""
    url, headers, body = _build_request(cfg, messages, max_tokens, stream=True)
    try:
        resp = requests.post(url, headers=headers, json=body, timeout=180, stream=True)
        if resp.status_code != 200:
            resp.encoding = "utf-8"
            yield f"\n\n⚠️ 豆包 API 错误 {resp.status_code}：{resp.text[:150]}"
            return

        resp.encoding = "utf-8"

        for line in resp.iter_lines(decode_unicode=True):
            if not line:
                continue
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if raw == "[DONE]":
                break
            try:
                evt = json.loads(raw)
                evt_type = evt.get("type", "")
                if evt_type == "response.output_text.delta":
                    delta = evt.get("delta", "")
                    if delta:
                        yield delta
                elif evt_type == "response.content_part.delta":
                    delta = evt.get("delta", {})
                    if isinstance(delta, dict):
                        text = delta.get("text", "")
                        if text:
                            yield text
                    elif isinstance(delta, str) and delta:
                        yield delta
            except json.JSONDecodeError:
                continue

    except requests.exceptions.Timeout:
        yield "\n\n⚠️ 豆包 API 请求超时，请稍后重试"
    except Exception as e:
        yield f"\n\n⚠️ 豆包调用异常：{str(e)[:120]}"
