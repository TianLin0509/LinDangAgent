"""Secrets compatibility helpers."""

from __future__ import annotations

import os
from pathlib import Path

import tomllib


_SECRETS_CACHE: dict[str, object] | None = None


def _candidate_secret_paths() -> list[Path]:
    repo_root = Path(__file__).resolve().parent.parent
    return [
        repo_root / ".streamlit" / "secrets.toml",
        repo_root.parent / "app" / ".streamlit" / "secrets.toml",
        repo_root.parent / "Stock_lite" / ".streamlit" / "secrets.toml",
    ]


def _load_local_secrets() -> dict[str, object]:
    global _SECRETS_CACHE
    if _SECRETS_CACHE is not None:
        return _SECRETS_CACHE

    for path in _candidate_secret_paths():
        if not path.exists():
            continue
        try:
            _SECRETS_CACHE = tomllib.loads(path.read_text(encoding="utf-8"))
            return _SECRETS_CACHE
        except Exception:
            continue

    _SECRETS_CACHE = {}
    return _SECRETS_CACHE


def _get_secret(key: str, default: str = "") -> str:
    env_value = os.environ.get(key)
    if env_value:
        return env_value

    try:
        import streamlit as st

        value = st.secrets.get(key, "")
        if value:
            return value
    except Exception:
        pass

    return str(_load_local_secrets().get(key, default))
