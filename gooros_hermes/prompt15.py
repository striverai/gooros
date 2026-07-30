from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PROMPT15_REPORT_JSON = "prompt15-routing-plugin-creation-verification.json"
PROMPT15_REPORT_MARKDOWN = "prompt15-routing-plugin-creation-verification.md"

PROMPT15_PLUGIN_YAML = """name: telegram_topic_profiles
version: 1.0.0
description: "Route Telegram forum topics to Hermes profiles."
author: chủ sở hữu
kind: standalone
"""

PROMPT15_INIT_PY = '''"""Route Telegram forum topics to Hermes profiles (out-of-tree, update-safe)."""
from __future__ import annotations
import json, logging
from pathlib import Path

logger = logging.getLogger(__name__)
_MAP_PATH = Path(__file__).with_name("topics.json")

def _load_map():
    try:
        data = json.loads(_MAP_PATH.read_text())
        chat_id = str(data.get("chat_id", "")).strip()
        topics = {str(k): str(v) for k, v in (data.get("topics") or {}).items()}
        return chat_id, topics
    except FileNotFoundError:
        return "", {}
    except Exception as e:
        logger.warning("telegram_topic_profiles: bad topics.json: %s", e)
        return "", {}

def _route(**kwargs):
    event = kwargs.get("event")
    source = getattr(event, "source", None)
    if source is None:
        return None
    pval = getattr(getattr(source, "platform", None), "value", getattr(source, "platform", None))
    if str(pval).lower() != "telegram":
        return None
    thread_id = getattr(source, "thread_id", None)
    if not thread_id or getattr(source, "profile", None):
        return None
    map_chat, topics = _load_map()
    chat_id = getattr(source, "chat_id", None)
    if map_chat and chat_id and str(chat_id) != map_chat:
        return None
    profile = topics.get(str(thread_id))
    if profile:
        source.profile = profile
        logger.info("telegram_topic_profiles: thread %s -> %s", thread_id, profile)
    return None

def register(ctx) -> None:
    ctx.register_hook("pre_gateway_dispatch", _route)
'''


def prompt15_report_dir(project_dir: Path) -> Path:
    return project_dir / "reports"


def prompt15_report_json_path(project_dir: Path) -> Path:
    return prompt15_report_dir(project_dir) / PROMPT15_REPORT_JSON


def prompt15_report_markdown_path(project_dir: Path) -> Path:
    return prompt15_report_dir(project_dir) / PROMPT15_REPORT_MARKDOWN


def prompt15_plugin_dir(hermes_home: Path) -> Path:
    return hermes_home / "plugins" / "telegram_topic_profiles"


def render_prompt15_topics(config: Any) -> dict[str, Any]:
    return {
        "chat_id": str(config.telegram_chat_id),
        "topics": {
            str(config.thread_scout): "scout",
            str(config.thread_scribe): "scribe",
            str(config.thread_reach): "reach",
            str(config.thread_dev): "dev",
        },
    }


def _inside_hermes_agent(plugin_dir: Path, hermes_home: Path) -> bool:
    try:
        plugin_dir.resolve().relative_to((hermes_home / "hermes-agent").resolve())
        return True
    except ValueError:
        return False


def validate_prompt15_plugin(plugin_dir: Path, config: Any, *, hermes_home: Path) -> list[str]:
    failures: list[str] = []
    if _inside_hermes_agent(plugin_dir, hermes_home):
        failures.append("Prompt 15 plugin is inside hermes-agent; it must be out-of-tree under ~/.hermes/plugins")
    if not plugin_dir.exists():
        return [*failures, f"Prompt 15 plugin directory missing: {plugin_dir}"]
    expected_files = {"plugin.yaml", "topics.json", "__init__.py"}
    existing_files = {path.name for path in plugin_dir.iterdir() if path.is_file()}
    missing = sorted(expected_files - existing_files)
    extras = sorted(existing_files - expected_files)
    if missing:
        failures.append("Prompt 15 plugin missing files: " + ", ".join(missing))
    if extras:
        failures.append("Prompt 15 plugin contains extra files: " + ", ".join(extras))

    plugin_yaml = plugin_dir / "plugin.yaml"
    if plugin_yaml.exists():
        actual = plugin_yaml.read_text(encoding="utf-8", errors="replace")
        if actual != PROMPT15_PLUGIN_YAML:
            failures.append("Prompt 15 plugin.yaml is not the exact required minimal manifest")

    init_py = plugin_dir / "__init__.py"
    if init_py.exists():
        actual = init_py.read_text(encoding="utf-8", errors="replace")
        if actual != PROMPT15_INIT_PY:
            failures.append("Prompt 15 __init__.py is not the exact required minimal router")
        for forbidden in ("onboarding", "board_db", "handoff", "orchestrator"):
            if forbidden in actual.lower():
                failures.append(f"Prompt 15 __init__.py contains later-stage behavior: {forbidden}")

    topics_path = plugin_dir / "topics.json"
    if topics_path.exists():
        try:
            data = json.loads(topics_path.read_text(encoding="utf-8"))
            if str(data.get("chat_id", "")).strip() != str(config.telegram_chat_id):
                failures.append("Prompt 15 topics.json chat_id does not match configured Telegram group")
            topics = data.get("topics")
            if not isinstance(topics, dict):
                failures.append("Prompt 15 topics.json missing topics object")
                topics = {}
            expected_topics = render_prompt15_topics(config)["topics"]
            if {str(k): str(v) for k, v in topics.items()} != expected_topics:
                failures.append("Prompt 15 topics.json routes do not match the four specialist thread IDs exactly")
            if "orchestrator" in {str(value) for value in topics.values()}:
                failures.append("Prompt 15 topics.json must not include #command/orchestrator")
            for forbidden_key in ("board_db", "onboarding"):
                if forbidden_key in data:
                    failures.append(f"Prompt 15 topics.json contains later-stage key: {forbidden_key}")
        except Exception as exc:
            failures.append(f"Prompt 15 topics.json invalid: {exc}")
    return failures


def validate_prompt15_report(data: dict[str, Any], *, config: Any, hermes_home: Path) -> list[str]:
    failures: list[str] = []
    if data.get("prompt") != "Prompt 15":
        failures.append("Prompt 15 report has the wrong prompt label")
    if data.get("status") != "passed":
        failures.append("Prompt 15 report status is not passed")
    if data.get("plugin_enabled") is not False:
        failures.append("Prompt 15 report must confirm plugin was not enabled")
    if data.get("gateway_restarted") is not False:
        failures.append("Prompt 15 report must confirm gateway was not restarted")
    plugin_dir = Path(str(data.get("plugin_dir", ""))).expanduser()
    failures.extend(validate_prompt15_plugin(plugin_dir, config, hermes_home=hermes_home))
    return failures


def render_prompt15_markdown_report(data: dict[str, Any]) -> str:
    lines = [
        "# Prompt 15 Routing Plugin Creation Verification",
        "",
        f"- Status: {data.get('status', 'unknown')}",
        f"- Plugin dir: `{data.get('plugin_dir', '')}`",
        f"- plugin.yaml: {data.get('plugin_yaml_exists', False)}",
        f"- topics.json: {data.get('topics_json_exists', False)}",
        f"- __init__.py: {data.get('init_py_exists', False)}",
        f"- Plugin enabled: {data.get('plugin_enabled', None)}",
        f"- Gateway restarted: {data.get('gateway_restarted', None)}",
        "",
        "## Routes",
        "",
    ]
    routes = data.get("topics", {})
    if isinstance(routes, dict):
        for thread_id, agent in routes.items():
            lines.append(f"- `{thread_id}` -> `{agent}`")
    errors = data.get("errors") or []
    if errors:
        lines.extend(["", "## Errors", ""])
        for error in errors:
            lines.append(f"- {error}")
    return "\n".join(lines).rstrip() + "\n"


def load_prompt15_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))
