from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PROMPT11_MEMORY_MARKER = "telegram topic routing plan Prompt 11 v1"
PROMPT11_SOUL_MARKER = "Prompt 11 Telegram command centre topic routing plan v1"
PROMPT11_REPORT_JSON = "prompt11-telegram-topic-routing-verification.json"
PROMPT11_REPORT_MARKDOWN = "prompt11-telegram-topic-routing-verification.md"
PROMPT11_CHANNELS = {
    "command": "orchestrator",
    "scout": "scout",
    "scribe": "scribe",
    "reach": "reach",
    "dev": "dev",
}


def prompt11_report_dir(project_dir: Path) -> Path:
    return project_dir / "reports"


def prompt11_report_json_path(project_dir: Path) -> Path:
    return prompt11_report_dir(project_dir) / PROMPT11_REPORT_JSON


def prompt11_report_markdown_path(project_dir: Path) -> Path:
    return prompt11_report_dir(project_dir) / PROMPT11_REPORT_MARKDOWN


def render_prompt11_restatement() -> str:
    return (
        "Chung ta dang xay mot command centre tren Telegram voi 5 topic: #command cho Orchestrator, "
        "#scout cho Scout, #scribe cho Scribe, #reach cho Reach va #dev cho Dev. "
        "Moi tin nhan trong topic se duoc route truc tiep sang dung Hermes profile/root agent tuong ung, "
        "de agent that tra loi bang identity, memory, tools va workspace rieng. "
        "Cach route theo topic tot hon viec Orchestrator chuyen tiep vi no tranh bien Orchestrator thanh nut that co chai, "
        "giam vong lap trung gian, va giu context cua tung agent khong bi tron lan."
    )


def render_prompt11_policy(workspace: Path) -> str:
    return f"""<!-- GOOROS-HERMES-MANAGED: orchestrator {PROMPT11_SOUL_MARKER} -->
## Prompt 11 - Telegram command centre topic routing plan

- Ke hoach can duoc giai thich lai truoc khi bat dau thao tac topic: {render_prompt11_restatement()}
- Topic bat buoc: #command -> Orchestrator root/default agent, #scout -> Scout, #scribe -> Scribe, #reach -> Reach, #dev -> Dev.
- Telegram chi co mot bot identity; Gooros tranh viec Orchestrator chuyen tiep moi thu bang cach route theo `thread_id` sang dung profile that.
- Specialist profile phai la Hermes profile ben vung voi memory, tools, SOUL va workspace rieng; Orchestrator khong duoc tao thanh profile moi.
- Plugin `telegram_topic_profiles` phai doc thread id, map topic sang profile/root agent, bat `multiplex_profiles`, va restart gateway truoc khi live.
- Buoc xac minh cuoi cung: tung topic duoc route den dung agent that; #command khong set specialist profile va de Orchestrator root tra loi.
- Workspace Orchestrator van la `{workspace}`.
<!-- END GOOROS-HERMES-MANAGED: orchestrator {PROMPT11_SOUL_MARKER} -->"""


def render_prompt11_memory() -> str:
    return (
        "Prompt 11 Telegram topic routing plan: "
        f"{render_prompt11_restatement()} "
        "Required mapping is #command->orchestrator, #scout->scout, #scribe->scribe, #reach->reach, #dev->dev. "
        "Use telegram_topic_profiles with thread_id routing and Hermes multiplex_profiles=true. "
        "For #command, leave the Telegram source on the root/default Orchestrator instead of routing to a separate orchestrator profile."
    )


def validate_prompt11_soul(soul_text: str, workspace: Path) -> list[str]:
    failures: list[str] = []
    if f"GOOROS-HERMES-MANAGED: orchestrator {PROMPT11_SOUL_MARKER}" not in soul_text:
        failures.append("Orchestrator SOUL missing Prompt 11 managed topic-routing plan")
    for phrase in (
        "#command",
        "#scout",
        "#scribe",
        "#reach",
        "#dev",
        "thread_id",
        "telegram_topic_profiles",
        "multiplex_profiles",
        "Orchestrator root",
        "khong duoc tao thanh profile moi",
    ):
        if phrase not in soul_text:
            failures.append(f"Orchestrator SOUL missing Prompt 11 phrase: {phrase}")
    if str(workspace) not in soul_text:
        failures.append("Orchestrator SOUL missing Prompt 11 workspace path")
    return failures


def validate_prompt11_memory(memory_text: str) -> list[str]:
    failures: list[str] = []
    if f"GOOROS-HERMES-MANAGED: orchestrator {PROMPT11_MEMORY_MARKER}" not in memory_text:
        failures.append("Orchestrator memory missing Prompt 11 routing plan seed")
    for phrase in ("#command->orchestrator", "#scout->scout", "#scribe->scribe", "#reach->reach", "#dev->dev", "multiplex_profiles=true"):
        if phrase not in memory_text:
            failures.append(f"Orchestrator memory missing Prompt 11 phrase: {phrase}")
    return failures


def expected_topic_routes(config: Any) -> dict[str, str]:
    return {
        str(config.thread_scout): "scout",
        str(config.thread_scribe): "scribe",
        str(config.thread_reach): "reach",
        str(config.thread_dev): "dev",
    }


def validate_topic_routes(topic_map: dict[str, Any], config: Any) -> list[str]:
    failures: list[str] = []
    expected = expected_topic_routes(config)
    for thread_id, agent in expected.items():
        if not thread_id:
            failures.append(f"Prompt 11 missing thread id for {agent}")
        elif str(topic_map.get(thread_id, "")).strip() != agent:
            failures.append(f"Prompt 11 topic route mismatch for thread {thread_id}: expected {agent}, got {topic_map.get(thread_id)!r}")
    command_thread = str(getattr(config, "thread_command", "") or "")
    if command_thread and command_thread in {str(key) for key in topic_map}:
        failures.append("Prompt 11/17 topic map must omit #command so Orchestrator remains the root/default agent")
    if "orchestrator" in {str(value).strip().lower() for value in topic_map.values()}:
        failures.append("Prompt 11/17 topic map must not route a topic to orchestrator")
    extras = {str(key): str(value) for key, value in topic_map.items() if str(value) not in set(PROMPT11_CHANNELS.values())}
    if extras:
        failures.append("Prompt 11 topics map contains unknown agent targets: " + ", ".join(f"{k}->{v}" for k, v in sorted(extras.items())))
    return failures


def validate_prompt11_report(data: dict[str, Any], *, config: Any) -> list[str]:
    failures: list[str] = []
    if data.get("prompt") != "Prompt 11":
        failures.append("Prompt 11 report has the wrong prompt label")
    if data.get("status") != "passed":
        failures.append("Prompt 11 report status is not passed")
    routes = data.get("topic_routes")
    if not isinstance(routes, dict):
        failures.append("Prompt 11 report missing topic_routes object")
        routes = {}
    failures.extend(validate_topic_routes(routes, config))
    route_checks = data.get("route_checks")
    if not isinstance(route_checks, list):
        failures.append("Prompt 11 report missing route_checks list")
        route_checks = []
    seen = {str(item.get("channel", "")).strip() for item in route_checks if isinstance(item, dict)}
    for channel in PROMPT11_CHANNELS:
        if channel not in seen:
            failures.append(f"Prompt 11 report missing route check for #{channel}")
    for item in route_checks:
        if not isinstance(item, dict):
            continue
        channel = str(item.get("channel", ""))
        expected = PROMPT11_CHANNELS.get(channel)
        if expected and item.get("expected_agent") != expected:
            failures.append(f"Prompt 11 route check expected_agent mismatch for #{channel}")
        if not item.get("verified"):
            failures.append(f"Prompt 11 route check did not pass for #{channel}")
    if not data.get("multiplex_profiles_enabled"):
        failures.append("Prompt 11 report does not confirm multiplex_profiles=true")
    if not data.get("plugin_installed"):
        failures.append("Prompt 11 report does not confirm telegram_topic_profiles is installed")
    return failures


def render_prompt11_markdown_report(data: dict[str, Any]) -> str:
    lines = [
        "# Prompt 11 Telegram Topic Routing Verification",
        "",
        f"- Status: {data.get('status', 'unknown')}",
        f"- Plugin installed: {data.get('plugin_installed', False)}",
        f"- multiplex_profiles: {data.get('multiplex_profiles_enabled', False)}",
        "",
        "## Topic Routes",
        "",
    ]
    routes = data.get("topic_routes", {})
    if isinstance(routes, dict):
        for thread_id, agent in routes.items():
            lines.append(f"- `{thread_id}` -> `{agent}`")
    lines.extend(["", "## Route Checks", ""])
    for item in data.get("route_checks", []):
        lines.append(
            f"- #{item.get('channel')}: expected `{item.get('expected_agent')}`, "
            f"actual `{item.get('actual_profile')}`, verified={item.get('verified')}"
        )
    return "\n".join(lines).rstrip() + "\n"


def load_prompt11_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))
