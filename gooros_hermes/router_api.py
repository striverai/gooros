from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Iterable
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .constants import GOOROS_9ROUTER_API_KEY_NAME, GOOROS_9ROUTER_COMBO_NAME

ROUTER_BASE_URL = os.environ.get("GOOROS_9ROUTER_BASE_URL", "http://127.0.0.1:20128").rstrip("/")
FREE_PROVIDER_HINT_IDS = {"kiro", "gemini-cli", "opencode", "opencode-free", "mimo-free"}
FREE_TEXT_HINTS = ("free", "noauth", "no-auth", "opencode free", "mimo free")
PRICING_FIELDS = ("input", "output", "cached", "reasoning", "cache_creation")


@dataclass(frozen=True)
class FreeModelDiscovery:
    models: list[str]
    warnings: list[str]


def router_json(path: str, *, method: str = "GET", body: object | None = None, timeout: int = 10) -> object:
    path = path if path.startswith("/") else f"/{path}"
    payload = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Accept": "application/json", "User-Agent": "gooros-hermes-installer"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(f"{ROUTER_BASE_URL}{path}", data=payload, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"9Router API {method} {path} failed ({exc.code}): {detail or exc.reason}") from exc
    except Exception as exc:
        raise RuntimeError(f"9Router API {method} {path} failed: {exc}") from exc
    return json.loads(raw) if raw.strip() else {}


def _object_list(payload: object, *keys: str) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def list_router_models() -> list[dict]:
    return _object_list(router_json("/v1/models"), "data", "models", "items")


def list_router_providers() -> list[dict]:
    return _object_list(router_json("/api/providers"), "connections", "providers", "items")


def list_router_provider_models(connection_id: str) -> list[dict]:
    return _object_list(router_json(f"/api/providers/{quote(connection_id, safe='')}/models"), "models", "data", "items")


def list_router_pricing() -> dict:
    data = router_json("/api/pricing")
    return data if isinstance(data, dict) else {}


def list_router_keys() -> list[dict]:
    return _object_list(router_json("/api/keys"), "keys", "data", "items")


def list_router_combos() -> list[dict]:
    return _object_list(router_json("/api/combos"), "combos", "data", "items")


def get_router_settings() -> dict:
    data = router_json("/api/settings")
    return data if isinstance(data, dict) else {}


def ensure_router_api_key(name: str = GOOROS_9ROUTER_API_KEY_NAME, *, preferred_key: str = "") -> dict:
    keys = list_router_keys()
    if preferred_key:
        for item in keys:
            if item.get("key") == preferred_key and item.get("isActive", True) is not False:
                return item
    for item in keys:
        if item.get("name") == name and item.get("key") and item.get("isActive", True) is not False:
            return item
    created = router_json("/api/keys", method="POST", body={"name": name})
    if not isinstance(created, dict) or not created.get("key"):
        raise RuntimeError("9Router did not return a usable API key")
    return created


def ensure_router_combo(name: str, models: list[str], *, kind: str = "llm") -> dict:
    if not models:
        raise RuntimeError("cannot create 9Router combo without free models")
    body = {"name": name, "models": models, "kind": kind}
    existing = next((item for item in list_router_combos() if item.get("name") == name and item.get("id")), None)
    if existing:
        updated = router_json(f"/api/combos/{quote(str(existing['id']), safe='')}", method="PUT", body=body)
        return updated if isinstance(updated, dict) else {}
    created = router_json("/api/combos", method="POST", body=body)
    return created if isinstance(created, dict) else {}


def ensure_router_round_robin(*, sticky_limit: int = 1) -> dict:
    updated = router_json(
        "/api/settings",
        method="PATCH",
        body={"comboStrategy": "round-robin", "comboStickyRoundRobinLimit": sticky_limit},
    )
    return updated if isinstance(updated, dict) else {}


def _to_text(*values: object) -> str:
    parts: list[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            parts.append(json.dumps(value, ensure_ascii=False, sort_keys=True))
        else:
            parts.append(str(value))
    return " ".join(parts).lower()


def _provider_free_hint(connection: dict) -> bool:
    provider = str(connection.get("provider") or "").strip().lower()
    if provider in FREE_PROVIDER_HINT_IDS:
        return True
    data = connection.get("providerSpecificData") if isinstance(connection.get("providerSpecificData"), dict) else {}
    if data.get("noAuth") is True or data.get("isFree") is True or data.get("freeTier") is True:
        return True
    text = _to_text(provider, connection.get("name"), connection.get("authType"), data)
    return any(hint in text for hint in FREE_TEXT_HINTS)


def _model_id(model: dict) -> str:
    value = model.get("id") or model.get("model") or model.get("upstreamModelId") or model.get("name")
    return str(value).strip() if value else ""


def _model_is_combo(model: dict, model_id: str) -> bool:
    owned_by = str(model.get("owned_by") or model.get("ownedBy") or "").strip().lower()
    if owned_by == "combo":
        return True
    return model_id == GOOROS_9ROUTER_COMBO_NAME


def _pricing_entry(pricing: dict, provider_id: str, model_id: str) -> object | None:
    if not isinstance(pricing, dict):
        return None
    provider_keys = [provider_id, provider_id.lower(), provider_id.replace("-", "_"), provider_id.replace("_", "-")]
    model_keys = [model_id, model_id.lower()]
    for provider_key in provider_keys:
        provider_data = pricing.get(provider_key)
        if not isinstance(provider_data, dict):
            continue
        for model_key in model_keys:
            if model_key in provider_data:
                return provider_data[model_key]
    return None


def _zero_priced(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    found = False
    for field in PRICING_FIELDS:
        if field not in entry:
            continue
        found = True
        try:
            if float(entry[field] or 0) != 0:
                return False
        except (TypeError, ValueError):
            return False
    return found


def _model_free_hint(model: dict, model_id: str) -> bool:
    if model.get("free") is True or model.get("isFree") is True:
        return True
    try:
        if float(model.get("rateMultiplier")) == 0:
            return True
    except (TypeError, ValueError):
        pass
    return "free" in _to_text(model_id, model.get("name"), model.get("displayName"))


def rank_router_models(models: Iterable[str]) -> list[str]:
    unique = {str(model).strip() for model in models if model is not None and str(model).strip()}
    return sorted(
        unique,
        key=lambda model: (
            0 if "deepseek" in model.lower() else 1,
            0 if "free" in model.lower() else 1,
            model.lower(),
        ),
    )


def choose_router_model(models: list[str]) -> str:
    ranked = rank_router_models(models)
    if not ranked:
        raise RuntimeError("9Router returned no free models; connect a free provider in the 9Router dashboard, then rerun update")
    return ranked[0]


def discover_free_router_models() -> FreeModelDiscovery:
    warnings: list[str] = []
    free_models: list[str] = []
    try:
        providers = list_router_providers()
    except RuntimeError as exc:
        providers = []
        warnings.append(str(exc))
    try:
        pricing = list_router_pricing()
    except RuntimeError as exc:
        pricing = {}
        warnings.append(str(exc))

    for connection in providers:
        connection_id = str(connection.get("id") or "").strip()
        provider_id = str(connection.get("provider") or "").strip()
        if not connection_id:
            continue
        try:
            provider_models = list_router_provider_models(connection_id)
        except RuntimeError as exc:
            warnings.append(f"{connection_id}: {exc}")
            continue
        provider_hint = _provider_free_hint(connection)
        for model in provider_models:
            model_id = _model_id(model)
            if not model_id or _model_is_combo(model, model_id):
                continue
            pricing_entry = _pricing_entry(pricing, provider_id, model_id) or _pricing_entry(pricing, connection_id, model_id)
            if provider_hint or _zero_priced(pricing_entry) or _model_free_hint(model, model_id):
                free_models.append(model_id)

    if not free_models:
        try:
            for model in list_router_models():
                model_id = _model_id(model)
                if model_id and not _model_is_combo(model, model_id) and _model_free_hint(model, model_id):
                    free_models.append(model_id)
        except RuntimeError as exc:
            warnings.append(str(exc))

    return FreeModelDiscovery(models=rank_router_models(free_models), warnings=warnings)


__all__ = [
    "GOOROS_9ROUTER_API_KEY_NAME",
    "GOOROS_9ROUTER_COMBO_NAME",
    "FreeModelDiscovery",
    "choose_router_model",
    "discover_free_router_models",
    "ensure_router_api_key",
    "ensure_router_combo",
    "ensure_router_round_robin",
    "get_router_settings",
    "list_router_combos",
    "list_router_keys",
    "list_router_models",
    "rank_router_models",
    "router_json",
]
