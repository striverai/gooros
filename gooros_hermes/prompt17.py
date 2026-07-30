from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .constants import SPECIALISTS
from .prompt16 import parse_multiplex_profiles_enabled, parse_plugin_enabled
from .yaml_merge import telegram_group_config_values


PROMPT17_REPORT_JSON = "prompt17-telegram-routing-audit.json"
PROMPT17_REPORT_MARKDOWN = "prompt17-telegram-routing-audit.md"
PROMPT17_PLUGIN = "telegram_topic_profiles"

PROMPT17_SYMPTOM_GUIDANCE = {
    "specialist_channel_answers_as_orchestrator": [
        "multiplex_profiles is not true at the top level of Hermes config",
        "telegram_topic_profiles is not enabled",
        "the Telegram thread ID in topics.json does not match that topic",
        "the gateway was not restarted after activation",
    ],
    "no_channel_answers": [
        "platforms.telegram.group_allowed_chats is missing the group chat ID",
        "the gateway was not restarted after activation",
    ],
}

_CONFIG_KEY_RE = re.compile(r"^(\s*)([^:#\s][^:#]*?)\s*:\s*(.*?)\s*$")


def prompt17_report_dir(project_dir: Path) -> Path:
    return project_dir / "reports"


def prompt17_report_json_path(project_dir: Path) -> Path:
    return prompt17_report_dir(project_dir) / PROMPT17_REPORT_JSON


def prompt17_report_markdown_path(project_dir: Path) -> Path:
    return prompt17_report_dir(project_dir) / PROMPT17_REPORT_MARKDOWN


def expected_prompt17_topic_routes(config: Any) -> dict[str, str]:
    return {
        str(config.thread_scout): "scout",
        str(config.thread_scribe): "scribe",
        str(config.thread_reach): "reach",
        str(config.thread_dev): "dev",
    }


def prompt17_audit_table(config: Any) -> list[dict[str, object]]:
    return [
        {
            "channel": "#command",
            "thread_id": str(config.thread_command),
            "profile": "orchestrator-root",
            "in_topics_json": False,
            "note": "#command is intentionally omitted so the root/default Orchestrator answers it",
        },
        {
            "channel": "#scout",
            "thread_id": str(config.thread_scout),
            "profile": "scout",
            "in_topics_json": True,
            "note": "specialist topic",
        },
        {
            "channel": "#scribe",
            "thread_id": str(config.thread_scribe),
            "profile": "scribe",
            "in_topics_json": True,
            "note": "specialist topic",
        },
        {
            "channel": "#reach",
            "thread_id": str(config.thread_reach),
            "profile": "reach",
            "in_topics_json": True,
            "note": "specialist topic",
        },
        {
            "channel": "#dev",
            "thread_id": str(config.thread_dev),
            "profile": "dev",
            "in_topics_json": True,
            "note": "specialist topic",
        },
    ]


def top_level_multiplex_profiles(config_text: str) -> tuple[bool, bool]:
    """Return (top_level_true, nested_gateway_true)."""
    top_level_true = False
    nested_gateway_true = False
    in_gateway = False
    gateway_indent = 0
    for raw in str(config_text or "").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        match = _CONFIG_KEY_RE.match(raw)
        if not match:
            continue
        indent = len(match.group(1))
        key = match.group(2).strip()
        value = match.group(3).strip()
        if indent == 0:
            in_gateway = key == "gateway"
            gateway_indent = indent if in_gateway else 0
        elif in_gateway and indent <= gateway_indent:
            in_gateway = False
        if key == "multiplex_profiles":
            enabled = parse_multiplex_profiles_enabled(value or "true")
            if indent == 0 and enabled:
                top_level_true = True
            if in_gateway and indent > gateway_indent and enabled:
                nested_gateway_true = True
    return top_level_true, nested_gateway_true


def group_allowed_chats_contains(config_text: str, chat_id: str) -> bool:
    values = telegram_group_config_values(config_text)
    allowed = values.get("group_allowed_chats")
    return isinstance(allowed, list) and str(chat_id) in {str(item).strip() for item in allowed}


def validate_prompt17_topics(topic_map: dict[str, Any], config: Any) -> list[str]:
    failures: list[str] = []
    expected = expected_prompt17_topic_routes(config)
    normalized = {str(key): str(value).strip() for key, value in topic_map.items()}
    if normalized != expected:
        failures.append("Prompt 17 topics.json must contain exactly the four specialist topic routes")
    command_thread = str(getattr(config, "thread_command", "") or "")
    if command_thread and command_thread in normalized:
        failures.append("Prompt 17 topics.json must omit #command so Orchestrator remains the root/default agent")
    if "orchestrator" in {value.lower() for value in normalized.values()}:
        failures.append("Prompt 17 topics.json must not route any topic to orchestrator")
    missing_threads = [thread for thread in expected if not thread]
    if missing_threads:
        failures.append("Prompt 17 missing one or more specialist thread IDs in customer config")
    return failures


def _messaging_config_findings(config_text: str) -> list[str]:
    findings: set[str] = set()
    for raw in str(config_text or "").splitlines():
        match = _CONFIG_KEY_RE.match(raw)
        if not match:
            continue
        key = match.group(2).strip().lower()
        if key in {"platforms", "telegram"}:
            findings.add(key)
    token_pattern = re.compile(r"(?im)telegram.*(token|secret|webhook)")
    if token_pattern.search(config_text or ""):
        findings.add("telegram-token-like-config")
    return sorted(findings)


def audit_prompt17_profile(hermes_home: Path, agent: str) -> dict[str, object]:
    profile_dir = hermes_home / "profiles" / agent
    soul_path = profile_dir / "SOUL.md"
    config_path = profile_dir / "config.yaml"
    errors: list[str] = []
    findings: list[str] = []
    if agent not in SPECIALISTS:
        errors.append(f"unknown specialist profile: {agent}")
    if not profile_dir.exists():
        errors.append(f"profile missing: {agent}")
    if not soul_path.exists():
        errors.append(f"SOUL.md missing for {agent}")
    if config_path.exists():
        config_text = config_path.read_text(encoding="utf-8", errors="replace")
        findings = _messaging_config_findings(config_text)
        for finding in findings:
            errors.append(f"profile {agent} config still has messaging block/key: {finding}")
    return {
        "agent": agent,
        "profile_path": str(profile_dir),
        "profile_exists": profile_dir.exists(),
        "soul_path": str(soul_path),
        "soul_exists": soul_path.exists(),
        "config_path": str(config_path),
        "config_exists": config_path.exists(),
        "messaging_config_findings": findings,
        "config_isolated": not findings,
        "verified": not errors,
        "errors": errors,
    }


def validate_prompt17_report(data: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if data.get("prompt") != "Prompt 17":
        failures.append("Prompt 17 report has the wrong prompt label")
    if data.get("status") != "passed":
        failures.append("Prompt 17 report status is not passed")
    if not data.get("multiplex_profiles_top_level"):
        failures.append("Prompt 17 did not confirm top-level multiplex_profiles: true")
    if data.get("multiplex_profiles_nested_under_gateway"):
        failures.append("Prompt 17 found multiplex_profiles nested under gateway")
    if not data.get("group_allowed"):
        failures.append("Prompt 17 did not confirm group_allowed_chats contains the group chat ID")
    if not data.get("plugin_exists"):
        failures.append("Prompt 17 did not confirm telegram_topic_profiles plugin exists")
    if not data.get("plugin_enabled"):
        failures.append("Prompt 17 did not confirm telegram_topic_profiles is enabled")
    if not data.get("topics_match"):
        failures.append("Prompt 17 did not confirm topics.json maps the four specialist topics exactly")
    if not data.get("command_omitted_from_topics"):
        failures.append("Prompt 17 did not confirm #command is omitted from topics.json")
    table = data.get("route_table")
    if not isinstance(table, list) or len(table) != 5:
        failures.append("Prompt 17 route table must contain #command plus four specialist channels")
    profiles = data.get("profile_checks")
    if not isinstance(profiles, list) or len(profiles) != len(SPECIALISTS):
        failures.append("Prompt 17 profile checks missing one or more specialists")
    else:
        for item in profiles:
            if not isinstance(item, dict) or not item.get("verified"):
                failures.append(f"Prompt 17 profile check failed for {item.get('agent', 'unknown') if isinstance(item, dict) else 'unknown'}")
    guidance = data.get("symptom_guidance")
    if not isinstance(guidance, dict):
        failures.append("Prompt 17 report missing symptom guidance")
    else:
        for key in PROMPT17_SYMPTOM_GUIDANCE:
            if key not in guidance:
                failures.append(f"Prompt 17 report missing symptom guidance: {key}")
    return failures


def render_prompt17_markdown_report(data: dict[str, Any]) -> str:
    lines = [
        "# Prompt 17 Telegram Routing Audit",
        "",
        f"- Status: {data.get('status', 'unknown')}",
        f"- Top-level multiplex_profiles: {data.get('multiplex_profiles_top_level', False)}",
        f"- group_allowed_chats contains group: {data.get('group_allowed', False)}",
        f"- Plugin exists: {data.get('plugin_exists', False)}",
        f"- Plugin enabled: {data.get('plugin_enabled', False)}",
        f"- Topics match specialists only: {data.get('topics_match', False)}",
        f"- #command omitted from topics.json: {data.get('command_omitted_from_topics', False)}",
        "",
        "## Route Table",
        "",
        "| Channel | Thread ID | Profile | In topics.json |",
        "|---|---:|---|---|",
    ]
    for row in data.get("route_table", []) or []:
        lines.append(
            f"| {row.get('channel', '')} | {row.get('thread_id', '')} | "
            f"{row.get('profile', '')} | {row.get('in_topics_json', False)} |"
        )
    lines.extend(["", "## Profile Checks", ""])
    for item in data.get("profile_checks", []) or []:
        lines.append(
            f"- `{item.get('agent')}`: SOUL={item.get('soul_exists')}, "
            f"config_isolated={item.get('config_isolated')}, verified={item.get('verified')}"
        )
    lines.extend(["", "## Symptom Guidance", ""])
    guidance = data.get("symptom_guidance") or {}
    for key, causes in guidance.items():
        lines.append(f"- `{key}`:")
        for cause in causes:
            lines.append(f"  - {cause}")
    errors = data.get("errors") or []
    if errors:
        lines.extend(["", "## Errors", ""])
        for error in errors:
            lines.append(f"- {error}")
    return "\n".join(lines).rstrip() + "\n"


def load_prompt17_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))
