"""Application configuration helpers."""

from __future__ import annotations

import os
from pathlib import Path

import tomllib


_SECRETS_CACHE: dict[str, object] | None = None


def _load_local_secrets() -> dict[str, object]:
    global _SECRETS_CACHE
    if _SECRETS_CACHE is not None:
        return _SECRETS_CACHE

    secrets_path = Path(__file__).resolve().parent.parent / ".streamlit" / "secrets.toml"
    if not secrets_path.exists():
        _SECRETS_CACHE = {}
        return _SECRETS_CACHE

    try:
        _SECRETS_CACHE = tomllib.loads(secrets_path.read_text(encoding="utf-8"))
    except Exception:
        _SECRETS_CACHE = {}
    return _SECRETS_CACHE


def get_secret(key: str, default: str = "") -> str:
    """Read config from env first, then Streamlit secrets if available."""
    env_value = os.getenv(key)
    if env_value:
        return env_value

    try:
        import streamlit as st

        return st.secrets.get(key, default)
    except Exception:
        return _load_local_secrets().get(key, default)
