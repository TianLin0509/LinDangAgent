"""模型配置 + 全局常量 — API Key 从 Streamlit Secrets 或环境变量读取"""

from core.secrets_compat import _get_secret

# ── 全局常量 ──────────────────────────────────────────────────────────────
ADMIN_USERNAME = "LT"
CORE_KEYS = ["expectation", "trend", "fundamentals"]
DEEP_KEYS = ["sentiment", "sector", "holders"]
ALL_ANALYSIS_KEYS = CORE_KEYS + DEEP_KEYS

MODEL_CONFIGS = {
    "🟠 Qwen · 通义千问": {
        "api_key":        _get_secret("QWEN_API_KEY", ""),
        "base_url":       "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model":          "qwen-plus-latest",
        "supports_search": True,
        "provider":       "qwen",
        "note":           "Qwen Plus · 联网搜索已开启",
    },
    "🔵 智谱 · GLM-5": {
        "api_key":        _get_secret("ZHIPU_API_KEY", ""),
        "base_url":       "https://open.bigmodel.cn/api/paas/v4/",
        "model":          "glm-5",
        "supports_search": True,
        "provider":       "zhipu",
        "note":           "GLM-5 旗舰 · 联网搜索",
    },
    "🟣 豆包 · Seed 2.0 Pro": {
        "api_key":        _get_secret("DOUBAO_API_KEY", ""),
        "base_url":       "https://ark.cn-beijing.volces.com/api/v3",
        "model":          "doubao-seed-2-0-pro-260215",
        "supports_search": True,
        "provider":       "doubao",
        "note":           "Seed 2.0 Pro · 联网搜索（贵）",
    },
    "🟤 豆包 · Seed 2.0 Mini": {
        "api_key":        _get_secret("DOUBAO_API_KEY", ""),
        "base_url":       "https://ark.cn-beijing.volces.com/api/v3",
        "model":          "doubao-seed-2-0-mini-260215",
        "supports_search": True,
        "provider":       "doubao",
        "note":           "Seed 2.0 Mini · 联网搜索（省钱）",
    },
    "⚫ DeepSeek": {
        "api_key":        _get_secret("DEEPSEEK_API_KEY", ""),
        "base_url":       "https://api.deepseek.com",
        "model":          "deepseek-chat",
        "supports_search": False,
        "provider":       "deepseek",
        "note":           "DeepSeek-V3 · 仅内部知识",
    },
    "🟢 Gemini 2.5 Pro · Google": {
        "api_key":        _get_secret("OPENROUTER_API_KEY", ""),
        "base_url":       "https://openrouter.ai/api/v1",
        "model":          "google/gemini-2.5-pro",
        "supports_search": True,
        "provider":       "openrouter",
        "note":           "Gemini 2.5 Pro · 联网搜索（OpenRouter）",
    },
    "💚 Gemini 3 Pro · Google": {
        "api_key":        _get_secret("OPENROUTER_API_KEY", ""),
        "base_url":       "https://openrouter.ai/api/v1",
        "model":          "google/gemini-3-pro-preview",
        "supports_search": True,
        "provider":       "openrouter",
        "note":           "Gemini 3 Pro · 最新旗舰 · 联网搜索（OpenRouter）",
    },
    "🔷 GPT-5.2 · OpenAI": {
        "api_key":        _get_secret("OPENROUTER_API_KEY", ""),
        "base_url":       "https://openrouter.ai/api/v1",
        "model":          "openai/gpt-5.2",
        "supports_search": True,
        "provider":       "openrouter",
        "note":           "GPT-5.2 · 最新旗舰 · 联网搜索（OpenRouter）",
    },
    "🔹 GPT-4o · OpenAI": {
        "api_key":        _get_secret("OPENROUTER_API_KEY", ""),
        "base_url":       "https://openrouter.ai/api/v1",
        "model":          "openai/gpt-4o",
        "supports_search": True,
        "provider":       "openrouter",
        "note":           "GPT-4o · 经典稳定 · 联网搜索（OpenRouter）",
    },
}

MODEL_NAMES = list(MODEL_CONFIGS.keys())
DEFAULT_MODEL = "🟣 豆包 · Seed 2.0 Pro"
