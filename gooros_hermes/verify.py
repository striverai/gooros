from __future__ import annotations

import json
import os
import shutil
import sqlite3
import urllib.request
from urllib.error import HTTPError
from pathlib import Path

from .constants import AGENTS, GOOROS_9ROUTER_COMBO_NAME, SPECIALISTS
from .paths import InstallPaths
from .prompt5 import (
    PROMPT5_MEMORY_MARKER,
    load_prompt5_report,
    prompt5_report_json_path,
    prompt5_report_markdown_path,
    validate_prompt5_report,
    validate_prompt5_soul,
)
from .prompt6 import (
    PROMPT6_MEMORY_MARKER,
    load_prompt6_report,
    prompt6_agent_workspace,
    prompt6_report_json_path,
    prompt6_report_markdown_path,
    validate_prompt6_report,
    validate_prompt6_soul,
)
from .prompt7 import (
    load_prompt7_report,
    prompt7_report_json_path,
    prompt7_report_markdown_path,
    validate_prompt7_memory,
    validate_prompt7_report,
    validate_prompt7_soul,
)
from .prompt9 import (
    load_prompt9_report,
    prompt9_report_json_path,
    prompt9_report_markdown_path,
    validate_prompt9_memory,
    validate_prompt9_report,
    validate_prompt9_soul,
)
from .prompt10 import (
    load_prompt10_report,
    prompt10_cron_line,
    prompt10_report_json_path,
    prompt10_report_markdown_path,
    validate_prompt10_report,
    validate_prompt10_script,
)
from .prompt11 import (
    load_prompt11_report,
    prompt11_report_json_path,
    prompt11_report_markdown_path,
    validate_prompt11_memory,
    validate_prompt11_report,
    validate_prompt11_soul,
    validate_topic_routes,
)
from .prompt12 import (
    load_prompt12_report,
    prompt12_report_json_path,
    prompt12_report_markdown_path,
    validate_prompt12_report,
)
from .prompt13 import (
    load_prompt13_report,
    prompt13_report_json_path,
    prompt13_report_markdown_path,
    validate_prompt13_report,
    validate_specialist_profile_isolation,
)
from .prompt16 import (
    load_prompt16_report,
    parse_multiplex_profiles_enabled,
    parse_plugin_enabled,
    prompt16_report_json_path,
    prompt16_report_markdown_path,
    validate_prompt16_report,
)
from .prompt17 import (
    load_prompt17_report,
    prompt17_report_json_path,
    prompt17_report_markdown_path,
    validate_prompt17_report,
)
from .prompt19 import discover_prompt19_sources
from .configstore import read_customer_files, read_env_values
from .proxy import verify_public_proxy
from .router_api import REQUIRED_FREE_PROVIDERS, get_router_settings, list_router_combos, list_router_keys
from .runner import Runner
from .tailscale import validate_prompt33_source
from .yaml_merge import validate_telegram_group_config_text


def check_http(url: str, timeout: int = 5, ok_statuses: tuple[int, ...] = (200,)) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status in ok_statuses, str(response.status)
    except HTTPError as exc:
        return exc.code in ok_statuses, str(exc.code)
    except Exception as exc:
        return False, str(exc)


def _router_models() -> tuple[list[str], str]:
    try:
        with urllib.request.urlopen("http://127.0.0.1:20128/v1/models", timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
        models = [m.get("id") for m in data.get("data", []) if isinstance(m, dict) and m.get("id")]
        return models, str(len(models))
    except Exception as exc:
        return [], str(exc)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def verify_agent_logs_db(db_path: Path, *, project_dir: Path, hermes_home: Path) -> list[str]:
    failures: list[str] = []
    resolved = db_path.expanduser().resolve()
    if not _is_relative_to(resolved, project_dir):
        failures.append(f"agent-logs.db is not inside the project directory: {resolved}")
    if _is_relative_to(resolved, hermes_home):
        failures.append(f"agent-logs.db must not be inside Hermes home: {resolved}")
    try:
        with sqlite3.connect(resolved) as conn:
            columns = conn.execute("PRAGMA table_info(agent_logs)").fetchall()
            indexes = conn.execute("PRAGMA index_list(agent_logs)").fetchall()
            expected = {
                "id": ("TEXT", 0, 1),
                "agent_name": ("TEXT", 1, 0),
                "task_description": ("TEXT", 1, 0),
                "model_used": ("TEXT", 0, 0),
                "status": ("TEXT", 1, 0),
                "created_at": ("TEXT", 1, 0),
            }
            actual = {str(row[1]): (str(row[2]).upper(), int(row[3]), int(row[5])) for row in columns}
            for name, spec in expected.items():
                if actual.get(name) != spec:
                    failures.append(f"agent_logs column mismatch for {name}: expected {spec}, got {actual.get(name)}")
            index_names = {str(row[1]) for row in indexes}
            for name in ("idx_agent_logs_agent_name", "idx_agent_logs_status", "idx_agent_logs_created_at"):
                if name not in index_names:
                    failures.append(f"agent_logs missing index: {name}")
            bad_statuses = [
                str(row[0])
                for row in conn.execute("SELECT DISTINCT status FROM agent_logs WHERE status NOT IN ('completed','failed') LIMIT 5").fetchall()
            ]
            if bad_statuses:
                failures.append("agent_logs contains invalid status values: " + ", ".join(bad_statuses))
            conn.execute("SELECT id, agent_name, task_description, model_used, status, created_at FROM agent_logs ORDER BY created_at DESC LIMIT 1").fetchone()
    except Exception as exc:
        failures.append(f"agent-logs.db invalid: {exc}")
    return failures


def scan_telegram_topic_plugin_log_failures(hermes_home: Path) -> list[str]:
    logs_dir = hermes_home / "logs"
    if not logs_dir.exists():
        return []
    failures: list[str] = []
    error_markers = ("traceback", "syntaxerror", "failed to load", "cannot import", "exception")
    for path in logs_dir.rglob("*"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "telegram_topic_profiles" not in text:
            continue
        lower = text.lower()
        if any(marker in lower for marker in error_markers):
            failures.append(f"telegram_topic_profiles has load/runtime errors in Hermes log: {path}")
    return failures


def verify_install(
    paths: InstallPaths,
    *,
    public: bool = False,
    with_9router: bool = False,
    auth_user: str = "",
    auth_password: str = "",
) -> list[str]:
    failures: list[str] = []
    config = read_customer_files(paths)
    for key in (
        "owner_name",
        "owner_work",
        "owner_focus",
        "owner_working_hours",
        "owner_important_people",
        "owner_cares_about",
        "timezone",
    ):
        if not getattr(config, key, ""):
            failures.append(f"customer owner profile missing required Prompt 1 field: {key}")
    root_soul = paths.hermes_home / "SOUL.md"
    if not root_soul.exists():
        failures.append("root SOUL.md missing; Orchestrator identity is not loaded by Hermes")
    else:
        root_soul_text = root_soul.read_text(encoding="utf-8", errors="replace")
        if "GOOROS-HERMES-MANAGED: orchestrator SOUL v1" not in root_soul_text:
            failures.append("root SOUL.md missing Gooros Orchestrator managed identity block")
        if "Ten cua ban la Orchestrator" not in root_soul_text:
            failures.append("root SOUL.md does not identify the default Hermes agent as Orchestrator")
        if config.owner_name and config.owner_name not in root_soul_text:
            failures.append("root SOUL.md does not include the configured owner name")
        if "Quy tac van hanh co dinh (Prompt 3)" not in root_soul_text:
            failures.append("root SOUL.md missing Prompt 3 fixed operating rules")
        if "[Agent]: Bước X/Y —" not in root_soul_text:
            failures.append("root SOUL.md missing Prompt 3 progress status format")
        failures.extend(validate_prompt6_soul("orchestrator", root_soul_text, prompt6_agent_workspace(paths.project_dir, "orchestrator")))
        failures.extend(validate_prompt7_soul("orchestrator", root_soul_text, prompt6_agent_workspace(paths.project_dir, "orchestrator")))
        failures.extend(validate_prompt9_soul("orchestrator", root_soul_text, prompt6_agent_workspace(paths.project_dir, "orchestrator")))
        failures.extend(validate_prompt11_soul(root_soul_text, prompt6_agent_workspace(paths.project_dir, "orchestrator")))
    audit_copy = paths.hermes_home / "GOOROS_ORCHESTRATOR.md"
    if not audit_copy.exists():
        failures.append("GOOROS_ORCHESTRATOR.md audit copy missing")
    memories = paths.hermes_home / "memories"
    user_memory = memories / "USER.md"
    team_memory = memories / "MEMORY.md"
    if not user_memory.exists() or "GOOROS-HERMES-MANAGED: owner profile USER v1" not in user_memory.read_text(encoding="utf-8", errors="replace"):
        failures.append("memories/USER.md missing managed owner profile seed")
    team_memory_text = team_memory.read_text(encoding="utf-8", errors="replace") if team_memory.exists() else ""
    if "GOOROS-HERMES-MANAGED: mission control team MEMORY v1" not in team_memory_text:
        failures.append("memories/MEMORY.md missing managed Mission Control team seed")
    if "GOOROS-HERMES-MANAGED: orchestrator operating rules Prompt 3 v1" not in team_memory_text:
        failures.append("memories/MEMORY.md missing Prompt 3 operating rules memory seed")
    if f"GOOROS-HERMES-MANAGED: orchestrator {PROMPT6_MEMORY_MARKER}" not in team_memory_text:
        failures.append("memories/MEMORY.md missing Prompt 6 Orchestrator memory seed")
    failures.extend(validate_prompt7_memory("orchestrator", team_memory_text))
    failures.extend(validate_prompt9_memory("orchestrator", team_memory_text))
    failures.extend(validate_prompt11_memory(team_memory_text))
    if (paths.hermes_home / "profiles" / "orchestrator").exists():
        failures.append("profiles/orchestrator exists; Orchestrator must remain the default Hermes agent")
    orchestrator_workspace = prompt6_agent_workspace(paths.project_dir, "orchestrator")
    if not orchestrator_workspace.exists():
        failures.append("private workspace missing for orchestrator")
    elif not (orchestrator_workspace / ".gooros-agent-workspace").exists():
        failures.append("private workspace marker missing for orchestrator")
    for agent in SPECIALISTS:
        root = paths.hermes_home / "profiles" / agent
        workspace = prompt6_agent_workspace(paths.project_dir, agent)
        if not root.exists():
            failures.append(f"profile missing: {agent}")
        soul = root / "SOUL.md"
        if not soul.exists():
            failures.append(f"SOUL.md missing for {agent}")
        else:
            soul_text = soul.read_text(encoding="utf-8", errors="replace")
            if config.owner_name and (config.owner_name not in soul_text or "Hồ sơ chủ sở hữu" not in soul_text):
                failures.append(f"SOUL.md for {agent} missing full owner context")
            failures.extend(validate_prompt5_soul(agent, soul_text, owner_name=config.owner_name or "the owner", workspace=workspace))
            failures.extend(validate_prompt6_soul(agent, soul_text, workspace))
            failures.extend(validate_prompt7_soul(agent, soul_text, workspace))
            failures.extend(validate_prompt9_soul(agent, soul_text, workspace))
        profile_memory = root / "memories" / "MEMORY.md"
        profile_user = root / "memories" / "USER.md"
        profile_memory_text = profile_memory.read_text(encoding="utf-8", errors="replace") if profile_memory.exists() else ""
        if f"GOOROS-HERMES-MANAGED: {agent} specialist profile Prompt 4 v1" not in profile_memory_text:
            failures.append(f"profile memory missing Prompt 4 seed for {agent}")
        if f"GOOROS-HERMES-MANAGED: {agent} {PROMPT5_MEMORY_MARKER}" not in profile_memory_text:
            failures.append(f"profile memory missing Prompt 5 identity seed for {agent}")
        if f"GOOROS-HERMES-MANAGED: {agent} {PROMPT6_MEMORY_MARKER}" not in profile_memory_text:
            failures.append(f"profile memory missing Prompt 6 role boundary seed for {agent}")
        failures.extend(validate_prompt7_memory(agent, profile_memory_text))
        failures.extend(validate_prompt9_memory(agent, profile_memory_text))
        if not profile_user.exists() or "GOOROS-HERMES-MANAGED: specialist owner profile USER v1" not in profile_user.read_text(encoding="utf-8", errors="replace"):
            failures.append(f"profile USER memory missing owner seed for {agent}")
        if not workspace.exists():
            failures.append(f"private workspace missing for {agent}")
        elif not (workspace / ".gooros-agent-workspace").exists():
            failures.append(f"private workspace marker missing for {agent}")
        cfg = root / "config.yaml"
        if cfg.exists() and "platforms:" in cfg.read_text(encoding="utf-8"):
            failures.append(f"profile {agent} still has platforms block")
        isolation = validate_specialist_profile_isolation(paths.hermes_home, agent)
        for error in isolation.get("errors", []):
            text = str(error)
            if text not in failures:
                failures.append(text)
    prompt5_json = prompt5_report_json_path(paths.project_dir)
    prompt5_markdown = prompt5_report_markdown_path(paths.project_dir)
    if not prompt5_json.exists():
        failures.append("Prompt 5 live identity verification report missing")
    else:
        try:
            failures.extend(validate_prompt5_report(load_prompt5_report(prompt5_json), project_dir=paths.project_dir, owner_name=config.owner_name or ""))
        except Exception as exc:
            failures.append(f"Prompt 5 live identity verification report invalid: {exc}")
    if not prompt5_markdown.exists():
        failures.append("Prompt 5 human-readable identity verification report missing")
    prompt6_json = prompt6_report_json_path(paths.project_dir)
    prompt6_markdown = prompt6_report_markdown_path(paths.project_dir)
    if not prompt6_json.exists():
        failures.append("Prompt 6 boundary/continuity verification report missing")
    else:
        try:
            failures.extend(validate_prompt6_report(load_prompt6_report(prompt6_json), project_dir=paths.project_dir))
        except Exception as exc:
            failures.append(f"Prompt 6 boundary/continuity verification report invalid: {exc}")
    if not prompt6_markdown.exists():
        failures.append("Prompt 6 human-readable boundary/continuity report missing")
    prompt7_json = prompt7_report_json_path(paths.project_dir)
    prompt7_markdown = prompt7_report_markdown_path(paths.project_dir)
    if not prompt7_json.exists():
        failures.append("Prompt 7 team-awareness/handoff verification report missing")
    else:
        try:
            failures.extend(validate_prompt7_report(load_prompt7_report(prompt7_json), project_dir=paths.project_dir))
        except Exception as exc:
            failures.append(f"Prompt 7 team-awareness/handoff verification report invalid: {exc}")
    if not prompt7_markdown.exists():
        failures.append("Prompt 7 human-readable team-awareness/handoff report missing")
    prompt9_json = prompt9_report_json_path(paths.project_dir)
    prompt9_markdown = prompt9_report_markdown_path(paths.project_dir)
    if not prompt9_json.exists():
        failures.append("Prompt 9 activity logging verification report missing")
    else:
        try:
            failures.extend(validate_prompt9_report(load_prompt9_report(prompt9_json), project_dir=paths.project_dir))
        except Exception as exc:
            failures.append(f"Prompt 9 activity logging verification report invalid: {exc}")
    if not prompt9_markdown.exists():
        failures.append("Prompt 9 human-readable activity logging report missing")
    prompt10_json = prompt10_report_json_path(paths.project_dir)
    prompt10_markdown = prompt10_report_markdown_path(paths.project_dir)
    if not prompt10_json.exists():
        failures.append("Prompt 10 log-retention verification report missing")
    else:
        try:
            failures.extend(validate_prompt10_report(load_prompt10_report(prompt10_json), project_dir=paths.project_dir, hermes_home=paths.hermes_home))
        except Exception as exc:
            failures.append(f"Prompt 10 log-retention verification report invalid: {exc}")
    if not prompt10_markdown.exists():
        failures.append("Prompt 10 human-readable log-retention report missing")
    prompt11_json = prompt11_report_json_path(paths.project_dir)
    prompt11_markdown = prompt11_report_markdown_path(paths.project_dir)
    if not prompt11_json.exists():
        failures.append("Prompt 11 Telegram topic-routing verification report missing")
    else:
        try:
            failures.extend(validate_prompt11_report(load_prompt11_report(prompt11_json), config=config))
        except Exception as exc:
            failures.append(f"Prompt 11 Telegram topic-routing verification report invalid: {exc}")
    if not prompt11_markdown.exists():
        failures.append("Prompt 11 human-readable Telegram topic-routing report missing")
    prompt12_json = prompt12_report_json_path(paths.project_dir)
    prompt12_markdown = prompt12_report_markdown_path(paths.project_dir)
    if not prompt12_json.exists():
        failures.append("Prompt 12 Telegram group-access verification report missing")
    else:
        try:
            failures.extend(validate_prompt12_report(load_prompt12_report(prompt12_json), config=config))
        except Exception as exc:
            failures.append(f"Prompt 12 Telegram group-access verification report invalid: {exc}")
    if not prompt12_markdown.exists():
        failures.append("Prompt 12 human-readable Telegram group-access report missing")
    prompt13_json = prompt13_report_json_path(paths.project_dir)
    prompt13_markdown = prompt13_report_markdown_path(paths.project_dir)
    if not prompt13_json.exists():
        failures.append("Prompt 13 specialist profile isolation verification report missing")
    else:
        try:
            failures.extend(validate_prompt13_report(load_prompt13_report(prompt13_json)))
        except Exception as exc:
            failures.append(f"Prompt 13 specialist profile isolation verification report invalid: {exc}")
    if not prompt13_markdown.exists():
        failures.append("Prompt 13 human-readable specialist profile isolation report missing")
    prompt16_json = prompt16_report_json_path(paths.project_dir)
    prompt16_markdown = prompt16_report_markdown_path(paths.project_dir)
    if not prompt16_json.exists():
        failures.append("Prompt 16 multi-agent activation verification report missing")
    else:
        try:
            failures.extend(validate_prompt16_report(load_prompt16_report(prompt16_json)))
        except Exception as exc:
            failures.append(f"Prompt 16 multi-agent activation verification report invalid: {exc}")
    if not prompt16_markdown.exists():
        failures.append("Prompt 16 human-readable multi-agent activation report missing")
    prompt17_json = prompt17_report_json_path(paths.project_dir)
    prompt17_markdown = prompt17_report_markdown_path(paths.project_dir)
    if not prompt17_json.exists():
        failures.append("Prompt 17 Telegram routing audit report missing")
    else:
        try:
            failures.extend(validate_prompt17_report(load_prompt17_report(prompt17_json)))
        except Exception as exc:
            failures.append(f"Prompt 17 Telegram routing audit report invalid: {exc}")
    if not prompt17_markdown.exists():
        failures.append("Prompt 17 human-readable Telegram routing audit report missing")
    shared = paths.hermes_home / "agents" / "_shared"
    for name in (
        "log-task-local.sh",
        "cleanup-logs.sh",
        "route_model.py",
        "route_and_run.sh",
        "orchestrate-task-local.py",
        "latest_session.py",
        "append-agent-memory.py",
        "handoff-task-local.py",
    ):
        if not (shared / name).exists():
            failures.append(f"shared runtime script missing: {name}")
    cleanup_script = shared / "cleanup-logs.sh"
    if cleanup_script.exists():
        cleanup_text = cleanup_script.read_text(encoding="utf-8", errors="replace")
        failures.extend(validate_prompt10_script(cleanup_text))
        if os.name == "posix" and not (cleanup_script.stat().st_mode & 0o111):
            failures.append("cleanup-logs.sh is not executable")
    route_script = shared / "route_and_run.sh"
    if route_script.exists():
        route_text = route_script.read_text(encoding="utf-8", errors="replace")
        if "GOOROS_AGENT_WORKSPACE_ROOT" not in route_text or "TERMINAL_CWD" not in route_text:
            failures.append("route_and_run.sh does not launch specialist profiles inside private workspaces")
        if "latest_session.py" not in route_text or "--resume" not in route_text:
            failures.append("route_and_run.sh does not resume latest agent sessions for Prompt 6 continuity")
        if "orchestrator" not in route_text or "GOOROS_AGENT_WORKSPACE" not in route_text:
            failures.append("route_and_run.sh does not route Orchestrator through its private workspace")
        if "handoff-task-local.py" not in route_text or "GOOROS_HANDOFF_ALREADY" not in route_text:
            failures.append("route_and_run.sh does not include Prompt 7 auto-handoff protection")
        if "new_tmp_file" not in route_text or "cat \"$out_file\"" not in route_text or "log-task-local.sh" not in route_text or ">/dev/null" not in route_text:
            failures.append("route_and_run.sh does not capture agent output, log Prompt 9 activity first, and hide logging chatter")
    if os.name == "posix":
        if not shutil.which("crontab"):
            failures.append("crontab missing; weekly log cleanup cannot be scheduled")
        else:
            result = Runner(verbose=False).run(["crontab", "-l"], capture=True, check=False, timeout=30)
            crontab_text = result.stdout or ""
            expected_cleanup_cron = prompt10_cron_line(paths.project_dir, paths.hermes_home)
            if "GOOROS-HERMES-MANAGED log cleanup" not in crontab_text:
                failures.append("weekly log cleanup crontab block missing")
            if expected_cleanup_cron not in crontab_text:
                failures.append("weekly log cleanup crontab line is not the exact Prompt 10 line")
    for name in ("server.py", "index.html"):
        if not (paths.project_dir / name).exists():
            failures.append(f"dashboard file missing: {name}")
    server = paths.project_dir / "server.py"
    if server.exists():
        server_text = server.read_text(encoding="utf-8", errors="replace")
        if 'HOST = "127.0.0.1"' not in server_text:
            failures.append("Mission Control server must bind only 127.0.0.1")
        for token in (
            "sqlite3.connect(path)",
            "BytesParser",
            "/api/upload",
            "shell=True",
        ):
            if token in server_text:
                failures.append(f"Mission Control server has a forbidden non-board write/control path: {token}")
        for token in (
            "def connect_board_rw",
            "CREATE TABLE IF NOT EXISTS tasks",
            "SEED_TASKS",
            "INSERT INTO tasks",
            "UPDATE tasks SET",
            "DELETE FROM tasks",
            'path == "/api/board"',
            'path == "/api/board/update"',
            'path == "/api/board/delete"',
            'path == "/api/chat/send"',
            'path == "/api/content/save"',
            'path == "/api/content/delete"',
            'path == "/api/cron/action"',
            'path == "/template"',
            "subprocess.Popen(",
            "subprocess.run(",
            "chat_command(agent",
            '[HERMES_BIN, "cron", action, job_id]',
            "TELEGRAM_HOME_CHANNEL",
            "CONTENT_DIR must not be inside HERMES_HOME",
        ):
            if token not in server_text:
                failures.append(f"Mission Control Prompt 23-30 support missing: {token}")
        for token in (
            "ACTIVE_AGENTS",
            "ACTIVE_AGENTS_LOCK",
            "working_agents_snapshot()",
            "set_agent_working(agent, True)",
            "set_agent_working(agent, False)",
            '"working_agents": active_agents',
            "Working directory:",
            "session_id:",
        ):
            if token not in server_text:
                failures.append(f"Mission Control Prompt 31/37 support missing: {token}")
        if '"channel": "telegram"' not in server_text:
            failures.append("Prompt 20 fleet metadata must use channel telegram for Orchestrator")
        if '"latency": "\\u2014"' not in server_text:
            failures.append("Prompt 20 fleet latency placeholder must be an em dash")
        if "mode=ro" not in server_text or "PRAGMA query_only=1" not in server_text:
            failures.append("Mission Control server must read SQLite with mode=ro and PRAGMA query_only=1")
        if "kanban.db" not in server_text:
            failures.append("Mission Control server does not expose read-only kanban.db discovery data")
    index = paths.project_dir / "index.html"
    if index.exists():
        text = index.read_text(encoding="utf-8", errors="replace")
        for token in (
            "DEMO_STATE",
            "DEMO_CHAT",
            "DEMO_CONTENT_DOCS",
            "DEMO_CONTENT_TEXT",
            "DEMO_GOOROS_CRON",
            "hard-coded reply",
            "Pulled 14 sources",
            "Routing directive #412",
            "Sweeping 14 sources",
            "node 0x9f",
            "Outline next week's video script",
            "claude-sonnet-4.5",
            "gemini-2.5-pro",
            "text-embed-3-large",
        ):
            if token in text:
                failures.append(f"dashboard still contains demo token: {token}")
        if "hydrate(); connectSSE(); startPolling();" not in text:
            failures.append("dashboard index is missing live hydrate/SSE bootstrap")
        for token in (
            "/api/upload",
            'method:"POST"',
        ):
            if token in text:
                failures.append(f"dashboard index is not read-only/live-safe; found {token}")
        for token in (
            "OFFICE empire (three.js)",
            "new THREE.WebGLRenderer",
            "WORKING_SERVER",
            "agentWorking(CODE_TO_PROFILE",
            "WORK_COL",
            "IDLE_COL",
            "b.windows.material.emissive.copy(col)",
            "b.accentMats.forEach(m => m.emissive.copy(col))",
        ):
            if token not in text:
                failures.append(f"dashboard index missing Prompt 31 3D active/idle support: {token}")
        if "OFFICE skyline (stdlib / no npm)" in text:
            failures.append("dashboard index still uses the 2D stdlib Office fallback instead of Prompt 31 3D Office")
        for token in (
            "/api/board",
            "/api/board/update",
            "/api/board/delete",
            "/api/chat/send",
            "/api/content/save",
            "/api/content/delete",
            "/api/cron/action",
            "method:'POST'",
        ):
            if token not in text:
                failures.append(f"dashboard index missing live Prompt 23-30 interaction: {token}")
    cli_path = Path(__file__).with_name("cli.py")
    tailscale_path = Path(__file__).with_name("tailscale.py")
    if server.exists() and cli_path.exists() and tailscale_path.exists():
        failures.extend(
            validate_prompt33_source(
                server.read_text(encoding="utf-8", errors="replace"),
                cli_path.read_text(encoding="utf-8", errors="replace"),
                tailscale_path.read_text(encoding="utf-8", errors="replace"),
            )
        )
    else:
        failures.append("Prompt 33 Tailscale source files are not all present")
    if not (paths.project_dir / "template.html").exists():
        failures.append("Prompt 23 /template design reference missing: template.html must remain in project")
    if not (paths.project_dir / "backups").exists():
        failures.append("Prompt 23 backups directory missing")
    scout_prompt29_notes = list((paths.project_dir / "content" / "scout").glob("*_prompt29-scout-research-note*.md"))
    if not scout_prompt29_notes:
        failures.append("Prompt 29 Scout research note missing from content/scout")
    prompt19 = discover_prompt19_sources(paths.hermes_home)
    if prompt19["files_created"] is not False or prompt19["read_only"] is not True:
        failures.append("Prompt 19 discovery contract must be read-only and create no files")
    if "mode=ro" not in prompt19["sqlite_read_mode"] or "PRAGMA query_only=1" not in prompt19["sqlite_read_mode"]:
        failures.append("Prompt 19 discovery does not declare SQLite mode=ro + PRAGMA query_only=1")
    for source in ("state_db", "kanban_db", "gateway_state"):
        if source not in prompt19:
            failures.append(f"Prompt 19 discovery missing {source}")
    ok, detail = check_http("http://127.0.0.1:51763/api/state")
    if not ok:
        failures.append(f"Mission Control /api/state not reachable: {detail}")
    failures.extend(verify_agent_logs_db(paths.project_dir / "agent-logs.db", project_dir=paths.project_dir, hermes_home=paths.hermes_home))
    board_db = paths.project_dir / "board.db"
    if board_db.exists():
        try:
            with sqlite3.connect(f"file:{board_db}?mode=ro", uri=True) as conn:
                conn.execute("PRAGMA query_only=1")
                conn.execute("SELECT COUNT(*) FROM tasks").fetchone()
        except Exception as exc:
            failures.append(f"board.db invalid: {exc}")
    telegram_env = read_env_values(paths.hermes_home / ".env")
    if not telegram_env.get("TELEGRAM_BOT_TOKEN"):
        failures.append("TELEGRAM_BOT_TOKEN missing from Hermes .env; Telegram bot cannot receive chat")
    home_channel = telegram_env.get("TELEGRAM_HOME_CHANNEL", "")
    if not home_channel.startswith("telegram:"):
        failures.append("TELEGRAM_HOME_CHANNEL missing or invalid; expected telegram:<chat_id>")
    config_path = paths.hermes_home / "config.yaml"
    if shutil.which("hermes"):
        result = Runner(verbose=False).run(["hermes", "config", "path"], capture=True, check=False, timeout=30)
        if result.returncode == 0 and result.stdout.strip():
            config_path = Path(result.stdout.strip()).expanduser()
    if not config_path.exists():
        failures.append(f"Hermes config path missing; Prompt 12 cannot confirm Telegram group access: {config_path}")
    elif config.telegram_chat_id:
        config_text = config_path.read_text(encoding="utf-8", errors="replace")
        failures.extend(validate_telegram_group_config_text(config_text, config.telegram_chat_id))
    plugin_dir = paths.hermes_home / "plugins" / "telegram_topic_profiles"
    if not (plugin_dir / "__init__.py").exists() or not (plugin_dir / "plugin.yaml").exists():
        failures.append("telegram_topic_profiles plugin is not installed")
    else:
        plugin_text = (plugin_dir / "__init__.py").read_text(encoding="utf-8", errors="replace")
        if "telegram onboarding owner profile v1" not in plugin_text or "_handle_onboarding" not in plugin_text:
            failures.append("telegram_topic_profiles plugin missing Telegram owner onboarding support")
        if "_prompt7_handoff_target" not in plugin_text or "Prompt 7 Telegram handoff" not in plugin_text:
            failures.append("telegram_topic_profiles plugin missing Prompt 7 Telegram handoff support")
        if "orchestrator root" not in plugin_text or "profile == \"orchestrator\"" not in plugin_text:
            failures.append("telegram_topic_profiles plugin missing Prompt 11 #command root-Orchestrator support")
        failures.extend(scan_telegram_topic_plugin_log_failures(paths.hermes_home))
    if shutil.which("hermes"):
        plugin_list = Runner(verbose=False).run(["hermes", "plugins", "list"], capture=True, check=False, timeout=30)
        if plugin_list.returncode != 0:
            failures.append("hermes plugins list failed; cannot confirm telegram_topic_profiles is enabled")
        elif not parse_plugin_enabled((plugin_list.stdout or "") + "\n" + (plugin_list.stderr or "")):
            failures.append("telegram_topic_profiles is not enabled according to hermes plugins list")
        multiplex = Runner(verbose=False).run(["hermes", "config", "get", "multiplex_profiles"], capture=True, check=False, timeout=30)
        if multiplex.returncode != 0:
            failures.append("hermes config get multiplex_profiles failed; cannot confirm multi-profile mode")
        elif not parse_multiplex_profiles_enabled((multiplex.stdout or "") + "\n" + (multiplex.stderr or "")):
            failures.append("multiplex_profiles is not true according to hermes config get")
    topics_path = plugin_dir / "topics.json"
    if topics_path.exists():
        try:
            topics_data = json.loads(topics_path.read_text(encoding="utf-8"))
            topic_map = topics_data.get("topics") if isinstance(topics_data, dict) else {}
            if not isinstance(topic_map, dict) or len([key for key, value in topic_map.items() if key and value]) != 4:
                failures.append("telegram_topic_profiles topics.json must contain exactly the four specialist Prompt 17 topic routes")
            elif config.thread_command:
                failures.extend(validate_topic_routes({str(k): str(v) for k, v in topic_map.items()}, config))
            board_db = str(topics_data.get("board_db", "")).strip() if isinstance(topics_data, dict) else ""
            if not board_db:
                failures.append("telegram_topic_profiles topics.json missing board_db; specialist Telegram tasks will not appear in dashboard Kanban")
            onboarding = topics_data.get("onboarding") if isinstance(topics_data, dict) else None
            if not isinstance(onboarding, dict) or str(onboarding.get("enabled", True)).strip().lower() in {"0", "false", "no", "off"}:
                failures.append("telegram_topic_profiles topics.json missing enabled owner onboarding config")
            else:
                state_raw = str(onboarding.get("state_path", "")).strip()
                memory_raw = str(onboarding.get("user_memory_path", "")).strip()
                for key in ("state_path", "owner_profile_path", "user_memory_path"):
                    if not str(onboarding.get(key, "")).strip():
                        failures.append(f"telegram_topic_profiles onboarding config missing {key}")
                if state_raw and memory_raw and Path(state_raw).expanduser().exists():
                    state_path = Path(state_raw).expanduser()
                    user_memory_path = Path(memory_raw).expanduser()
                    state_data = json.loads(state_path.read_text(encoding="utf-8"))
                    chats = state_data.get("chats") if isinstance(state_data, dict) else {}
                    completed = any(isinstance(item, dict) and item.get("status") == "completed" for item in (chats or {}).values())
                    if completed:
                        memory_text = user_memory_path.read_text(encoding="utf-8", errors="replace") if user_memory_path.exists() else ""
                        if "GOOROS-HERMES-MANAGED: telegram onboarding owner profile v1" not in memory_text:
                            failures.append("Telegram owner onboarding completed but USER.md missing onboarding memory entry")
        except Exception as exc:
            failures.append(f"telegram_topic_profiles topics.json invalid: {exc}")
    else:
        failures.append("telegram_topic_profiles topics.json missing")
    if shutil.which("hermes"):
        gateway_ok = False
        gateway_details: list[str] = []
        for cmd in (
            ["hermes", "gateway", "--accept-hooks", "status", "--deep"],
            ["hermes", "gateway", "--accept-hooks", "status", "--deep", "--system"],
        ):
            result = Runner(verbose=False).run(
                cmd,
                capture=True,
                check=False,
                timeout=45,
                env={"HERMES_ACCEPT_HOOKS": "1"},
            )
            if result.returncode == 0:
                gateway_ok = True
                break
            detail = (result.stderr or result.stdout or "").strip()
            if detail:
                gateway_details.append(detail)
        if not gateway_ok:
            failures.append("Hermes gateway service is not healthy; Telegram bot will not chat: " + " | ".join(gateway_details[:2]))
    if public and not shutil.which("caddy"):
        failures.append("public dashboard requested but caddy is not installed")
    if public:
        ok, detail = check_http("http://127.0.0.1:9119", ok_statuses=(200, 301, 302, 303, 307, 308, 401, 403))
        if not ok:
            failures.append(f"Hermes native dashboard upstream not reachable on 127.0.0.1:9119: {detail}")
        if auth_user:
            config.dash_user = auth_user
        if config.public_ip and shutil.which("curl"):
            failures.extend(verify_public_proxy(Runner(verbose=False), config, auth_password=auth_password))
        elif config.public_ip:
            failures.append("public dashboard requested but curl is not installed")
    if with_9router:
        ok, detail = check_http("http://127.0.0.1:20128/dashboard", ok_statuses=(200, 301, 302, 303, 307, 308, 401, 403))
        if not ok:
            failures.append(f"9Router dashboard upstream not reachable on 127.0.0.1:20128/dashboard: {detail}")
        models, detail = _router_models()
        if not models:
            failures.append(f"9Router /v1/models returned no usable models: {detail}")
        elif GOOROS_9ROUTER_COMBO_NAME not in models:
            failures.append(f"9Router /v1/models does not expose combo model: {GOOROS_9ROUTER_COMBO_NAME}")
        router_key = read_env_values(paths.secrets_env).get("GOOROS_9ROUTER_API_KEY", "")
        if not router_key:
            failures.append("GOOROS_9ROUTER_API_KEY missing; Hermes is not using a real 9Router API key")
        else:
            try:
                keys = list_router_keys()
                if not any(item.get("key") == router_key and item.get("isActive", True) is not False for item in keys):
                    failures.append("stored GOOROS_9ROUTER_API_KEY is not present as an active 9Router API key")
            except Exception as exc:
                failures.append(f"9Router API key list not verifiable: {exc}")
        combo_models: list[str] = []
        try:
            combos = list_router_combos()
            combo = next((item for item in combos if item.get("name") == GOOROS_9ROUTER_COMBO_NAME), None)
            if not combo:
                failures.append(f"9Router combo missing: {GOOROS_9ROUTER_COMBO_NAME}")
            elif not combo.get("models"):
                failures.append(f"9Router combo has no member models: {GOOROS_9ROUTER_COMBO_NAME}")
            else:
                combo_models = [str(model).strip() for model in combo.get("models", []) if str(model).strip()]
                bad_models = [model for model in combo_models if "/" not in model]
                if bad_models:
                    failures.append("9Router combo contains non-routable raw model IDs: " + ", ".join(bad_models[:5]))
                for spec in REQUIRED_FREE_PROVIDERS:
                    if not any(model.startswith(f"{spec.alias}/") or model.startswith(f"{spec.provider_id}/") for model in combo_models):
                        failures.append(f"9Router combo is missing required provider models: {spec.display_name} ({spec.alias}/...)")
        except Exception as exc:
            failures.append(f"9Router combo list not verifiable: {exc}")
        try:
            settings = get_router_settings()
            if settings.get("comboStrategy") != "round-robin":
                failures.append("9Router comboStrategy is not round-robin")
        except Exception as exc:
            failures.append(f"9Router settings not verifiable: {exc}")
        routing = paths.hermes_home / "agents" / "_shared" / "model-routing.json"
        if routing.exists():
            try:
                routing_data = json.loads(routing.read_text(encoding="utf-8"))
                combo_data = routing_data.get("combo", {}) if isinstance(routing_data, dict) else {}
                if combo_data.get("name") != GOOROS_9ROUTER_COMBO_NAME:
                    failures.append("model-routing.json does not point to the Gooros 9Router combo")
                route_members = [str(model).strip() for model in (combo_data.get("members") or []) if str(model).strip()]
                if not route_members:
                    failures.append("model-routing.json has no free combo member list")
                for spec in REQUIRED_FREE_PROVIDERS:
                    if not any(model.startswith(f"{spec.alias}/") or model.startswith(f"{spec.provider_id}/") for model in route_members):
                        failures.append(f"model-routing.json is missing required provider models: {spec.display_name}")
                route_models = routing_data.get("models") if isinstance(routing_data, dict) else []
                if not isinstance(route_models, list):
                    failures.append("model-routing.json models must be a list with premium/fast tiers")
                    route_models = []
                route_tiers = {str(row.get("tier", "")): str(row.get("id", "")) for row in route_models if isinstance(row, dict)}
                if not route_tiers.get("premium"):
                    failures.append("model-routing.json missing premium tier for complex Prompt 34 tasks")
                if route_tiers.get("fast") != GOOROS_9ROUTER_COMBO_NAME:
                    failures.append("model-routing.json fast tier must route to the Gooros 9Router combo")
                if route_tiers.get("premium") == route_tiers.get("fast"):
                    failures.append("model-routing.json premium and fast tiers must be distinct when free provider models are available")
                if combo_models:
                    missing_from_router = sorted(set(route_members) - set(combo_models))
                    if missing_from_router:
                        failures.append("9Router combo is missing model-routing members: " + ", ".join(missing_from_router[:5]))
            except Exception as exc:
                failures.append(f"model-routing.json invalid: {exc}")
        else:
            failures.append("model-routing.json missing for 9Router combo routing")
        profile_targets = [("orchestrator", paths.hermes_home)] + [(agent, paths.hermes_home / "profiles" / agent) for agent in SPECIALISTS]
        for agent, root in profile_targets:
            cfg = root / "config.yaml"
            env = root / ".env"
            if cfg.exists() and GOOROS_9ROUTER_COMBO_NAME not in cfg.read_text(encoding="utf-8", errors="replace"):
                failures.append(f"Hermes profile {agent} default model is not the 9Router combo")
            if router_key and read_env_values(env).get("OPENAI_API_KEY") != router_key:
                failures.append(f"Hermes profile {agent} .env does not contain the active 9Router API key")
    return failures


def doctor(paths: InstallPaths, runner: Runner) -> dict:
    report = {
        "hermes_home": str(paths.hermes_home),
        "project_dir": str(paths.project_dir),
        "commands": {},
        "files": {},
    }
    for cmd in ("hermes", "python3", "node", "npm", "9router", "caddy", "systemctl", "curl"):
        report["commands"][cmd] = shutil.which(cmd)
    for path in (
        paths.hermes_home / "config.yaml",
        paths.hermes_home / "state.db",
        paths.hermes_home / "kanban.db",
        paths.project_dir / "server.py",
        paths.project_dir / "index.html",
        paths.project_dir / "board.db",
        paths.project_dir / "agent-logs.db",
    ):
        report["files"][str(path)] = path.exists()
    return report


def print_doctor(report: dict) -> None:
    print(json.dumps(report, indent=2, sort_keys=True))
