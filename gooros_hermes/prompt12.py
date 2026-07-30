from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PROMPT12_REPORT_JSON = "prompt12-telegram-group-access-verification.json"
PROMPT12_REPORT_MARKDOWN = "prompt12-telegram-group-access-verification.md"
PROMPT12_OWNER_ROUND_TRIP_TEXT = "xin chao"


def prompt12_report_dir(project_dir: Path) -> Path:
    return project_dir / "reports"


def prompt12_report_json_path(project_dir: Path) -> Path:
    return prompt12_report_dir(project_dir) / PROMPT12_REPORT_JSON


def prompt12_report_markdown_path(project_dir: Path) -> Path:
    return prompt12_report_dir(project_dir) / PROMPT12_REPORT_MARKDOWN


def render_prompt12_markdown_report(data: dict[str, Any]) -> str:
    lines = [
        "# Prompt 12 Telegram Group Access Verification",
        "",
        f"- Status: {data.get('status', 'unknown')}",
        f"- Hermes config: `{data.get('hermes_config_path', '')}`",
        f"- Telegram group chat ID: `{data.get('telegram_group_chat_id', '')}`",
        f"- Config token line preserved: {data.get('config_token_line_preserved', False)}",
        f"- require_mention=false: {data.get('require_mention_false', False)}",
        f"- group_allowed_chats contains group: {data.get('group_chat_allowed', False)}",
        f"- Gateway restart attempted: {data.get('gateway_restart_attempted', False)}",
        f"- Gateway healthy: {data.get('gateway_healthy', False)}",
        "",
        "## Owner Acceptance",
        "",
        str(data.get("owner_round_trip_instruction", "")),
    ]
    errors = data.get("errors", [])
    if errors:
        lines.extend(["", "## Errors", ""])
        for error in errors:
            lines.append(f"- {error}")
    return "\n".join(lines).rstrip() + "\n"


def validate_prompt12_report(data: dict[str, Any], *, config: Any) -> list[str]:
    failures: list[str] = []
    if data.get("prompt") != "Prompt 12":
        failures.append("Prompt 12 report has the wrong prompt label")
    if data.get("status") != "passed":
        failures.append("Prompt 12 report status is not passed")
    if str(data.get("telegram_group_chat_id", "")).strip() != str(config.telegram_chat_id).strip():
        failures.append("Prompt 12 report Telegram group chat ID does not match customer config")
    for key, message in (
        ("hermes_config_found", "Prompt 12 report does not confirm Hermes config path was found"),
        ("telegram_env_token_present", "Prompt 12 report does not confirm TELEGRAM_BOT_TOKEN is present"),
        ("config_token_line_preserved", "Prompt 12 report does not confirm existing config token line was preserved"),
        ("require_mention_false", "Prompt 12 report does not confirm require_mention=false"),
        ("group_chat_allowed", "Prompt 12 report does not confirm group_allowed_chats contains the configured group"),
        ("gateway_restart_attempted", "Prompt 12 report does not confirm gateway restart was attempted"),
        ("gateway_healthy", "Prompt 12 report does not confirm Hermes gateway is healthy"),
    ):
        if not data.get(key):
            failures.append(message)
    instruction = str(data.get("owner_round_trip_instruction", "")).lower()
    if "xin chao" not in instruction and "xin chào" not in instruction:
        failures.append('Prompt 12 report missing owner round-trip instruction to send "xin chao"')
    return failures


def load_prompt12_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))
