"""Application configuration helpers."""

from __future__ import annotations

import os
from pathlib import Path

import tomllib


_SECRETS_CACHE: dict[str, object] | None = None

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

_SECRETS_CANDIDATES = [
    _PROJECT_ROOT / "secrets.toml",
    _PROJECT_ROOT / ".streamlit" / "secrets.toml",
]


def _load_local_secrets() -> dict[str, object]:
    global _SECRETS_CACHE
    if _SECRETS_CACHE is not None:
        return _SECRETS_CACHE

    for path in _SECRETS_CANDIDATES:
        if path.exists():
            try:
                _SECRETS_CACHE = tomllib.loads(path.read_text(encoding="utf-8"))
                return _SECRETS_CACHE
            except Exception:
                continue

    _SECRETS_CACHE = {}
    return _SECRETS_CACHE


def get_secret(key: str, default: str = "") -> str:
    """Read config from env first, then local secrets.toml."""
    env_value = os.getenv(key)
    if env_value:
        return env_value

    return _load_local_secrets().get(key, default)
