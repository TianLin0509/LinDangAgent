"""Stock_top10 配置 — 复用主项目 MODEL_CONFIGS，仅保留 Top10 专属常量。"""

from config import MODEL_CONFIGS, MODEL_NAMES, get_active_model, set_active_model  # noqa: F401

# ── Top10 专属常量 ──────────────────────────────────────────────
ADMIN_USERNAME = "LT"
CORE_KEYS = ["expectation", "trend", "fundamentals"]
DEEP_KEYS = ["sentiment", "sector", "holders"]
ALL_ANALYSIS_KEYS = CORE_KEYS + DEEP_KEYS

DEFAULT_MODEL = "🟣 豆包 · Seed 2.0 Pro"
