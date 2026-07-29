from __future__ import annotations

import getpass
import os
import secrets
import shlex
from dataclasses import dataclass
from pathlib import Path

from .fsutil import atomic_write_text, ensure_dir
from .paths import InstallPaths


@dataclass
class CustomerConfig:
    owner_name: str
    owner_work: str
    owner_focus: str
    timezone: str
    telegram_chat_id: str
    telegram_bot_token: str
    telegram_allowed_users: str
    thread_scout: str
    thread_scribe: str
    thread_reach: str
    thread_dev: str
    telegram_home_channel: str
    public_ip: str
    acme_email: str
    dash_user: str
    dash_password: str
    model_policy: str

    def as_flat(self) -> dict[str, str]:
        return {
            "owner_name": self.owner_name,
            "owner_work": self.owner_work,
            "owner_focus": self.owner_focus,
            "timezone": self.timezone,
            "telegram_chat_id": self.telegram_chat_id,
            "thread_scout": self.thread_scout,
            "thread_scribe": self.thread_scribe,
            "thread_reach": self.thread_reach,
            "thread_dev": self.thread_dev,
            "telegram_home_channel": self.telegram_home_channel,
            "public_ip": self.public_ip,
            "acme_email": self.acme_email,
            "dash_user": self.dash_user,
            "model_policy": self.model_policy,
        }


def _prompt(label: str, default: str = "", secret: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    if secret:
        value = getpass.getpass(f"{label}{suffix}: ").strip()
    else:
        value = input(f"{label}{suffix}: ").strip()
    return value or default


def _arg(args: object, name: str, default: str = "") -> str:
    value = getattr(args, name, None)
    return str(value).strip() if value not in (None, "") else default


def _clean_env_value(value: str) -> str:
    text = value.strip()
    try:
        parsed = shlex.split(text, comments=True, posix=True)
        if parsed:
            return " ".join(parsed).strip()
        return ""
    except ValueError:
        return text.strip("\"'")


def _env_arg(args: object, name: str, env_file: dict[str, str], env_names: tuple[str, ...], default: str = "") -> str:
    value = getattr(args, name, None)
    if value not in (None, ""):
        return str(value).strip()
    for env_name in env_names:
        if env_name in env_file and env_file[env_name] not in (None, ""):
            return str(env_file[env_name]).strip()
    for env_name in env_names:
        value = os.environ.get(env_name)
        if value not in (None, ""):
            return str(value).strip()
    return default


def collect_customer_config(args: object, *, interactive: bool) -> CustomerConfig:
    generated_password = secrets.token_urlsafe(24)
    env_file_path = getattr(args, "env_file", None)
    env_file = _read_env_file(Path(env_file_path).expanduser()) if env_file_path else {}
    values = {
        "owner_name": _env_arg(args, "owner_name", env_file, ("GOOROS_OWNER_NAME", "OWNER_NAME")),
        "owner_work": _env_arg(args, "owner_work", env_file, ("GOOROS_OWNER_WORK", "OWNER_WORK")),
        "owner_focus": _env_arg(args, "owner_focus", env_file, ("GOOROS_OWNER_FOCUS", "OWNER_FOCUS")),
        "timezone": _env_arg(args, "timezone", env_file, ("GOOROS_TIMEZONE", "TIMEZONE"), "Asia/Ho_Chi_Minh"),
        "telegram_chat_id": _env_arg(args, "telegram_chat_id", env_file, ("TELEGRAM_CHAT_ID", "GOOROS_TELEGRAM_CHAT_ID")),
        "telegram_bot_token": _env_arg(args, "telegram_bot_token", env_file, ("TELEGRAM_BOT_TOKEN", "GOOROS_TELEGRAM_BOT_TOKEN")),
        "telegram_allowed_users": _env_arg(args, "telegram_allowed_users", env_file, ("TELEGRAM_ALLOWED_USERS", "GOOROS_TELEGRAM_ALLOWED_USERS")),
        "thread_scout": _env_arg(args, "thread_scout", env_file, ("TELEGRAM_THREAD_SCOUT", "GOOROS_THREAD_SCOUT", "THREAD_SCOUT")),
        "thread_scribe": _env_arg(args, "thread_scribe", env_file, ("TELEGRAM_THREAD_SCRIBE", "GOOROS_THREAD_SCRIBE", "THREAD_SCRIBE")),
        "thread_reach": _env_arg(args, "thread_reach", env_file, ("TELEGRAM_THREAD_REACH", "GOOROS_THREAD_REACH", "THREAD_REACH")),
        "thread_dev": _env_arg(args, "thread_dev", env_file, ("TELEGRAM_THREAD_DEV", "GOOROS_THREAD_DEV", "THREAD_DEV")),
        "telegram_home_channel": _env_arg(args, "telegram_home_channel", env_file, ("TELEGRAM_HOME_CHANNEL", "GOOROS_TELEGRAM_HOME_CHANNEL")),
        "public_ip": _env_arg(args, "public_ip", env_file, ("GOOROS_PUBLIC_IP", "PUBLIC_IP")),
        "acme_email": _env_arg(args, "acme_email", env_file, ("GOOROS_ACME_EMAIL", "ACME_EMAIL")),
        "dash_user": _env_arg(args, "dash_user", env_file, ("GOOROS_DASH_USER", "DASH_USER"), "gooros"),
        "dash_password": _env_arg(args, "dash_password", env_file, ("GOOROS_DASH_PASSWORD", "DASH_PASSWORD"), generated_password),
        "model_policy": _env_arg(args, "model_policy", env_file, ("GOOROS_MODEL_POLICY", "MODEL_POLICY"), "deepseek-free-first"),
    }
    if interactive:
        values["owner_name"] = _prompt("Owner name", values["owner_name"])
        values["owner_work"] = _prompt("Owner work/business", values["owner_work"])
        values["owner_focus"] = _prompt("Current focus", values["owner_focus"])
        values["timezone"] = _prompt("Timezone", values["timezone"])
        values["telegram_chat_id"] = _prompt("Telegram group chat ID (-100...)", values["telegram_chat_id"])
        values["telegram_bot_token"] = _prompt("Telegram bot token", values["telegram_bot_token"], secret=True)
        values["telegram_allowed_users"] = _prompt(
            "Telegram allowed user IDs (comma-separated, optional)",
            values["telegram_allowed_users"],
        )
        values["thread_scout"] = _prompt("Thread ID #scout", values["thread_scout"])
        values["thread_scribe"] = _prompt("Thread ID #scribe", values["thread_scribe"])
        values["thread_reach"] = _prompt("Thread ID #reach", values["thread_reach"])
        values["thread_dev"] = _prompt("Thread ID #dev", values["thread_dev"])
        values["telegram_home_channel"] = _prompt(
            "Telegram home channel target (telegram:chat:thread)",
            values["telegram_home_channel"] or f"telegram:{values['telegram_chat_id']}",
        )
        values["public_ip"] = _prompt("Public IP for sslip.io", values["public_ip"])
        values["acme_email"] = _prompt("ACME email for Caddy", values["acme_email"])
        values["dash_user"] = _prompt("Dashboard auth user", values["dash_user"])
        if not getattr(args, "dash_password", None):
            print("Dashboard password generated automatically. It will be shown once in the install report.")
    return CustomerConfig(**values)


def validate_required(config: CustomerConfig, *, public_dashboards: bool, require_telegram_token: bool = False) -> list[str]:
    missing = []
    required = ["owner_name", "timezone", "telegram_chat_id", "thread_scout", "thread_scribe", "thread_reach", "thread_dev"]
    if require_telegram_token:
        required.append("telegram_bot_token")
    if public_dashboards:
        required += ["public_ip", "acme_email", "dash_user", "dash_password"]
    for key in required:
        if not getattr(config, key):
            missing.append(key)
    return missing


def _read_colon_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip().lstrip("\ufeff")] = value.strip()
    return values


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip().lstrip("\ufeff")] = _clean_env_value(value)
    return values


def read_env_values(path: Path) -> dict[str, str]:
    return _read_env_file(path)


def merge_env_values(path: Path, updates: dict[str, str]) -> None:
    clean_updates = {}
    for key, value in updates.items():
        if value in (None, ""):
            continue
        text = str(value)
        if "\n" in text or "\r" in text:
            raise RuntimeError(f"refusing to write multiline env value: {key}")
        clean_updates[key] = text
    if not clean_updates:
        return
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.exists() else []
    seen: set[str] = set()
    out: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in raw:
            out.append(raw)
            continue
        key = raw.split("=", 1)[0].strip().lstrip("\ufeff")
        if key in clean_updates:
            out.append(f"{key}={clean_updates[key]}")
            seen.add(key)
        else:
            out.append(raw)
    for key, value in clean_updates.items():
        if key not in seen:
            out.append(f"{key}={value}")
    atomic_write_text(path, "\n".join(out).rstrip() + "\n", mode=0o600)


def read_customer_files(paths: InstallPaths) -> CustomerConfig:
    values = _read_colon_file(paths.customer_config)
    secrets = _read_env_file(paths.secrets_env)
    defaults = {
        "owner_name": "",
        "owner_work": "",
        "owner_focus": "",
        "timezone": "Asia/Ho_Chi_Minh",
        "telegram_chat_id": "",
        "telegram_bot_token": secrets.get("GOOROS_TELEGRAM_BOT_TOKEN", secrets.get("TELEGRAM_BOT_TOKEN", "")),
        "telegram_allowed_users": secrets.get("GOOROS_TELEGRAM_ALLOWED_USERS", secrets.get("TELEGRAM_ALLOWED_USERS", "")),
        "thread_scout": "",
        "thread_scribe": "",
        "thread_reach": "",
        "thread_dev": "",
        "telegram_home_channel": "",
        "public_ip": "",
        "acme_email": "",
        "dash_user": "gooros",
        "dash_password": "",
        "model_policy": "deepseek-free-first",
    }
    defaults.update({k: v for k, v in values.items() if k in defaults})
    return CustomerConfig(**defaults)


def read_caddy_hash(paths: InstallPaths) -> str:
    return _read_env_file(paths.secrets_env).get("GOOROS_DASH_PASS_HASH", "")


def write_customer_files(paths: InstallPaths, config: CustomerConfig, caddy_hash: str | None = None) -> None:
    ensure_dir(paths.config_dir, 0o700)
    existing_secrets = _read_env_file(paths.secrets_env)
    public = config.as_flat()
    text = "\n".join(f"{k}: {v}" for k, v in public.items()) + "\n"
    atomic_write_text(paths.customer_config, text, mode=0o600)
    env_lines = [
        f"TELEGRAM_HOME_CHANNEL={config.telegram_home_channel}",
        f"GOOROS_ACME_EMAIL={config.acme_email}",
        f"GOOROS_DASH_USER={config.dash_user}",
    ]
    if config.telegram_bot_token:
        env_lines.append(f"GOOROS_TELEGRAM_BOT_TOKEN={config.telegram_bot_token}")
    if config.telegram_allowed_users:
        env_lines.append(f"GOOROS_TELEGRAM_ALLOWED_USERS={config.telegram_allowed_users}")
    if caddy_hash:
        env_lines.append(f"GOOROS_DASH_PASS_HASH={caddy_hash}")
    preserved_prefixes = ("GOOROS_9ROUTER_",)
    preserved_keys = {"OPENAI_API_KEY"}
    written = {line.split("=", 1)[0] for line in env_lines if "=" in line}
    for key in sorted(existing_secrets):
        if key in written:
            continue
        if key in preserved_keys or key.startswith(preserved_prefixes):
            env_lines.append(f"{key}={existing_secrets[key]}")
    atomic_write_text(paths.secrets_env, "\n".join(env_lines) + "\n", mode=0o600)
