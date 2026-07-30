from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PROMPT16_REPORT_JSON = "prompt16-multi-agent-activation-verification.json"
PROMPT16_REPORT_MARKDOWN = "prompt16-multi-agent-activation-verification.md"

PROMPT16_PLUGIN = "telegram_topic_profiles"
PROMPT16_ENABLE_COMMAND = ["hermes", "plugins", "enable", PROMPT16_PLUGIN]
PROMPT16_CONFIG_SET_COMMAND = ["hermes", "config", "set", "multiplex_profiles", "true"]
PROMPT16_GATEWAY_RESTART_COMMAND = ["hermes", "gateway", "restart"]
PROMPT16_PLUGINS_LIST_COMMAND = ["hermes", "plugins", "list"]
PROMPT16_CONFIG_GET_COMMAND = ["hermes", "config", "get", "multiplex_profiles"]
PROMPT16_GATEWAY_STATUS_COMMAND = ["hermes", "gateway", "status", "--deep"]


def prompt16_report_dir(project_dir: Path) -> Path:
    return project_dir / "reports"


def prompt16_report_json_path(project_dir: Path) -> Path:
    return prompt16_report_dir(project_dir) / PROMPT16_REPORT_JSON


def prompt16_report_markdown_path(project_dir: Path) -> Path:
    return prompt16_report_dir(project_dir) / PROMPT16_REPORT_MARKDOWN


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y", "on", "enabled", "active"}


def _falsey(value: Any) -> bool:
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value == 0
    text = str(value).strip().lower()
    return text in {"0", "false", "no", "n", "off", "disabled", "inactive"}


def _json_plugin_enabled(data: Any, plugin: str) -> bool | None:
    plugin_l = plugin.lower()
    if isinstance(data, list):
        for item in data:
            found = _json_plugin_enabled(item, plugin)
            if found is not None:
                return found
        return None
    if not isinstance(data, dict):
        return None
    for collection_key in ("plugins", "items", "data", "results"):
        if collection_key in data:
            found = _json_plugin_enabled(data[collection_key], plugin)
            if found is not None:
                return found
    if plugin in data:
        value = data[plugin]
        if isinstance(value, dict):
            for status_key in ("enabled", "active", "loaded", "status", "state"):
                if status_key in value:
                    if _falsey(value[status_key]):
                        return False
                    if _truthy(value[status_key]):
                        return True
            return True
        return _truthy(value)
    name = str(data.get("name") or data.get("id") or data.get("plugin") or "").strip().lower()
    if name == plugin_l:
        for status_key in ("enabled", "active", "loaded", "status", "state"):
            if status_key in data:
                if _falsey(data[status_key]):
                    return False
                if _truthy(data[status_key]):
                    return True
        return True
    return None


def parse_plugin_enabled(output: str, plugin: str = PROMPT16_PLUGIN) -> bool:
    text = str(output or "").strip()
    if not text:
        return False
    try:
        found = _json_plugin_enabled(json.loads(text), plugin)
        if found is not None:
            return found
    except Exception:
        pass
    plugin_l = plugin.lower()
    for raw in text.splitlines():
        line = raw.strip().lower()
        if plugin_l not in line:
            continue
        if any(marker in line for marker in ("disabled", "inactive", "false", "off", "not enabled")):
            continue
        if any(marker in line for marker in ("enabled", "active", "true", "on", "yes", "✓", "✔", "[x]")):
            return True
    return False


def parse_multiplex_profiles_enabled(output: str) -> bool:
    text = str(output or "").strip()
    if not text:
        return False
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            for key in ("multiplex_profiles", "value", "enabled"):
                if key in data:
                    return _truthy(data[key]) and not _falsey(data[key])
        return _truthy(data)
    except Exception:
        pass
    lowered = text.lower()
    if lowered in {"true", "1", "yes", "on", "enabled"}:
        return True
    for line in lowered.splitlines():
        if "multiplex_profiles" in line and "true" in line:
            return True
    return False


def validate_prompt16_report(data: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if data.get("prompt") != "Prompt 16":
        failures.append("Prompt 16 report has the wrong prompt label")
    if data.get("status") != "passed":
        failures.append("Prompt 16 report status is not passed")
    if data.get("plugin_enable_command") != PROMPT16_ENABLE_COMMAND:
        failures.append("Prompt 16 did not run the exact plugin enable command")
    if data.get("config_set_command") != PROMPT16_CONFIG_SET_COMMAND:
        failures.append("Prompt 16 did not run the exact multiplex_profiles config command")
    if data.get("plugins_list_command") != PROMPT16_PLUGINS_LIST_COMMAND:
        failures.append("Prompt 16 did not verify with hermes plugins list")
    if data.get("config_get_command") != PROMPT16_CONFIG_GET_COMMAND:
        failures.append("Prompt 16 did not verify multiplex_profiles with hermes config get")
    if data.get("gateway_status_command") != PROMPT16_GATEWAY_STATUS_COMMAND:
        failures.append("Prompt 16 did not verify gateway health with hermes gateway status --deep")
    if not data.get("plugin_present_before_enable"):
        failures.append("Prompt 16 did not confirm the routing plugin was placed before enabling")
    if not data.get("plugin_enable_attempted"):
        failures.append("Prompt 16 did not attempt plugin enable")
    if not data.get("plugin_enabled"):
        failures.append("Prompt 16 did not confirm telegram_topic_profiles is enabled")
    if not data.get("multiplex_set_attempted"):
        failures.append("Prompt 16 did not attempt multiplex_profiles=true")
    if not data.get("multiplex_profiles_enabled"):
        failures.append("Prompt 16 did not confirm multiplex_profiles=true")
    attempts = data.get("gateway_restart_attempts")
    if not isinstance(attempts, list) or not attempts:
        failures.append("Prompt 16 did not attempt gateway restart")
    else:
        for item in attempts:
            if not isinstance(item, dict) or item.get("command") != PROMPT16_GATEWAY_RESTART_COMMAND:
                failures.append("Prompt 16 gateway restart attempt did not use hermes gateway restart")
                break
    if not data.get("gateway_restarted"):
        failures.append("Prompt 16 did not confirm gateway restart success")
    if not data.get("gateway_status_ok"):
        failures.append("Prompt 16 did not confirm gateway is healthy after restart")
    if data.get("specialist_scrub_attempted") and not data.get("specialist_profiles_isolated_after_scrub"):
        failures.append("Prompt 16 scrubbed specialist profiles but did not confirm isolation after scrub")
    return failures


def render_prompt16_markdown_report(data: dict[str, Any]) -> str:
    lines = [
        "# Prompt 16 Multi-Agent Activation Verification",
        "",
        f"- Status: {data.get('status', 'unknown')}",
        f"- Plugin present before enable: {data.get('plugin_present_before_enable', False)}",
        f"- Plugin enabled: {data.get('plugin_enabled', False)}",
        f"- multiplex_profiles: {data.get('multiplex_profiles_enabled', False)}",
        f"- Gateway restarted: {data.get('gateway_restarted', False)}",
        f"- Gateway healthy: {data.get('gateway_status_ok', False)}",
        f"- Specialist scrub attempted: {data.get('specialist_scrub_attempted', False)}",
        "",
        "## Commands",
        "",
        f"- Enable plugin: `{' '.join(data.get('plugin_enable_command') or [])}`",
        f"- Set multiplex: `{' '.join(data.get('config_set_command') or [])}`",
        f"- List plugins: `{' '.join(data.get('plugins_list_command') or [])}`",
        f"- Get multiplex: `{' '.join(data.get('config_get_command') or [])}`",
        f"- Gateway status: `{' '.join(data.get('gateway_status_command') or [])}`",
        "",
        "## Gateway Restart Attempts",
        "",
    ]
    for item in data.get("gateway_restart_attempts", []) or []:
        lines.append(f"- `{item.get('reason', 'attempt')}`: returncode={item.get('returncode')}")
    scrubbed = data.get("specialist_scrubbed") or {}
    if scrubbed:
        lines.extend(["", "## Specialist Scrub", ""])
        for agent, item in scrubbed.items():
            lines.append(
                f"- `{agent}`: config_removed={item.get('removed_config_keys', [])}, "
                f"env_removed={item.get('removed_env_keys', [])}, verified={item.get('verified')}"
            )
    errors = data.get("errors") or []
    if errors:
        lines.extend(["", "## Errors", ""])
        for error in errors:
            lines.append(f"- {error}")
    return "\n".join(lines).rstrip() + "\n"


def load_prompt16_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))
