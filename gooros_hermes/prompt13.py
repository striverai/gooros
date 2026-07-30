from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .constants import SPECIALISTS


PROMPT13_REPORT_JSON = "prompt13-specialist-profile-isolation-verification.json"
PROMPT13_REPORT_MARKDOWN = "prompt13-specialist-profile-isolation-verification.md"

MESSAGING_PLATFORM_KEYS = (
    "platforms",
    "telegram",
    "discord",
    "whatsapp",
    "slack",
    "signal",
    "teams",
    "google_chat",
    "qqbot",
    "yuanbao",
    "matrix",
    "feishu",
    "dingtalk",
    "bluebubbles",
)

MESSAGING_ENV_PREFIXES = (
    "TELEGRAM_",
    "GOOROS_TELEGRAM_",
    "DISCORD_",
    "GOOROS_DISCORD_",
    "WHATSAPP_",
    "GOOROS_WHATSAPP_",
    "SLACK_",
    "GOOROS_SLACK_",
    "SIGNAL_",
    "GOOROS_SIGNAL_",
    "TEAMS_",
    "GOOROS_TEAMS_",
    "GOOGLE_CHAT_",
    "GOOROS_GOOGLE_CHAT_",
    "QQBOT_",
    "GOOROS_QQBOT_",
    "YUANBAO_",
    "GOOROS_YUANBAO_",
    "MATRIX_",
    "GOOROS_MATRIX_",
    "FEISHU_",
    "GOOROS_FEISHU_",
    "DINGTALK_",
    "GOOROS_DINGTALK_",
    "BLUEBUBBLES_",
    "GOOROS_BLUEBUBBLES_",
)

_ENV_KEY_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
_YAML_KEY_RE = re.compile(r"^(\s*)([^:#\s][^:#]*?)\s*:")


def prompt13_report_dir(project_dir: Path) -> Path:
    return project_dir / "reports"


def prompt13_report_json_path(project_dir: Path) -> Path:
    return prompt13_report_dir(project_dir) / PROMPT13_REPORT_JSON


def prompt13_report_markdown_path(project_dir: Path) -> Path:
    return prompt13_report_dir(project_dir) / PROMPT13_REPORT_MARKDOWN


def messaging_env_key(line: str) -> str | None:
    match = _ENV_KEY_RE.match(line)
    if not match:
        return None
    key = match.group(1).upper()
    if any(key.startswith(prefix) for prefix in MESSAGING_ENV_PREFIXES):
        return key
    return None


def scrub_profile_env(env_path: Path) -> list[str]:
    """Remove messaging-platform env keys from a specialist profile .env."""
    if not env_path.exists():
        return []
    lines = env_path.read_text(encoding="utf-8", errors="replace").splitlines()
    kept: list[str] = []
    removed: list[str] = []
    for line in lines:
        key = messaging_env_key(line)
        if key:
            removed.append(key)
            continue
        kept.append(line)
    if removed:
        env_path.write_text("\n".join(kept).rstrip() + ("\n" if kept else ""), encoding="utf-8")
    return removed


def _yaml_key(raw: str) -> tuple[int, str] | None:
    match = _YAML_KEY_RE.match(raw)
    if not match:
        return None
    return len(match.group(1)), match.group(2).strip()


def _block_end(lines: list[str], start: int, indent: int) -> int:
    end = len(lines)
    for idx in range(start + 1, len(lines)):
        stripped = lines[idx].strip()
        if not stripped or stripped.startswith("#"):
            continue
        child = _yaml_key(lines[idx])
        child_indent = child[0] if child else len(lines[idx]) - len(lines[idx].lstrip(" "))
        if child_indent <= indent:
            end = idx
            break
    return end


def scrub_profile_config(config_path: Path) -> list[str]:
    """Remove messaging platform blocks while preserving model/tool config."""
    if not config_path.exists():
        return []
    lines = config_path.read_text(encoding="utf-8", errors="replace").splitlines()
    removed: list[str] = []
    idx = 0
    keys = set(MESSAGING_PLATFORM_KEYS)
    while idx < len(lines):
        current = _yaml_key(lines[idx])
        if current and current[1] in keys:
            indent, key = current
            end = _block_end(lines, idx, indent)
            del lines[idx:end]
            removed.append(key)
            continue
        idx += 1
    if removed:
        config_path.write_text("\n".join(lines).rstrip() + ("\n" if lines else ""), encoding="utf-8")
    return removed


def _has_messaging_config_text(text: str) -> list[str]:
    findings: list[str] = []
    for key in MESSAGING_PLATFORM_KEYS:
        pattern = re.compile(rf"(?m)^\s*{re.escape(key)}\s*:")
        if pattern.search(text):
            findings.append(key)
    token_pattern = re.compile(r"(?im)(telegram|discord|whatsapp|slack|signal|teams|google_chat|qqbot|yuanbao|matrix|feishu|dingtalk|bluebubbles).*(token|secret|webhook)")
    if token_pattern.search(text):
        findings.append("messaging-token-like-config")
    return sorted(set(findings))


def validate_specialist_profile_isolation(hermes_home: Path, agent: str) -> dict[str, Any]:
    profile_dir = hermes_home / "profiles" / agent
    soul_path = profile_dir / "SOUL.md"
    config_path = profile_dir / "config.yaml"
    env_path = profile_dir / ".env"
    memory_dir = profile_dir / "memories"
    errors: list[str] = []
    config_findings: list[str] = []
    env_findings: list[str] = []

    if agent not in SPECIALISTS:
        errors.append(f"unknown specialist profile: {agent}")
    if not profile_dir.exists():
        errors.append(f"profile missing: {agent}")
    if not soul_path.exists():
        errors.append(f"SOUL.md missing for {agent}")
    else:
        soul_text = soul_path.read_text(encoding="utf-8", errors="replace")
        if agent not in soul_text.lower():
            errors.append(f"SOUL.md for {agent} does not identify its own profile")
    if not memory_dir.exists():
        errors.append(f"memory directory missing for {agent}")
    if config_path.exists():
        config_text = config_path.read_text(encoding="utf-8", errors="replace")
        config_findings = _has_messaging_config_text(config_text)
        for finding in config_findings:
            errors.append(f"profile {agent} config still has messaging platform block/key: {finding}")
    if env_path.exists():
        env_lines = env_path.read_text(encoding="utf-8", errors="replace").splitlines()
        env_findings = [key for line in env_lines if (key := messaging_env_key(line))]
        for key in env_findings:
            errors.append(f"profile {agent} .env still has messaging key: {key}")
    return {
        "agent": agent,
        "profile_path": str(profile_dir),
        "profile_exists": profile_dir.exists(),
        "soul_path": str(soul_path),
        "soul_exists": soul_path.exists(),
        "config_path": str(config_path),
        "config_exists": config_path.exists(),
        "env_path": str(env_path),
        "env_exists": env_path.exists(),
        "memory_dir": str(memory_dir),
        "memory_dir_exists": memory_dir.exists(),
        "messaging_config_findings": config_findings,
        "messaging_env_findings": env_findings,
        "config_isolated": not config_findings,
        "env_isolated": not env_findings,
        "verified": not errors,
        "errors": errors,
    }


def validate_prompt13_report(data: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if data.get("prompt") != "Prompt 13":
        failures.append("Prompt 13 report has the wrong prompt label")
    if data.get("status") != "passed":
        failures.append("Prompt 13 report status is not passed")
    if not data.get("orchestrator_keeps_single_bot"):
        failures.append("Prompt 13 report does not confirm Orchestrator/default keeps the single bot")
    checks = data.get("checks")
    if not isinstance(checks, list):
        return [*failures, "Prompt 13 report missing checks list"]
    seen: set[str] = set()
    for item in checks:
        if not isinstance(item, dict):
            failures.append("Prompt 13 report contains non-object check")
            continue
        agent = str(item.get("agent", "")).strip()
        seen.add(agent)
        if agent not in SPECIALISTS:
            failures.append(f"Prompt 13 report has unknown specialist: {agent}")
            continue
        for key, message in (
            ("profile_exists", f"Prompt 13 report profile missing for {agent}"),
            ("soul_exists", f"Prompt 13 report SOUL.md missing for {agent}"),
            ("memory_dir_exists", f"Prompt 13 report memory dir missing for {agent}"),
            ("config_isolated", f"Prompt 13 report config not isolated for {agent}"),
            ("env_isolated", f"Prompt 13 report .env not isolated for {agent}"),
            ("verified", f"Prompt 13 report verification failed for {agent}"),
        ):
            if not item.get(key):
                failures.append(message)
    missing = [agent for agent in SPECIALISTS if agent not in seen]
    if missing:
        failures.append("Prompt 13 report missing specialists: " + ", ".join(missing))
    return failures


def render_prompt13_markdown_report(data: dict[str, Any]) -> str:
    lines = [
        "# Prompt 13 Specialist Profile Isolation Verification",
        "",
        f"- Status: {data.get('status', 'unknown')}",
        f"- Orchestrator/default keeps single bot: {data.get('orchestrator_keeps_single_bot', False)}",
        "",
        "## Specialist Checks",
        "",
    ]
    for item in data.get("checks", []):
        errors = item.get("errors") or []
        lines.append(
            f"- `{item.get('agent')}`: profile={item.get('profile_exists')}, "
            f"SOUL={item.get('soul_exists')}, config_isolated={item.get('config_isolated')}, "
            f"env_isolated={item.get('env_isolated')}, verified={item.get('verified')}"
        )
        if errors:
            for error in errors:
                lines.append(f"  - {error}")
    return "\n".join(lines).rstrip() + "\n"


def load_prompt13_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))
