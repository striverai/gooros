from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .constants import AGENTS
from .prompt6 import PROMPT6_BOUNDARY_TESTS, normalize


PROMPT7_MEMORY_MARKER = "shared team awareness and handoff Prompt 7 v1"
PROMPT7_SOUL_MARKER = "Prompt 7 shared team awareness handoff v1"
PROMPT7_REPORT_JSON = "prompt7-team-awareness-handoff-verification.json"
PROMPT7_REPORT_MARKDOWN = "prompt7-team-awareness-handoff-verification.md"

PROMPT7_TEAM_ROLES = {
    "owner": "Chủ sở hữu — có thể chỉ thị trực tiếp cho bất kỳ agent nào, bất cứ lúc nào.",
    "orchestrator": "Orchestrator — điều phối viên cấp cao nhất, Telegram.",
    "scout": "Scout — nghiên cứu và tìm nguồn.",
    "scribe": "Scribe — viết lách và biên tập.",
    "reach": "Reach — marketing, tăng trưởng, kiếm tiền.",
    "dev": "Dev — kỹ thuật, tự động hóa, tích hợp.",
}

PROMPT7_HANDOFF_TESTS = {
    agent: {
        "request": str(spec["request"]),
        "teammate": str(spec["teammate"]),
        "reason": str(spec["reason"]),
    }
    for agent, spec in PROMPT6_BOUNDARY_TESTS.items()
}


def prompt7_report_dir(project_dir: Path) -> Path:
    return project_dir / "reports"


def prompt7_report_json_path(project_dir: Path) -> Path:
    return prompt7_report_dir(project_dir) / PROMPT7_REPORT_JSON


def prompt7_report_markdown_path(project_dir: Path) -> Path:
    return prompt7_report_dir(project_dir) / PROMPT7_REPORT_MARKDOWN


def prompt7_teammate(agent: str) -> str:
    return str(PROMPT7_HANDOFF_TESTS[agent]["teammate"])


def prompt7_handoff_line(agent: str) -> str:
    return f"Đây là mảng của {prompt7_teammate(agent)}, đang chuyển việc này cho họ."


def render_prompt7_team_awareness() -> str:
    return "\n".join(f"- {line}" for line in PROMPT7_TEAM_ROLES.values())


def render_prompt7_policy(agent: str, workspace: Path) -> str:
    teammate = prompt7_teammate(agent)
    return f"""<!-- GOOROS-HERMES-MANAGED: {agent} {PROMPT7_SOUL_MARKER} -->
## Prompt 7 - nhận thức chung về đội ngũ và chuyển việc

{render_prompt7_team_awareness()}

- Nếu một nhiệm vụ chủ yếu thuộc chuyên môn agent khác, không âm thầm tự ôm việc và không từ chối cụt.
- Hãy nêu đúng đồng nghiệp và chuyển việc thật trong một dòng theo mẫu: `{prompt7_handoff_line(agent)}`
- Khi có thể chạy công cụ, chuyển việc bằng `python3 ~/.hermes/agents/_shared/handoff-task-local.py {agent} "<task>" --to {teammate.lower()}`; script này sẽ ghi log, dùng workspace riêng, và gọi đúng profile nhận việc.
- Nếu handoff thất bại, nói thẳng lỗi và trả lại blocker cho Orchestrator hoặc owner; không bịa rằng việc đã xong.
- Workspace hiện tại của bạn vẫn là `{workspace}`.
<!-- END GOOROS-HERMES-MANAGED: {agent} {PROMPT7_SOUL_MARKER} -->"""


def render_prompt7_memory(agent: str) -> str:
    return (
        f"Prompt 7 team awareness for {agent}: owner may directly instruct any agent anytime; "
        "Orchestrator is the senior Telegram coordinator; Scout researches and finds sources; "
        "Scribe writes and edits; Reach handles marketing, growth, and monetization; "
        "Dev handles engineering, automation, and integrations. "
        f"If work mainly belongs to {prompt7_teammate(agent)}, say exactly one clear handoff line like "
        f"`{prompt7_handoff_line(agent)}` and transfer through handoff-task-local.py instead of silently doing it or bluntly refusing."
    )


def validate_handoff_answer(agent: str, answer: str) -> list[str]:
    failures: list[str] = []
    stripped = answer.strip()
    teammate = prompt7_teammate(agent)
    normalized = normalize(stripped)
    if not stripped:
        return [f"{agent} returned an empty Prompt 7 handoff answer"]
    if "\n" in stripped or "\r" in stripped:
        failures.append(f"{agent} Prompt 7 handoff answer is not one line")
    if len(stripped) > 280:
        failures.append(f"{agent} Prompt 7 handoff answer is too long")
    if normalize(teammate) not in normalized:
        failures.append(f"{agent} Prompt 7 handoff answer does not name {teammate}")
    if "mang" not in normalized and "phan viec" not in normalized:
        failures.append(f"{agent} Prompt 7 handoff answer does not identify the teammate's domain")
    if "dang chuyen" not in normalized and "chuyen viec" not in normalized:
        failures.append(f"{agent} Prompt 7 handoff answer does not say the work is being transferred")
    return failures


def validate_prompt7_soul(agent: str, soul_text: str, workspace: Path) -> list[str]:
    failures: list[str] = []
    normalized = normalize(soul_text)
    if f"GOOROS-HERMES-MANAGED: {agent} {PROMPT7_SOUL_MARKER}" not in soul_text:
        failures.append(f"SOUL.md for {agent} missing Prompt 7 managed policy")
    for phrase in (
        "Prompt 7",
        "Chủ sở hữu",
        "Orchestrator",
        "Scout",
        "Scribe",
        "Reach",
        "Dev",
        "chuyển việc",
        "handoff-task-local.py",
    ):
        if normalize(phrase) not in normalized:
            failures.append(f"SOUL.md for {agent} missing Prompt 7 phrase: {phrase}")
    if str(workspace) not in soul_text:
        failures.append(f"SOUL.md for {agent} missing Prompt 7 workspace path")
    if normalize(prompt7_teammate(agent)) not in normalized:
        failures.append(f"SOUL.md for {agent} missing Prompt 7 handoff teammate")
    return failures


def validate_prompt7_memory(agent: str, memory_text: str) -> list[str]:
    marker = f"GOOROS-HERMES-MANAGED: {agent} {PROMPT7_MEMORY_MARKER}"
    failures: list[str] = []
    normalized = normalize(memory_text)
    if marker not in memory_text:
        failures.append(f"memory missing Prompt 7 team awareness seed for {agent}")
    for phrase in ("owner", "Orchestrator", "Scout", "Scribe", "Reach", "Dev", "handoff-task-local.py"):
        if normalize(phrase) not in normalized:
            failures.append(f"memory for {agent} missing Prompt 7 phrase: {phrase}")
    if normalize(prompt7_teammate(agent)) not in normalized:
        failures.append(f"memory for {agent} missing Prompt 7 teammate")
    return failures


def validate_prompt7_report(data: dict[str, Any], *, project_dir: Path) -> list[str]:
    failures: list[str] = []
    if data.get("prompt") != "Prompt 7":
        failures.append("Prompt 7 report has the wrong prompt label")
    if data.get("status") != "passed":
        failures.append("Prompt 7 report status is not passed")
    awareness_checks = data.get("team_awareness_checks")
    handoff_checks = data.get("handoff_checks")
    script_checks = data.get("handoff_script_checks")
    if not isinstance(awareness_checks, list):
        failures.append("Prompt 7 report missing team_awareness_checks list")
        awareness_checks = []
    if not isinstance(handoff_checks, list):
        failures.append("Prompt 7 report missing handoff_checks list")
        handoff_checks = []
    if not isinstance(script_checks, list):
        failures.append("Prompt 7 report missing handoff_script_checks list")
        script_checks = []
    for section, checks in (
        ("team awareness", awareness_checks),
        ("handoff", handoff_checks),
        ("handoff script", script_checks),
    ):
        seen = {str(item.get("agent", "")).strip() for item in checks if isinstance(item, dict)}
        missing = [agent for agent in AGENTS if agent not in seen]
        if missing:
            failures.append(f"Prompt 7 report missing {section} checks for: " + ", ".join(missing))
    for item in awareness_checks:
        if not isinstance(item, dict) or item.get("agent") not in AGENTS:
            continue
        agent = str(item["agent"])
        if not item.get("verified"):
            failures.append(f"Prompt 7 team awareness check did not pass for {agent}")
    for item in handoff_checks:
        if not isinstance(item, dict) or item.get("agent") not in AGENTS:
            continue
        agent = str(item["agent"])
        if not item.get("verified"):
            failures.append(f"Prompt 7 handoff answer check did not pass for {agent}")
        for issue in validate_handoff_answer(agent, str(item.get("answer", ""))):
            failures.append(f"Prompt 7 handoff answer invalid for {agent}: {issue}")
    for item in script_checks:
        if not isinstance(item, dict) or item.get("agent") not in AGENTS:
            continue
        agent = str(item["agent"])
        if not item.get("verified"):
            failures.append(f"Prompt 7 handoff script check did not pass for {agent}")
        expected_target = prompt7_teammate(agent).lower()
        actual_target = str(item.get("target", "")).strip().lower()
        if actual_target != expected_target:
            failures.append(f"Prompt 7 handoff script target mismatch for {agent}: expected {expected_target}, got {actual_target}")
    return failures


def render_prompt7_markdown_report(data: dict[str, Any]) -> str:
    lines = [
        "# Prompt 7 Team Awareness And Handoff Verification",
        "",
        f"- Status: {data.get('status', 'unknown')}",
        "",
        "## Team Awareness",
        "",
    ]
    for item in data.get("team_awareness_checks", []):
        lines.append(f"- {item.get('agent')}: {'passed' if item.get('verified') else 'failed'}")
    lines.extend(["", "## Handoff Answers", ""])
    for item in data.get("handoff_checks", []):
        lines.append(f"- {item.get('agent')} -> {item.get('expected_teammate')}: {str(item.get('answer', '')).strip()}")
    lines.extend(["", "## Handoff Script", ""])
    for item in data.get("handoff_script_checks", []):
        lines.append(f"- {item.get('agent')} -> {item.get('target')}: returncode={item.get('returncode')}")
    return "\n".join(lines).rstrip() + "\n"


def load_prompt7_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))
