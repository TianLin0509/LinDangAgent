"""Model configuration and global constants."""

import json
from pathlib import Path

from utils.app_config import get_secret

ARCHIVE_CUTOFF_HOUR = 19
CORE_KEYS = ["comprehensive"]
DEEP_KEYS = []
ALL_ANALYSIS_KEYS = CORE_KEYS

# ─── 默认模型持久化 ────────────────────────────────────────
_ACTIVE_MODEL_FILE = Path(__file__).resolve().parent / "storage" / "active_model.json"


def get_active_model() -> str:
    """读取当前激活的默认分析模型名。"""
    if _ACTIVE_MODEL_FILE.exists():
        try:
            return json.loads(_ACTIVE_MODEL_FILE.read_text("utf-8")).get("model", "")
        except Exception:
            pass
    return "🟡 豆包 · Seed 2.0 Lite"


def set_active_model(model_name: str) -> None:
    """持久化设置默认分析模型。"""
    _ACTIVE_MODEL_FILE.parent.mkdir(parents=True, exist_ok=True)
    _ACTIVE_MODEL_FILE.write_text(
        json.dumps({"model": model_name}, ensure_ascii=False), encoding="utf-8",
    )

MODEL_CONFIGS = {
    "🟠 Qwen · 通义千问": {
        "api_key": get_secret("QWEN_API_KEY", ""),
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus-latest",
        "supports_search": True,
        "provider": "qwen",
        "note": "Qwen Plus · 联网搜索已开启",
    },
    "🔵 智谱 · GLM-5": {
        "api_key": get_secret("ZHIPU_API_KEY", ""),
        "base_url": "https://open.bigmodel.cn/api/paas/v4/",
        "model": "glm-5",
        "supports_search": True,
        "provider": "zhipu",
        "note": "GLM-5 旗舰 · 联网搜索",
    },
    "🟣 豆包 · Seed 2.0 Pro": {
        "api_key": get_secret("DOUBAO_API_KEY", ""),
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-seed-2-0-pro-260215",
        "supports_search": True,
        "provider": "doubao",
        "note": "Seed 2.0 Pro · 联网搜索（贵）",
    },
    "🟤 豆包 · Seed 2.0 Mini": {
        "api_key": get_secret("DOUBAO_API_KEY", ""),
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-seed-2-0-mini-260215",
        "supports_search": True,
        "provider": "doubao",
        "note": "Seed 2.0 Mini · 联网搜索（省钱）",
    },
    "🟡 豆包 · Seed 2.0 Lite": {
        "api_key": get_secret("DOUBAO_API_KEY", ""),
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-seed-2-0-lite-260215",
        "supports_search": True,
        "provider": "doubao",
        "note": "Seed 2.0 Lite · 联网搜索（最省钱）",
    },
    "⚫ DeepSeek": {
        "api_key": get_secret("DEEPSEEK_API_KEY", ""),
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "supports_search": False,
        "provider": "deepseek",
        "note": "DeepSeek-V3 · 仅内部知识",
    },
    "🟢 Gemini 2.5 Pro · Google": {
        "api_key": get_secret("OPENROUTER_API_KEY", ""),
        "base_url": "https://openrouter.ai/api/v1",
        "model": "google/gemini-2.5-pro",
        "supports_search": True,
        "provider": "openrouter",
        "note": "Gemini 2.5 Pro · 联网搜索（OpenRouter）",
    },
    "💚 Gemini 3 Pro · Google": {
        "api_key": get_secret("OPENROUTER_API_KEY", ""),
        "base_url": "https://openrouter.ai/api/v1",
        "model": "google/gemini-3-pro-preview",
        "supports_search": True,
        "provider": "openrouter",
        "note": "Gemini 3 Pro · 最新旗舰 · 联网搜索（OpenRouter）",
    },
    "🔷 GPT-5.2 · OpenAI": {
        "api_key": get_secret("OPENROUTER_API_KEY", ""),
        "base_url": "https://openrouter.ai/api/v1",
        "model": "openai/gpt-5.2",
        "supports_search": True,
        "provider": "openrouter",
        "note": "GPT-5.2 · 最新旗舰 · 联网搜索（OpenRouter）",
    },
    "🔹 GPT-4o · OpenAI": {
        "api_key": get_secret("OPENROUTER_API_KEY", ""),
        "base_url": "https://openrouter.ai/api/v1",
        "model": "openai/gpt-4o",
        "supports_search": True,
        "provider": "openrouter",
        "note": "GPT-4o · 经典稳定 · 联网搜索（OpenRouter）",
    },
    # ─── 免费 CLI 调用 ─────────────────────────────────────
    "🔮 Gemini CLI（免费）": {
        "api_key": "cli",
        "base_url": "",
        "model": "gemini-cli",
        "supports_search": True,
        "provider": "gemini_cli",
        "note": "Gemini CLI · 完全免费 · Google OAuth · 内置联网搜索",
    },
    "🤖 Codex CLI（Plus）": {
        "api_key": "cli",
        "base_url": "",
        "model": "codex-cli",
        "supports_search": True,
        "provider": "codex_cli",
        "note": "Codex CLI (GPT-5.4) · ChatGPT Plus · 不额外计费 · 内置工具",
    },
    # ─── Claude Code CLI ──────────────────────────────────
    "🧠 Claude Opus（MAX）": {
        "api_key": "cli",
        "base_url": "",
        "model": "opus",
        "supports_search": False,
        "provider": "claude_cli",
        "note": "Claude Opus 4.6 · MAX订阅 · 最强推理 · 无联网但推理最深",
    },
    "⚡ Claude Sonnet（MAX）": {
        "api_key": "cli",
        "base_url": "",
        "model": "sonnet",
        "supports_search": False,
        "provider": "claude_cli",
        "note": "Claude Sonnet 4.6 · MAX订阅 · 速度与质量平衡",
    },
}

MODEL_NAMES = list(MODEL_CONFIGS.keys())
