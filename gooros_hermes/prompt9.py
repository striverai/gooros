from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .constants import AGENTS
from .prompt6 import normalize


PROMPT9_MEMORY_MARKER = "activity logging rule Prompt 9 v1"
PROMPT9_SOUL_MARKER = "Prompt 9 activity logging before every response v1"
PROMPT9_REPORT_JSON = "prompt9-activity-logging-verification.json"
PROMPT9_REPORT_MARKDOWN = "prompt9-activity-logging-verification.md"
PROMPT9_SMOKE_TASK = "saved activity logging rule to memory"


def prompt9_report_dir(project_dir: Path) -> Path:
    return project_dir / "reports"


def prompt9_report_json_path(project_dir: Path) -> Path:
    return prompt9_report_dir(project_dir) / PROMPT9_REPORT_JSON


def prompt9_report_markdown_path(project_dir: Path) -> Path:
    return prompt9_report_dir(project_dir) / PROMPT9_REPORT_MARKDOWN


def render_prompt9_policy(agent: str, workspace: Path) -> str:
    return f"""<!-- GOOROS-HERMES-MANAGED: {agent} {PROMPT9_SOUL_MARKER} -->
## Prompt 9 - activity logging before every response

- Before sending any response, run: `bash ~/.hermes/agents/_shared/log-task-local.sh "{agent}" "<brief description>" "<status>" "<model>"`.
- Use lowercase agent name exactly as `{agent}`.
- Use `completed` for a successful response and `failed` when anything goes wrong.
- Use the exact model you are currently running on in `<model>`.
- Never leave any field blank.
- Log every response, including simple answers; keep the task description under 140 characters.
- Run the logging command before sending the response.
- Do not mention logging unless the owner explicitly asks.
- Prompt 9 smoke task text is `{PROMPT9_SMOKE_TASK}`.
- Workspace remains `{workspace}`.
<!-- END GOOROS-HERMES-MANAGED: {agent} {PROMPT9_SOUL_MARKER} -->"""


def render_prompt9_memory(agent: str) -> str:
    return (
        f"Prompt 9 activity logging rule for {agent}: before sending any response, run "
        f"`bash ~/.hermes/agents/_shared/log-task-local.sh \"{agent}\" \"<brief description>\" \"<status>\" \"<model>\"`; "
        f"agent-name must be lowercase `{agent}`; status must be `completed` or `failed`; "
        "model must be the exact current model; no field may be blank; log every response including simple answers; "
        "task description must stay under 140 characters; run logging before the response; "
        "do not mention logging unless the owner asks."
    )


def _has_any(normalized: str, phrases: tuple[str, ...]) -> bool:
    return any(normalize(phrase) in normalized for phrase in phrases)


def validate_prompt9_soul(agent: str, soul_text: str, workspace: Path) -> list[str]:
    failures: list[str] = []
    normalized = normalize(soul_text)
    if f"GOOROS-HERMES-MANAGED: {agent} {PROMPT9_SOUL_MARKER}" not in soul_text:
        failures.append(f"SOUL.md for {agent} missing Prompt 9 managed logging policy")
    exact_command = f'bash ~/.hermes/agents/_shared/log-task-local.sh "{agent}" "<brief description>" "<status>" "<model>"'
    if exact_command not in soul_text:
        failures.append(f"SOUL.md for {agent} missing exact Prompt 9 logging command")
    checks = (
        ("completed status", ("completed",)),
        ("failed status", ("failed",)),
        ("exact current model", ("exact model", "model ban dang chay", "currently running")),
        ("no blank fields", ("never leave any field blank", "no field may be blank", "khong de trong")),
        ("every response", ("every response", "bat ky phan hoi", "moi phan hoi")),
        ("simple responses", ("simple answers", "simple response", "cau tra loi don gian")),
        ("140 character description limit", ("140",)),
        ("log before response", ("before sending", "before the response", "truoc khi gui")),
        ("do not mention logging unless owner asks", ("do not mention logging unless the owner", "khong nhac den logging")),
    )
    for label, phrases in checks:
        if not _has_any(normalized, phrases):
            failures.append(f"SOUL.md for {agent} missing Prompt 9 requirement: {label}")
    if str(workspace) not in soul_text:
        failures.append(f"SOUL.md for {agent} missing Prompt 9 workspace path")
    if PROMPT9_SMOKE_TASK not in soul_text:
        failures.append(f"SOUL.md for {agent} missing Prompt 9 smoke task text")
    return failures


def validate_prompt9_memory(agent: str, memory_text: str) -> list[str]:
    marker = f"GOOROS-HERMES-MANAGED: {agent} {PROMPT9_MEMORY_MARKER}"
    failures: list[str] = []
    normalized = normalize(memory_text)
    if marker not in memory_text:
        failures.append(f"memory missing Prompt 9 activity logging seed for {agent}")
    exact_command = f'bash ~/.hermes/agents/_shared/log-task-local.sh "{agent}"'
    if exact_command not in memory_text:
        failures.append(f"memory for {agent} missing exact Prompt 9 logging command prefix")
    for label, phrases in (
        ("completed/failed status", ("completed", "failed")),
        ("exact current model", ("exact current model", "exact model")),
        ("no blank fields", ("no field may be blank", "never leave any field blank")),
        ("every response", ("every response",)),
        ("simple answers", ("simple answers",)),
        ("140 character description limit", ("140",)),
        ("before response", ("before sending", "before the response")),
        ("owner-only logging mention", ("unless the owner asks",)),
    ):
        if not _has_any(normalized, phrases):
            failures.append(f"memory for {agent} missing Prompt 9 requirement: {label}")
    return failures


def validate_prompt9_report(data: dict[str, Any], *, project_dir: Path) -> list[str]:
    failures: list[str] = []
    if data.get("prompt") != "Prompt 9":
        failures.append("Prompt 9 report has the wrong prompt label")
    if data.get("status") != "passed":
        failures.append("Prompt 9 report status is not passed")
    if Path(str(data.get("agent_log_db", ""))).expanduser() != project_dir / "agent-logs.db":
        failures.append("Prompt 9 report points to the wrong agent log DB path")

    for section_name in ("memory_checks", "log_smoke_checks"):
        checks = data.get(section_name)
        if not isinstance(checks, list):
            failures.append(f"Prompt 9 report missing {section_name} list")
            checks = []
        seen = {str(item.get("agent", "")).strip() for item in checks if isinstance(item, dict)}
        missing = [agent for agent in AGENTS if agent not in seen]
        if missing:
            failures.append(f"Prompt 9 report missing {section_name} for: " + ", ".join(missing))
        for item in checks:
            if not isinstance(item, dict) or item.get("agent") not in AGENTS:
                continue
            agent = str(item["agent"])
            if not item.get("verified"):
                failures.append(f"Prompt 9 {section_name} did not pass for {agent}")
            if section_name == "log_smoke_checks":
                if item.get("task_description") != PROMPT9_SMOKE_TASK:
                    failures.append(f"Prompt 9 smoke task text mismatch for {agent}")
                if str(item.get("model_used", "")).strip() == "":
                    failures.append(f"Prompt 9 smoke model is blank for {agent}")
                output = str(item.get("output", ""))
                if f"LOGGED: {agent} | completed |" not in output:
                    failures.append(f"Prompt 9 smoke output missing LOGGED confirmation for {agent}")

    recent_rows = data.get("recent_rows")
    if not isinstance(recent_rows, list):
        failures.append("Prompt 9 report missing recent_rows list")
        recent_rows = []
    if len(recent_rows) < 5:
        failures.append("Prompt 9 report does not include five latest log rows")
    for index, row in enumerate(recent_rows[:5], start=1):
        if not isinstance(row, dict):
            failures.append(f"Prompt 9 recent row {index} is not an object")
            continue
        for key in ("agent_name", "status", "model_used", "created_at"):
            if not str(row.get(key, "")).strip():
                failures.append(f"Prompt 9 recent row {index} missing {key}")
        if str(row.get("status", "")).strip() not in {"completed", "failed"}:
            failures.append(f"Prompt 9 recent row {index} has invalid status")
    return failures


def render_prompt9_markdown_report(data: dict[str, Any]) -> str:
    lines = [
        "# Prompt 9 Activity Logging Verification",
        "",
        f"- Status: {data.get('status', 'unknown')}",
        f"- Agent log DB: `{data.get('agent_log_db', '')}`",
        "",
        "## Memory Policy",
        "",
    ]
    for item in data.get("memory_checks", []):
        lines.append(f"- {item.get('agent')}: {'passed' if item.get('verified') else 'failed'}")
    lines.extend(["", "## Smoke Tests", ""])
    for item in data.get("log_smoke_checks", []):
        lines.append(
            f"- {item.get('agent')}: {'passed' if item.get('verified') else 'failed'}; "
            f"model={item.get('model_used', '')}; output={str(item.get('output', '')).strip()}"
        )
    lines.extend(["", "## Latest Five Log Rows", "", "| agent_name | status | model_used | created_at |", "| --- | --- | --- | --- |"])
    for row in data.get("recent_rows", [])[:5]:
        lines.append(
            f"| {row.get('agent_name', '')} | {row.get('status', '')} | "
            f"{row.get('model_used', '')} | {row.get('created_at', '')} |"
        )
    return "\n".join(lines).rstrip() + "\n"


def load_prompt9_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))
