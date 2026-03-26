"""Realtime token or credit balance queries for supported AI providers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests

from ai.client import get_token_usage
from config import MODEL_CONFIGS
from utils.app_config import get_secret

_TIMEOUT_SECONDS = 15

_PROVIDER_META = {
    "qwen": {
        "label": "Qwen / DashScope",
        "dashboard_url": "https://bailian.console.aliyun.com/",
        "supports_realtime_balance_api": True,
        "message": "Realtime account balance is available via Alibaba Cloud BSS OpenAPI with AK/SK.",
    },
    "zhipu": {
        "label": "Zhipu",
        "dashboard_url": "https://open.bigmodel.cn/financial-overview",
        "supports_realtime_balance_api": False,
        "message": "No public official balance API was found for Zhipu; console remains the supported path.",
    },
    "doubao": {
        "label": "Doubao / Volcengine Ark",
        "dashboard_url": "https://console.volcengine.com/finance/account",
        "supports_realtime_balance_api": True,
        "message": "Realtime account balance is available via Volcengine billing OpenAPI with AK/SK.",
    },
    "deepseek": {
        "label": "DeepSeek",
        "dashboard_url": "https://platform.deepseek.com/top_up",
        "supports_realtime_balance_api": True,
        "message": "Realtime balance is fetched from DeepSeek official API.",
    },
    "openrouter": {
        "label": "OpenRouter",
        "dashboard_url": "https://openrouter.ai/settings/credits",
        "supports_realtime_balance_api": True,
        "message": "Realtime balance is fetched from OpenRouter official APIs.",
    },
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def _base_provider_payload(provider: str, api_key: str) -> dict[str, Any]:
    meta = _PROVIDER_META.get(
        provider,
        {
            "label": provider,
            "dashboard_url": "",
            "supports_realtime_balance_api": False,
            "message": "Unknown provider.",
        },
    )
    return {
        "provider": provider,
        "provider_label": meta["label"],
        "api_key_configured": bool(api_key),
        "supports_realtime_balance_api": meta["supports_realtime_balance_api"],
        "dashboard_url": meta["dashboard_url"],
        "message": meta["message"],
        "status": "not_configured" if not api_key else "unsupported",
    }


def _fetch_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
    response = requests.get(url, headers=headers, timeout=_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def _fetch_openrouter_balance(api_key: str) -> dict[str, Any]:
    payload = _base_provider_payload("openrouter", api_key)
    if not api_key:
        return payload

    try:
        credits_data = _fetch_json(
            "https://openrouter.ai/api/v1/credits",
            {"Authorization": f"Bearer {api_key}"},
        ).get("data", {})
        key_data = _fetch_json(
            "https://openrouter.ai/api/v1/key",
            {"Authorization": f"Bearer {api_key}"},
        ).get("data", {})
    except requests.HTTPError as exc:
        body = exc.response.text[:300] if exc.response is not None else ""
        payload.update({"status": "error", "message": f"OpenRouter balance query failed: {exc}. {body}".strip()})
        return payload
    except Exception as exc:
        payload.update({"status": "error", "message": f"OpenRouter balance query failed: {exc}"})
        return payload

    payload.update(
        {
            "status": "ok",
            "message": "Realtime balance fetched successfully.",
            "account": {
                "total_credits": credits_data.get("total_credits"),
                "total_usage": credits_data.get("total_usage"),
            },
            "api_key": {
                "label": key_data.get("label"),
                "limit": key_data.get("limit"),
                "limit_remaining": key_data.get("limit_remaining"),
                "limit_reset": key_data.get("limit_reset"),
                "usage": key_data.get("usage"),
                "usage_daily": key_data.get("usage_daily"),
                "usage_weekly": key_data.get("usage_weekly"),
                "usage_monthly": key_data.get("usage_monthly"),
                "is_free_tier": key_data.get("is_free_tier"),
            },
        }
    )
    return payload


def _fetch_deepseek_balance(api_key: str) -> dict[str, Any]:
    payload = _base_provider_payload("deepseek", api_key)
    if not api_key:
        return payload

    try:
        data = _fetch_json(
            "https://api.deepseek.com/user/balance",
            {"Authorization": f"Bearer {api_key}"},
        )
    except requests.HTTPError as exc:
        body = exc.response.text[:300] if exc.response is not None else ""
        payload.update({"status": "error", "message": f"DeepSeek balance query failed: {exc}. {body}".strip()})
        return payload
    except Exception as exc:
        payload.update({"status": "error", "message": f"DeepSeek balance query failed: {exc}"})
        return payload

    balances = data.get("balance_infos") or []
    total_available = sum(_safe_float(item.get("total_balance")) or 0.0 for item in balances)
    total_granted = sum(_safe_float(item.get("granted_balance")) or 0.0 for item in balances)
    total_topped_up = sum(_safe_float(item.get("topped_up_balance")) or 0.0 for item in balances)

    payload.update(
        {
            "status": "ok",
            "message": "Realtime balance fetched successfully.",
            "account": {
                "is_available": data.get("is_available"),
                "currency": balances[0].get("currency") if balances else None,
                "total_balance": total_available,
                "granted_balance": total_granted,
                "topped_up_balance": total_topped_up,
                "balance_infos": balances,
            },
        }
    )
    return payload


def _get_aliyun_aksk() -> tuple[str, str]:
    access_key_id = get_secret("ALIBABA_CLOUD_ACCESS_KEY_ID") or get_secret("ALIYUN_ACCESS_KEY_ID")
    access_key_secret = get_secret("ALIBABA_CLOUD_ACCESS_KEY_SECRET") or get_secret("ALIYUN_ACCESS_KEY_SECRET")
    return access_key_id, access_key_secret


def _fetch_qwen_balance(api_key: str) -> dict[str, Any]:
    payload = _base_provider_payload("qwen", api_key)
    access_key_id, access_key_secret = _get_aliyun_aksk()
    payload["cloud_credentials_configured"] = bool(access_key_id and access_key_secret)

    if not access_key_id or not access_key_secret:
        payload.update(
            {
                "status": "credential_required",
                "message": (
                    "Alibaba Cloud balance query needs cloud AK/SK. "
                    "Set ALIBABA_CLOUD_ACCESS_KEY_ID and ALIBABA_CLOUD_ACCESS_KEY_SECRET."
                ),
            }
        )
        return payload

    try:
        from alibabacloud_bssopenapi20171214.client import Client as BssClient
        from alibabacloud_tea_openapi import models as open_api_models

        config = open_api_models.Config(
            access_key_id=access_key_id,
            access_key_secret=access_key_secret,
            endpoint="business.aliyuncs.com",
        )
        client = BssClient(config)
        response = client.query_account_balance()
        body = response.body
        data = body.data if body else None
    except Exception as exc:
        payload.update({"status": "error", "message": f"Alibaba Cloud balance query failed: {exc}"})
        return payload

    payload.update(
        {
            "status": "ok",
            "message": "Realtime balance fetched successfully.",
            "account": {
                "currency": getattr(data, "currency", None),
                "available_amount": _safe_float(getattr(data, "available_amount", None)),
                "available_cash_amount": _safe_float(getattr(data, "available_cash_amount", None)),
                "credit_amount": _safe_float(getattr(data, "credit_amount", None)),
                "quota_limit": _safe_float(getattr(data, "quota_limit", None)),
            },
        }
    )
    return payload


def _get_volc_aksk() -> tuple[str, str]:
    access_key_id = (
        get_secret("VOLC_ACCESSKEY")
        or get_secret("VOLCENGINE_ACCESS_KEY_ID")
        or get_secret("ARK_ACCESS_KEY_ID")
    )
    access_key_secret = (
        get_secret("VOLC_SECRETKEY")
        or get_secret("VOLCENGINE_ACCESS_KEY_SECRET")
        or get_secret("ARK_ACCESS_KEY_SECRET")
    )
    return access_key_id, access_key_secret


def _fetch_doubao_balance(api_key: str) -> dict[str, Any]:
    payload = _base_provider_payload("doubao", api_key)
    access_key_id, access_key_secret = _get_volc_aksk()
    payload["cloud_credentials_configured"] = bool(access_key_id and access_key_secret)

    if not access_key_id or not access_key_secret:
        payload.update(
            {
                "status": "credential_required",
                "message": (
                    "Volcengine balance query needs cloud AK/SK. "
                    "Set VOLC_ACCESSKEY and VOLC_SECRETKEY."
                ),
            }
        )
        return payload

    try:
        from volcengine.ApiInfo import ApiInfo
        from volcengine.Credentials import Credentials
        from volcengine.ServiceInfo import ServiceInfo
        from volcengine.base.Service import Service

        service_info = ServiceInfo(
            "billing.volcengineapi.com",
            {"Accept": "application/json"},
            Credentials(access_key_id, access_key_secret, "billing", "cn-north-1"),
            5,
            5,
        )
        api_info = {
            "QueryBalanceAcct": ApiInfo(
                "POST",
                "/",
                {"Action": "QueryBalanceAcct", "Version": "2022-01-01"},
                {},
                {},
            )
        }
        client = Service(service_info, api_info)
        result = client.post("QueryBalanceAcct", {}, {})
        data = requests.models.complexjson.loads(result)
    except Exception as exc:
        payload.update({"status": "error", "message": f"Volcengine balance query failed: {exc}"})
        return payload

    response_metadata = data.get("ResponseMetadata", {})
    result_data = data.get("Result", {})
    if response_metadata.get("Error"):
        payload.update(
            {
                "status": "error",
                "message": f"Volcengine balance query failed: {response_metadata['Error']}",
            }
        )
        return payload

    payload.update(
        {
            "status": "ok",
            "message": "Realtime balance fetched successfully.",
            "account": {
                "available_balance": _safe_float(result_data.get("AvailableBalance")),
                "available_balance_available": _safe_float(result_data.get("AvailableBalanceAvailable")),
                "available_balance_unavailable": _safe_float(result_data.get("AvailableBalanceUnavailable")),
                "credit_balance": _safe_float(result_data.get("CreditBalance")),
                "currency": result_data.get("Currency"),
            },
        }
    )
    return payload


def _fetch_zhipu_balance(api_key: str) -> dict[str, Any]:
    payload = _base_provider_payload("zhipu", api_key)
    if not api_key:
        return payload
    payload.update(
        {
            "status": "unsupported",
            "message": (
                "Zhipu official docs expose finance console pages, but no public balance API "
                "was found to safely integrate here."
            ),
        }
    )
    return payload


def get_provider_balance(provider: str, api_key: str) -> dict[str, Any]:
    if provider == "openrouter":
        return _fetch_openrouter_balance(api_key)
    if provider == "deepseek":
        return _fetch_deepseek_balance(api_key)
    if provider == "qwen":
        return _fetch_qwen_balance(api_key)
    if provider == "doubao":
        return _fetch_doubao_balance(api_key)
    if provider == "zhipu":
        return _fetch_zhipu_balance(api_key)
    return _base_provider_payload(provider, api_key)


def get_token_balance_snapshot(model_name: str | None = None) -> dict[str, Any]:
    if model_name:
        cfg = MODEL_CONFIGS.get(model_name)
        if not cfg:
            return {
                "generated_at": _utc_now_iso(),
                "model_name": model_name,
                "error": "Unknown model name.",
                "providers": [],
                "local_token_usage": get_token_usage(),
            }
        providers_to_query = [
            {
                "provider": cfg.get("provider", "unknown"),
                "api_key": cfg.get("api_key", ""),
                "model_name": model_name,
                "model_id": cfg.get("model", ""),
            }
        ]
    else:
        seen: set[tuple[str, str]] = set()
        providers_to_query = []
        for current_model_name, cfg in MODEL_CONFIGS.items():
            provider = cfg.get("provider", "unknown")
            api_key = cfg.get("api_key", "")
            dedupe_key = (provider, api_key)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            providers_to_query.append(
                {
                    "provider": provider,
                    "api_key": api_key,
                    "model_name": current_model_name,
                    "model_id": cfg.get("model", ""),
                }
            )

    providers: list[dict[str, Any]] = []
    for item in providers_to_query:
        provider_payload = get_provider_balance(item["provider"], item["api_key"])
        provider_payload["example_model_name"] = item["model_name"]
        provider_payload["example_model_id"] = item["model_id"]
        providers.append(provider_payload)

    return {
        "generated_at": _utc_now_iso(),
        "model_name": model_name,
        "providers": providers,
        "local_token_usage": get_token_usage(),
        "notes": [
            "local_token_usage is tracked inside this app.",
            "Some providers expose account credits or cash balance rather than token quota.",
            "Qwen and Doubao realtime balance require cloud account AK/SK, not only model API keys.",
        ],
    }
