from __future__ import annotations

import json
import os
from http.cookies import SimpleCookie
from dataclasses import dataclass, field
from typing import Iterable
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

from .constants import GOOROS_9ROUTER_API_KEY_NAME, GOOROS_9ROUTER_COMBO_NAME

ROUTER_BASE_URL = os.environ.get("GOOROS_9ROUTER_BASE_URL", "http://127.0.0.1:20128").rstrip("/")
FREE_PROVIDER_HINT_IDS = {"kiro", "gemini-cli", "opencode", "opencode-free", "mimo-free", "mimo-code-free", "oc", "mmf"}
FREE_PROVIDER_ALIASES = {
    "gemini-cli": "gcli",
    "kiro": "kr",
    "mimo-free": "mmf",
    "opencode": "oc",
    "opencode-free": "oc",
}
FREE_TEXT_HINTS = ("free", "noauth", "no-auth", "opencode free", "mimo free", "mimo code free")
PRICING_FIELDS = ("input", "output", "cached", "reasoning", "cache_creation")
_ROUTER_COOKIE = ""


@dataclass(frozen=True)
class RequiredFreeProvider:
    provider_id: str
    display_name: str
    alias: str
    fetcher_url: str
    fetcher_type: str


REQUIRED_FREE_PROVIDERS: tuple[RequiredFreeProvider, ...] = (
    RequiredFreeProvider(
        provider_id="opencode",
        display_name="OpenCode Free",
        alias="oc",
        fetcher_url="https://opencode.ai/zen/v1/models",
        fetcher_type="opencode-free",
    ),
    RequiredFreeProvider(
        provider_id="mimo-free",
        display_name="MiMo Code Free",
        alias="mmf",
        fetcher_url="https://models.dev/api.json",
        fetcher_type="mimo-free",
    ),
)
REQUIRED_FREE_PROVIDER_BY_ID = {item.provider_id: item for item in REQUIRED_FREE_PROVIDERS}


@dataclass(frozen=True)
class FreeModelDiscovery:
    models: list[str]
    warnings: list[str]
    required_provider_models: dict[str, list[str]] = field(default_factory=dict)
    missing_required_providers: list[str] = field(default_factory=list)


def _local_router_base() -> bool:
    host = (urlparse(ROUTER_BASE_URL).hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def _management_path(path: str) -> bool:
    return path.startswith("/api/") and not path.startswith("/api/auth/")


def _router_initial_password() -> str:
    return os.environ.get("GOOROS_9ROUTER_INITIAL_PASSWORD") or os.environ.get("INITIAL_PASSWORD") or ""


def _auth_cookie_from_headers(headers: object) -> str:
    get_all = getattr(headers, "get_all", None)
    values = get_all("Set-Cookie") if callable(get_all) else None
    if not values:
        value = getattr(headers, "get", lambda _name, _default=None: _default)("Set-Cookie", "")
        values = [value] if value else []
    cookie = SimpleCookie()
    for value in values:
        cookie.load(value)
    auth = cookie.get("auth_token")
    return f"auth_token={auth.value}" if auth and auth.value else ""


def router_login(timeout: int = 10) -> str:
    global _ROUTER_COOKIE
    password = _router_initial_password()
    if not password:
        raise RuntimeError(
            "9Router management API requires dashboard login, but INITIAL_PASSWORD/GOOROS_9ROUTER_INITIAL_PASSWORD is not available"
        )
    body = json.dumps({"password": password}).encode("utf-8")
    request = Request(
        f"{ROUTER_BASE_URL}/api/auth/login",
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "gooros-hermes-installer",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            response.read()
            cookie = _auth_cookie_from_headers(response.headers)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"9Router login failed ({exc.code}): {detail or exc.reason}") from exc
    except Exception as exc:
        raise RuntimeError(f"9Router login failed: {exc}") from exc
    if not cookie:
        raise RuntimeError("9Router login succeeded but did not return an auth_token cookie")
    _ROUTER_COOKIE = cookie
    return cookie


def router_json(path: str, *, method: str = "GET", body: object | None = None, timeout: int = 10, _retry_login: bool = True) -> object:
    global _ROUTER_COOKIE
    path = path if path.startswith("/") else f"/{path}"
    payload = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Accept": "application/json", "User-Agent": "gooros-hermes-installer"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if _ROUTER_COOKIE and _management_path(path):
        headers["Cookie"] = _ROUTER_COOKIE
    request = Request(f"{ROUTER_BASE_URL}{path}", data=payload, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        if exc.code == 401 and _retry_login and _local_router_base() and _management_path(path):
            _ROUTER_COOKIE = router_login(timeout=timeout)
            return router_json(path, method=method, body=body, timeout=timeout, _retry_login=False)
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


def list_router_suggested_models(source_url: str, filter_type: str) -> list[dict]:
    query = urlencode({"url": source_url, "type": filter_type})
    return _object_list(router_json(f"/api/providers/suggested-models?{query}", timeout=30), "data", "models", "items")


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


def _provider_specific_data(connection: dict) -> dict:
    data = connection.get("providerSpecificData")
    return data if isinstance(data, dict) else {}


def _provider_key(connection: dict) -> str:
    return str(connection.get("provider") or connection.get("providerId") or "").strip().lower().replace("_", "-")


def _provider_free_hint(connection: dict) -> bool:
    provider = _provider_key(connection)
    if provider in FREE_PROVIDER_HINT_IDS:
        return True
    data = _provider_specific_data(connection)
    if data.get("noAuth") is True or data.get("isFree") is True or data.get("freeTier") is True:
        return True
    text = _to_text(provider, connection.get("name"), connection.get("authType"), data)
    return any(hint in text for hint in FREE_TEXT_HINTS)


def _required_free_provider_for_connection(connection: dict) -> RequiredFreeProvider | None:
    provider = _provider_key(connection)
    text = _to_text(
        provider,
        connection.get("name"),
        connection.get("displayName"),
        connection.get("authType"),
        _provider_specific_data(connection),
    )
    for spec in REQUIRED_FREE_PROVIDERS:
        if provider in {spec.provider_id, spec.alias}:
            return spec
        if spec.display_name.lower() in text:
            return spec
    return None


def _provider_alias(connection: dict, provider_id: str) -> str:
    data = _provider_specific_data(connection)
    for key in ("prefix", "alias", "uiAlias", "providerAlias"):
        value = data.get(key) or connection.get(key)
        if value:
            alias = str(value).strip()
            if alias:
                return alias
    return FREE_PROVIDER_ALIASES.get(provider_id, provider_id)


def _model_as_dict(model: dict | str) -> dict:
    return model if isinstance(model, dict) else {"id": model}


def _prefix_router_model(model_id: str, alias: str, provider_id: str) -> str:
    model_id = str(model_id).strip()
    if not model_id:
        return ""
    for prefix in {alias, provider_id}:
        if prefix and model_id.startswith(f"{prefix}/"):
            return model_id
    prefix = alias or provider_id
    return f"{prefix}/{model_id}" if prefix else model_id


def _required_free_provider_for_model(model_id: str) -> RequiredFreeProvider | None:
    if "/" not in model_id:
        return None
    prefix = model_id.split("/", 1)[0].strip().lower()
    for spec in REQUIRED_FREE_PROVIDERS:
        if prefix in {spec.alias, spec.provider_id}:
            return spec
    return None


def _append_model(models: list[str], required: dict[str, list[str]], model_id: str, spec: RequiredFreeProvider | None = None) -> None:
    if not model_id:
        return
    models.append(model_id)
    required_spec = spec or _required_free_provider_for_model(model_id)
    if required_spec:
        required.setdefault(required_spec.provider_id, []).append(model_id)


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


def select_free_router_models(
    providers: list[dict],
    provider_models_by_connection_id: dict[str, list[dict]],
    pricing: dict,
    *,
    suggested_models_by_provider_id: dict[str, list[dict | str]] | None = None,
    fallback_models: list[dict] | None = None,
) -> FreeModelDiscovery:
    free_models: list[str] = []
    required_models: dict[str, list[str]] = {spec.provider_id: [] for spec in REQUIRED_FREE_PROVIDERS}
    suggested_models_by_provider_id = suggested_models_by_provider_id or {}

    for spec in REQUIRED_FREE_PROVIDERS:
        for model in suggested_models_by_provider_id.get(spec.provider_id, []):
            model_id = _model_id(_model_as_dict(model))
            if model_id and not _model_is_combo(_model_as_dict(model), model_id):
                _append_model(free_models, required_models, _prefix_router_model(model_id, spec.alias, spec.provider_id), spec)

    for connection in providers:
        connection_id = str(connection.get("id") or "").strip()
        provider_id = _provider_key(connection)
        if not connection_id:
            continue
        provider_hint = _provider_free_hint(connection)
        required_spec = _required_free_provider_for_connection(connection)
        provider_alias = required_spec.alias if required_spec else _provider_alias(connection, provider_id)
        provider_key = required_spec.provider_id if required_spec else provider_id
        for model in provider_models_by_connection_id.get(connection_id, []):
            model_id = _model_id(model)
            if not model_id or _model_is_combo(model, model_id):
                continue
            pricing_entry = _pricing_entry(pricing, provider_id, model_id) or _pricing_entry(pricing, connection_id, model_id)
            if required_spec or provider_hint or _zero_priced(pricing_entry) or _model_free_hint(model, model_id):
                _append_model(
                    free_models,
                    required_models,
                    _prefix_router_model(model_id, provider_alias, provider_key),
                    required_spec,
                )

    for model in fallback_models or []:
        model_id = _model_id(model)
        if not model_id or _model_is_combo(model, model_id):
            continue
        required_spec = _required_free_provider_for_model(model_id)
        if required_spec or _model_free_hint(model, model_id):
            _append_model(free_models, required_models, model_id, required_spec)

    ranked_required = {provider_id: rank_router_models(models) for provider_id, models in required_models.items()}
    missing_required = [
        spec.display_name for spec in REQUIRED_FREE_PROVIDERS if not ranked_required.get(spec.provider_id)
    ]
    return FreeModelDiscovery(
        models=rank_router_models(free_models),
        warnings=[],
        required_provider_models=ranked_required,
        missing_required_providers=missing_required,
    )


def discover_free_router_models() -> FreeModelDiscovery:
    warnings: list[str] = []
    suggested_models: dict[str, list[dict | str]] = {}
    provider_models_by_connection_id: dict[str, list[dict]] = {}
    fallback_models: list[dict] = []

    for spec in REQUIRED_FREE_PROVIDERS:
        try:
            suggested_models[spec.provider_id] = list_router_suggested_models(spec.fetcher_url, spec.fetcher_type)
        except RuntimeError as exc:
            warnings.append(f"{spec.display_name}: {exc}")

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
        if not connection_id:
            continue
        try:
            provider_models_by_connection_id[connection_id] = list_router_provider_models(connection_id)
        except RuntimeError as exc:
            warnings.append(f"{connection_id}: {exc}")
    try:
        fallback_models = list_router_models()
    except RuntimeError as exc:
        warnings.append(str(exc))

    selected = select_free_router_models(
        providers,
        provider_models_by_connection_id,
        pricing,
        suggested_models_by_provider_id=suggested_models,
        fallback_models=fallback_models,
    )
    return FreeModelDiscovery(
        models=selected.models,
        warnings=[*warnings, *selected.warnings],
        required_provider_models=selected.required_provider_models,
        missing_required_providers=selected.missing_required_providers,
    )


__all__ = [
    "GOOROS_9ROUTER_API_KEY_NAME",
    "GOOROS_9ROUTER_COMBO_NAME",
    "FreeModelDiscovery",
    "REQUIRED_FREE_PROVIDERS",
    "RequiredFreeProvider",
    "choose_router_model",
    "discover_free_router_models",
    "ensure_router_api_key",
    "ensure_router_combo",
    "ensure_router_round_robin",
    "get_router_settings",
    "list_router_combos",
    "list_router_keys",
    "list_router_models",
    "list_router_suggested_models",
    "rank_router_models",
    "router_json",
    "select_free_router_models",
]
