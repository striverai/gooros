from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


PROMPT10_RETENTION_DAYS = 7
PROMPT10_REPORT_JSON = "prompt10-log-retention-verification.json"
PROMPT10_REPORT_MARKDOWN = "prompt10-log-retention-verification.md"


def prompt10_report_dir(project_dir: Path) -> Path:
    return project_dir / "reports"


def prompt10_report_json_path(project_dir: Path) -> Path:
    return prompt10_report_dir(project_dir) / PROMPT10_REPORT_JSON


def prompt10_report_markdown_path(project_dir: Path) -> Path:
    return prompt10_report_dir(project_dir) / PROMPT10_REPORT_MARKDOWN


def prompt10_cron_line(project_dir: Path, hermes_home: Path) -> str:
    db = project_dir / "agent-logs.db"
    script = hermes_home / "agents" / "_shared" / "cleanup-logs.sh"
    return f"0 3 * * 0 AGENT_LOG_DB={db} bash {script} >/tmp/gooros-cleanup-logs.log 2>&1"


def parse_cleanup_summary(output: str) -> dict[str, int]:
    match = re.search(r"deleted=(\d+)\s+remaining=(\d+)\s+retention_days=(\d+)", output.strip())
    if not match:
        raise ValueError("cleanup output does not contain deleted/remaining/retention_days summary")
    return {
        "deleted": int(match.group(1)),
        "remaining": int(match.group(2)),
        "retention_days": int(match.group(3)),
    }


def validate_prompt10_script(script_text: str) -> list[str]:
    failures: list[str] = []
    if "RETENTION_DAYS=7" not in script_text:
        failures.append("cleanup-logs.sh does not set RETENTION_DAYS=7 at the top")
    if "AGENT_LOG_DB:-$HOME/agent-mission-control/agent-logs.db" not in script_text:
        failures.append("cleanup-logs.sh does not default AGENT_LOG_DB to ~/agent-mission-control/agent-logs.db")
    if "os.makedirs" not in script_text:
        failures.append("cleanup-logs.sh does not create the DB parent directory")
    if "CREATE TABLE IF NOT EXISTS agent_logs" not in script_text:
        failures.append("cleanup-logs.sh does not create agent_logs if missing")
    for index_name in ("idx_agent_logs_agent_name", "idx_agent_logs_status", "idx_agent_logs_created_at"):
        if index_name not in script_text:
            failures.append(f"cleanup-logs.sh missing index creation: {index_name}")
    if "DELETE FROM agent_logs WHERE created_at < ?" not in script_text:
        failures.append("cleanup-logs.sh does not delete rows older than the retention cutoff")
    if "VACUUM" not in script_text:
        failures.append("cleanup-logs.sh does not run VACUUM")
    if "deleted=" not in script_text or "remaining=" not in script_text:
        failures.append("cleanup-logs.sh does not print deleted and remaining row counts")
    if "import sqlite3" not in script_text:
        failures.append("cleanup-logs.sh does not use Python sqlite3 stdlib")
    if "pip " in script_text or "import pip" in script_text:
        failures.append("cleanup-logs.sh must not use pip")
    return failures


def validate_prompt10_report(data: dict[str, Any], *, project_dir: Path, hermes_home: Path) -> list[str]:
    failures: list[str] = []
    if data.get("prompt") != "Prompt 10":
        failures.append("Prompt 10 report has the wrong prompt label")
    if data.get("status") != "passed":
        failures.append("Prompt 10 report status is not passed")
    expected_cron = prompt10_cron_line(project_dir, hermes_home)
    if data.get("cron_line") != expected_cron:
        failures.append("Prompt 10 report cron_line does not match the installed weekly cleanup cron line")
    output = str(data.get("cleanup_output", "")).strip()
    try:
        summary = parse_cleanup_summary(output)
    except Exception as exc:
        failures.append(f"Prompt 10 cleanup output invalid: {exc}")
        summary = {}
    if summary.get("retention_days") != PROMPT10_RETENTION_DAYS:
        failures.append("Prompt 10 cleanup summary does not use 7 retention days")
    for key in ("deleted", "remaining", "retention_days"):
        if key not in data:
            failures.append(f"Prompt 10 report missing {key}")
            continue
        try:
            value = int(data[key])
        except Exception:
            failures.append(f"Prompt 10 report {key} is not an integer")
            continue
        if key in summary and value != summary[key]:
            failures.append(f"Prompt 10 report {key} does not match cleanup output")
    script_checks = data.get("script_checks")
    if not isinstance(script_checks, list):
        failures.append("Prompt 10 report missing script_checks list")
    elif script_checks:
        failures.append("Prompt 10 report has script check failures: " + "; ".join(str(item) for item in script_checks[:5]))
    return failures


def render_prompt10_markdown_report(data: dict[str, Any]) -> str:
    lines = [
        "# Prompt 10 Log Retention Verification",
        "",
        f"- Status: {data.get('status', 'unknown')}",
        f"- Retention days: {data.get('retention_days', '')}",
        f"- Deleted rows: {data.get('deleted', '')}",
        f"- Remaining rows: {data.get('remaining', '')}",
        f"- Cleanup output: `{str(data.get('cleanup_output', '')).strip()}`",
        "",
        "## Cron",
        "",
        "```cron",
        str(data.get("cron_line", "")),
        "```",
    ]
    script_checks = data.get("script_checks", [])
    if script_checks:
        lines.extend(["", "## Script Check Failures", ""])
        for item in script_checks:
            lines.append(f"- {item}")
    return "\n".join(lines).rstrip() + "\n"


def load_prompt10_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))
