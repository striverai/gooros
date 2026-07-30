from __future__ import annotations

import json
import os
import getpass
import importlib.util
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from .configstore import CustomerConfig, collect_customer_config, merge_env_values, read_env_values, validate_required, write_customer_files
from .constants import AGENTS, DASHBOARD_VERSION, GOOROS_9ROUTER_API_KEY_NAME, GOOROS_9ROUTER_COMBO_NAME, SPECIALISTS, VERSION
from .dashboard_patcher import build_live_dashboard
from .fsutil import atomic_write_json, atomic_write_text, copy_file, ensure_dir
from .paths import InstallPaths, asset_path, default_paths
from .prompt5 import (
    PROMPT5_IDENTITY_QUESTION,
    PROMPT5_MEMORY_MARKER,
    PROMPT5_REQUIRED_RULE_PHRASES,
    prompt5_report_json_path,
    prompt5_report_markdown_path,
    render_prompt5_identity,
    render_prompt5_markdown_report,
    validate_identity_answer,
    validate_prompt5_soul,
)
from .prompt6 import (
    PROMPT6_BOUNDARY_TESTS,
    PROMPT6_MEMORY_MARKER,
    PROMPT6_REPORT_MARKDOWN,
    boundary_teammate,
    prompt6_agent_home,
    prompt6_agent_memory_dir,
    prompt6_agent_workspace,
    prompt6_report_json_path,
    prompt6_report_markdown_path,
    render_prompt6_markdown_report,
    render_prompt6_memory,
    render_prompt6_policy,
    validate_boundary_answer,
    validate_prompt6_identity_answer,
    validate_prompt6_soul,
)
from .prompt7 import (
    PROMPT7_HANDOFF_TESTS,
    PROMPT7_MEMORY_MARKER,
    prompt7_handoff_line,
    prompt7_report_json_path,
    prompt7_report_markdown_path,
    prompt7_teammate,
    render_prompt7_markdown_report,
    render_prompt7_memory,
    render_prompt7_policy,
    validate_handoff_answer,
    validate_prompt7_memory,
    validate_prompt7_soul,
)
from .prompt9 import (
    PROMPT9_MEMORY_MARKER,
    PROMPT9_SMOKE_TASK,
    prompt9_report_json_path,
    prompt9_report_markdown_path,
    render_prompt9_markdown_report,
    render_prompt9_memory,
    render_prompt9_policy,
    validate_prompt9_memory,
    validate_prompt9_soul,
)
from .prompt10 import (
    PROMPT10_RETENTION_DAYS,
    parse_cleanup_summary,
    prompt10_cron_line,
    prompt10_report_json_path,
    prompt10_report_markdown_path,
    render_prompt10_markdown_report,
    validate_prompt10_script,
)
from .prompt11 import (
    PROMPT11_MEMORY_MARKER,
    PROMPT11_CHANNELS,
    expected_topic_routes,
    prompt11_report_json_path,
    prompt11_report_markdown_path,
    render_prompt11_markdown_report,
    render_prompt11_memory,
    render_prompt11_policy,
    validate_prompt11_memory,
    validate_prompt11_report,
    validate_prompt11_soul,
    validate_topic_routes,
)
from .prompt12 import (
    prompt12_report_json_path,
    prompt12_report_markdown_path,
    render_prompt12_markdown_report,
    validate_prompt12_report,
)
from .prompt13 import (
    prompt13_report_json_path,
    prompt13_report_markdown_path,
    render_prompt13_markdown_report,
    scrub_profile_config,
    scrub_profile_env,
    validate_prompt13_report,
    validate_specialist_profile_isolation,
)
from .prompt15 import (
    PROMPT15_INIT_PY,
    PROMPT15_PLUGIN_YAML,
    prompt15_plugin_dir,
    prompt15_report_json_path,
    prompt15_report_markdown_path,
    render_prompt15_markdown_report,
    render_prompt15_topics,
    validate_prompt15_plugin,
    validate_prompt15_report,
)
from .prompt16 import (
    PROMPT16_CONFIG_GET_COMMAND,
    PROMPT16_CONFIG_SET_COMMAND,
    PROMPT16_ENABLE_COMMAND,
    PROMPT16_GATEWAY_RESTART_COMMAND,
    PROMPT16_GATEWAY_STATUS_COMMAND,
    PROMPT16_PLUGINS_LIST_COMMAND,
    parse_multiplex_profiles_enabled,
    parse_plugin_enabled,
    prompt16_report_json_path,
    prompt16_report_markdown_path,
    render_prompt16_markdown_report,
    validate_prompt16_report,
)
from .prompt17 import (
    PROMPT17_SYMPTOM_GUIDANCE,
    audit_prompt17_profile,
    expected_prompt17_topic_routes,
    group_allowed_chats_contains,
    prompt17_audit_table,
    prompt17_report_json_path,
    prompt17_report_markdown_path,
    render_prompt17_markdown_report,
    top_level_multiplex_profiles,
    validate_prompt17_report,
    validate_prompt17_topics,
)
from .prompt29 import (
    PROMPT29_MEMORY_MARKER,
    render_prompt29_memory,
    render_prompt29_policy,
    validate_prompt29_soul,
)
from .proxy import (
    caddy_hash_password,
    install_caddy_if_missing,
    install_public_proxy,
    root_prefix,
    router_initial_password,
    router_local_api_key,
    sslip_name,
    write_system_env,
)
from .release import current_source_metadata
from .runner import Runner
from .router_api import (
    REQUIRED_FREE_PROVIDERS,
    choose_router_model,
    discover_free_router_models,
    ensure_router_api_key,
    ensure_router_combo,
    ensure_router_round_robin,
)
from .safety import create_snapshot, write_install_state
from .verify import verify_install
from .yaml_merge import merge_telegram_group_config, telegram_token_lines, validate_telegram_group_config_text


ORCHESTRATOR_SOUL_MARKER = "orchestrator SOUL v1"
OWNER_USER_MEMORY_MARKER = "owner profile USER v1"
TEAM_MEMORY_MARKER = "mission control team MEMORY v1"
OPERATING_RULES_MEMORY_MARKER = "orchestrator operating rules Prompt 3 v1"
SPECIALIST_MEMORY_MARKER = "specialist profile Prompt 4 v1"
SPECIALIST_OWNER_MEMORY_MARKER = "specialist owner profile USER v1"
MEMORY_ENTRY_DELIMITER = "\n\u00a7\n"


def preflight(runner: Runner, paths: InstallPaths, *, public_dashboards: bool, with_9router: bool) -> None:
    runner.log("[preflight] checking local requirements")
    if os.name != "posix" and not runner.dry_run:
        raise RuntimeError("full install target is Linux/macOS/WSL; run --dry-run on Windows for planning")
    for cmd in ("python3", "bash", "curl"):
        if not shutil.which(cmd) and not runner.dry_run:
            raise RuntimeError(f"required command missing: {cmd}")
    if public_dashboards and not runner.dry_run:
        root_prefix()
    if with_9router and not shutil.which("npm") and not runner.dry_run:
        runner.log("[preflight] npm missing; installer will try apt-get install nodejs npm")
    if runner.dry_run:
        runner.log(f"would ensure project dir: {paths.project_dir}")
        runner.log(f"would ensure config dir: {paths.config_dir}")
        runner.log(f"would ensure data dir: {paths.data_dir}")
        return
    ensure_dir(paths.project_dir)
    ensure_dir(paths.config_dir, 0o700)
    ensure_dir(paths.data_dir)


def install_hermes_if_needed(runner: Runner, *, with_hermes: bool) -> None:
    if shutil.which("hermes"):
        runner.log("[hermes] found existing Hermes CLI")
        return
    if not with_hermes:
        raise RuntimeError("Hermes CLI not found; rerun with --with-hermes")
    runner.log("[hermes] installing Hermes Agent using official install script")
    try:
        runner.shell("curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash", timeout=600, env={"CI": "1"})
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        if shutil.which("hermes"):
            runner.log(f"[hermes] install script exited before setup completed; Hermes CLI is present, continuing: {exc}")
            return
        raise RuntimeError(f"Hermes install script did not finish and Hermes CLI is still missing: {exc}") from exc
    if not shutil.which("hermes"):
        raise RuntimeError("Hermes install script completed, but Hermes CLI is still missing from PATH")


def install_9router_if_requested(runner: Runner, *, requested: bool) -> None:
    if not requested:
        return
    if not shutil.which("npm"):
        if shutil.which("apt-get"):
            prefix = root_prefix()
            runner.run(prefix + ["apt-get", "update"], timeout=300)
            runner.run(prefix + ["apt-get", "install", "-y", "nodejs", "npm"], timeout=300)
        else:
            raise RuntimeError("npm is required to install 9Router")
    if not shutil.which("9router"):
        runner.run(["npm", "install", "-g", "9router"], timeout=600)


def hermes_plugin_enable_command(runner: Runner, plugin: str) -> list[str]:
    command = ["hermes", "plugins", "enable", plugin]
    result = runner.run(["hermes", "plugins", "enable", "--help"], capture=True, check=False, timeout=30)
    help_text = (result.stdout or "") + "\n" + (result.stderr or "")
    if "--no-allow-tool-override" in help_text:
        command.append("--no-allow-tool-override")
    return command


def _http_ok(url: str, timeout: float = 5.0) -> tuple[bool, str]:
    try:
        request = Request(url, headers={"User-Agent": "gooros-hermes-installer"})
        with urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 500, str(response.status)
    except Exception as exc:
        return False, str(exc)


def wait_for_9router(timeout_seconds: int = 60) -> None:
    deadline = time.time() + timeout_seconds
    detail = "not checked"
    while time.time() < deadline:
        ok, detail = _http_ok("http://127.0.0.1:20128/v1/models", timeout=3)
        if ok:
            return
        time.sleep(2)
    raise RuntimeError(f"9Router did not become reachable at http://127.0.0.1:20128/v1/models: {detail}")


def _provider_smoke_enabled() -> bool:
    return os.environ.get("GOOROS_9ROUTER_SMOKE", "1").strip().lower() not in {"0", "false", "no", "off"}


def smoke_9router_model(model: str, api_key: str = "", timeout: int = 90) -> None:
    if not _provider_smoke_enabled():
        return
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with OK only."}],
        "max_tokens": 8,
        "stream": False,
    }
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "gooros-hermes-installer",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        request = Request(
            "http://127.0.0.1:20128/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urlopen(request, timeout=timeout) as response:
            body = response.read(256 * 1024).decode("utf-8", errors="replace")
        data = json.loads(body)
        if not data.get("choices"):
            raise RuntimeError(f"unexpected response: {body[:500]}")
    except Exception as exc:
        raise RuntimeError(
            "9Router combo smoke test failed. Connect a working free provider/model in the 9Router dashboard "
            "or set GOOROS_9ROUTER_SMOKE=0 only for a non-provider update. "
            f"model={model}; detail={exc}"
        ) from exc


def install_shared_scripts(paths: InstallPaths, runner: Runner | None = None) -> None:
    if runner and runner.dry_run:
        runner.log(f"would install shared scripts to {paths.hermes_home / 'agents' / '_shared'}")
        return
    shared = paths.hermes_home / "agents" / "_shared"
    ensure_dir(shared)
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
        copy_file(asset_path("shared", name), shared / name, mode=0o755)


def install_profiles(runner: Runner, paths: InstallPaths, config: CustomerConfig) -> None:
    for agent in SPECIALISTS:
        profile_dir = paths.hermes_home / "profiles" / agent
        soul = profile_dir / "SOUL.md"
        workspace = specialist_workspace(paths, agent)
        if profile_dir.exists() and soul.exists() and "GOOROS-HERMES-MANAGED" not in soul.read_text(encoding="utf-8", errors="replace"):
            raise RuntimeError(f"profile {agent} already exists and is not Gooros-managed; choose a merge strategy first")
        if not profile_dir.exists():
            runner.run(["hermes", "profile", "create", agent, "--clone", "--description", f"Gooros {agent} specialist"], timeout=120)
        if runner.dry_run:
            runner.log(
                f"would write exact Prompt 5 identity + Prompt 6 boundaries + Prompt 7 team handoff + Prompt 9 response logging into SOUL.md, seed private memories, "
                f"create workspace {workspace}, and sanitize platforms for profile {agent}"
            )
            runner.run(["hermes", "profile", "alias", agent, "--name", f"gooros-{agent}"], check=False, timeout=60)
            continue
        ensure_agent_workspace(paths, agent)
        text = asset_path("profiles", agent, "SOUL.md.tmpl").read_text(encoding="utf-8").format(
            prompt5_identity=render_prompt5_identity(agent, config.owner_name or "the owner"),
            prompt6_policy=render_prompt6_policy(agent, workspace),
            prompt7_policy=render_prompt7_policy(agent, workspace),
            prompt9_policy=render_prompt9_policy(agent, workspace),
            prompt29_policy=render_prompt29_policy(agent, paths.project_dir),
            owner_name=config.owner_name or "the owner",
            owner_work=config.owner_work or "unknown",
            owner_focus=config.owner_focus or "unknown",
            owner_working_hours=config.owner_working_hours or "unknown",
            owner_important_people=config.owner_important_people or "unknown",
            owner_cares_about=config.owner_cares_about or "unknown",
            timezone=config.timezone or "unknown",
            agent_workspace=str(workspace),
        )
        atomic_write_text(soul, text)
        soul_failures = validate_prompt5_soul(agent, text, owner_name=config.owner_name or "the owner", workspace=workspace)
        soul_failures.extend(validate_prompt6_soul(agent, text, workspace))
        soul_failures.extend(validate_prompt7_soul(agent, text, workspace))
        soul_failures.extend(validate_prompt9_soul(agent, text, workspace))
        soul_failures.extend(validate_prompt29_soul(agent, text))
        if soul_failures:
            raise RuntimeError("specialist SOUL validation failed for " + agent + ": " + "; ".join(soul_failures))
        seed_specialist_memory(profile_dir, agent, config, workspace, paths.project_dir)
        cfg = profile_dir / "config.yaml"
        if cfg.exists():
            scrub_profile_config(cfg)
        scrub_profile_env(profile_dir / ".env")
        runner.run(["hermes", "profile", "alias", agent, "--name", f"gooros-{agent}"], check=False, timeout=60)


def specialist_workspace(paths: InstallPaths, agent: str) -> Path:
    return prompt6_agent_workspace(paths.project_dir, agent)


def ensure_agent_workspace(paths: InstallPaths, agent: str) -> None:
    workspace = prompt6_agent_workspace(paths.project_dir, agent)
    ensure_dir(workspace)
    marker = workspace / ".gooros-agent-workspace"
    if not marker.exists():
        atomic_write_text(
            marker,
            f"agent={agent}\nrole={'orchestrator' if agent == 'orchestrator' else 'specialist'}\nprompt=Prompt 6\nowner=Gooros Hermes Mission Control\n",
        )
    readme = workspace / "README.md"
    if not readme.exists():
        atomic_write_text(
            readme,
            f"# {agent.title()} Workspace\n\nPrivate Prompt 6 working directory for the persistent Gooros agent `{agent}`.\n",
        )


def ensure_specialist_workspace(paths: InstallPaths, agent: str) -> None:
    ensure_agent_workspace(paths, agent)


def seed_specialist_memory(profile_dir: Path, agent: str, config: CustomerConfig, workspace: Path, project_dir: Path) -> None:
    memories = profile_dir / "memories"
    _upsert_memory_entry(memories / "USER.md", SPECIALIST_OWNER_MEMORY_MARKER, render_owner_user_memory(config))
    _upsert_memory_entry(
        memories / "MEMORY.md",
        f"{agent} {SPECIALIST_MEMORY_MARKER}",
        render_specialist_memory(agent, workspace),
    )
    _upsert_memory_entry(
        memories / "MEMORY.md",
        f"{agent} {PROMPT5_MEMORY_MARKER}",
        render_prompt5_specialist_memory(agent, config),
    )
    _upsert_memory_entry(
        memories / "MEMORY.md",
        f"{agent} {PROMPT6_MEMORY_MARKER}",
        render_prompt6_memory(agent, workspace),
    )
    _upsert_memory_entry(
        memories / "MEMORY.md",
        f"{agent} {PROMPT7_MEMORY_MARKER}",
        render_prompt7_memory(agent),
    )
    _upsert_memory_entry(
        memories / "MEMORY.md",
        f"{agent} {PROMPT9_MEMORY_MARKER}",
        render_prompt9_memory(agent),
    )
    _upsert_memory_entry(
        memories / "MEMORY.md",
        f"{agent} {PROMPT29_MEMORY_MARKER}",
        render_prompt29_memory(agent, project_dir),
    )


def render_specialist_memory(agent: str, workspace: Path) -> str:
    roles = {
        "scout": "Scout researches and tracks trends. It should gather verifiable sources and never write final marketing/content drafts.",
        "scribe": "Scribe writes and edits content. It should use Scout research when facts are needed and never invent sources.",
        "reach": "Reach handles marketing, growth, and monetization. It should respect owner limits and avoid fake traction claims.",
        "dev": "Dev handles engineering, automation, integrations, and dashboards. It should verify technical work before reporting completion.",
    }
    return (
        f"Prompt 4 persistent specialist profile: {agent} is a long-lived Hermes profile, not a temporary sub-agent. "
        f"It has its own HERMES_HOME, SOUL.md, memories directory, and private workspace at {workspace}. "
        f"{roles.get(agent, '')} Orchestrator remains the default/root Hermes agent on Telegram and must not be recreated as a profile. "
        "The owner has final decision authority. The team uses Telegram, not Discord."
    )


def render_prompt5_specialist_memory(agent: str, config: CustomerConfig) -> str:
    rules = "; ".join(PROMPT5_REQUIRED_RULE_PHRASES[agent])
    return (
        f"Prompt 5 exact specialist identity is installed for {agent}. "
        f"When asked `{PROMPT5_IDENTITY_QUESTION}`, the agent must identify as {agent.title()} and its role for "
        f"{_owner_value(config.owner_name)}. Prompt 5 behavior rules to preserve: {rules}."
    )


def _write_prompt5_identity_report(paths: InstallPaths, report: dict[str, object]) -> None:
    ensure_dir(prompt5_report_json_path(paths.project_dir).parent)
    atomic_write_json(prompt5_report_json_path(paths.project_dir), report, mode=0o600)
    atomic_write_text(prompt5_report_markdown_path(paths.project_dir), render_prompt5_markdown_report(report), mode=0o600)


def verify_prompt5_specialist_identities_live(runner: Runner, paths: InstallPaths, config: CustomerConfig) -> None:
    if runner.dry_run:
        runner.log(f"would ask each specialist profile `{PROMPT5_IDENTITY_QUESTION}` and write Prompt 5 verification report")
        return
    report: dict[str, object] = {
        "prompt": "Prompt 5",
        "version": VERSION,
        "question": PROMPT5_IDENTITY_QUESTION,
        "owner_name": config.owner_name or "",
        "status": "running",
        "checks": [],
        "report_json": str(prompt5_report_json_path(paths.project_dir)),
        "report_markdown": str(prompt5_report_markdown_path(paths.project_dir)),
    }
    checks = report["checks"]
    assert isinstance(checks, list)
    for index, agent in enumerate(SPECIALISTS, start=1):
        runner.log(f"[prompt5] Bước {index}/4 — hỏi {agent}: {PROMPT5_IDENTITY_QUESTION}")
        profile_dir = paths.hermes_home / "profiles" / agent
        soul_path = profile_dir / "SOUL.md"
        workspace = specialist_workspace(paths, agent)
        soul_text = soul_path.read_text(encoding="utf-8", errors="replace") if soul_path.exists() else ""
        errors = validate_prompt5_soul(agent, soul_text, owner_name=config.owner_name or "the owner", workspace=workspace)
        answer = ""
        returncode = None
        if not errors:
            result = runner.run(
                ["hermes", "-p", agent, "chat", "-Q", "--no-restore-cwd", "-q", PROMPT5_IDENTITY_QUESTION],
                cwd=workspace,
                capture=True,
                check=False,
                timeout=240,
                env={
                    "TERMINAL_CWD": str(workspace),
                    "GOOROS_AGENT_WORKSPACE": str(workspace),
                    "GOOROS_AGENT_WORKSPACE_ROOT": str(paths.project_dir / "workspaces"),
                },
            )
            returncode = result.returncode
            answer = (result.stdout or result.stderr or "").strip()
            if result.returncode != 0:
                errors.append(f"Hermes chat returned {result.returncode}: {(result.stderr or result.stdout or '').strip()[:500]}")
            errors.extend(validate_identity_answer(agent, answer))
        entry = {
            "order": index,
            "agent": agent,
            "owner_name": config.owner_name or "",
            "profile_path": str(profile_dir),
            "soul_path": str(soul_path),
            "workspace_path": str(workspace),
            "question": PROMPT5_IDENTITY_QUESTION,
            "answer": answer,
            "returncode": returncode,
            "soul_verified": not [issue for issue in errors if issue.startswith("SOUL.md")],
            "verified": not errors,
            "errors": errors,
        }
        checks.append(entry)
        if errors:
            report["status"] = "failed"
            _write_prompt5_identity_report(paths, report)
            raise RuntimeError(f"Prompt 5 identity verification failed for {agent}: " + "; ".join(errors))
    report["status"] = "passed"
    _write_prompt5_identity_report(paths, report)


def _agent_chat_command(agent: str, query: str) -> list[str]:
    if agent == "orchestrator":
        return ["hermes", "chat", "-Q", "--no-restore-cwd", "-q", query]
    return ["hermes", "-p", agent, "chat", "-Q", "--no-restore-cwd", "-q", query]


def _write_prompt6_report(paths: InstallPaths, report: dict[str, object]) -> None:
    ensure_dir(prompt6_report_json_path(paths.project_dir).parent)
    atomic_write_json(prompt6_report_json_path(paths.project_dir), report, mode=0o600)
    atomic_write_text(prompt6_report_markdown_path(paths.project_dir), render_prompt6_markdown_report(report), mode=0o600)


def _write_prompt7_report(paths: InstallPaths, report: dict[str, object]) -> None:
    ensure_dir(prompt7_report_json_path(paths.project_dir).parent)
    atomic_write_json(prompt7_report_json_path(paths.project_dir), report, mode=0o600)
    atomic_write_text(prompt7_report_markdown_path(paths.project_dir), render_prompt7_markdown_report(report), mode=0o600)


def _write_prompt9_report(paths: InstallPaths, report: dict[str, object]) -> None:
    ensure_dir(prompt9_report_json_path(paths.project_dir).parent)
    atomic_write_json(prompt9_report_json_path(paths.project_dir), report, mode=0o600)
    atomic_write_text(prompt9_report_markdown_path(paths.project_dir), render_prompt9_markdown_report(report), mode=0o600)


def _write_prompt10_report(paths: InstallPaths, report: dict[str, object]) -> None:
    ensure_dir(prompt10_report_json_path(paths.project_dir).parent)
    atomic_write_json(prompt10_report_json_path(paths.project_dir), report, mode=0o600)
    atomic_write_text(prompt10_report_markdown_path(paths.project_dir), render_prompt10_markdown_report(report), mode=0o600)


def _write_prompt11_report(paths: InstallPaths, report: dict[str, object]) -> None:
    ensure_dir(prompt11_report_json_path(paths.project_dir).parent)
    atomic_write_json(prompt11_report_json_path(paths.project_dir), report, mode=0o600)
    atomic_write_text(prompt11_report_markdown_path(paths.project_dir), render_prompt11_markdown_report(report), mode=0o600)


def _write_prompt12_report(paths: InstallPaths, report: dict[str, object]) -> None:
    ensure_dir(prompt12_report_json_path(paths.project_dir).parent)
    atomic_write_json(prompt12_report_json_path(paths.project_dir), report, mode=0o600)
    atomic_write_text(prompt12_report_markdown_path(paths.project_dir), render_prompt12_markdown_report(report), mode=0o600)


def _write_prompt13_report(paths: InstallPaths, report: dict[str, object]) -> None:
    ensure_dir(prompt13_report_json_path(paths.project_dir).parent)
    atomic_write_json(prompt13_report_json_path(paths.project_dir), report, mode=0o600)
    atomic_write_text(prompt13_report_markdown_path(paths.project_dir), render_prompt13_markdown_report(report), mode=0o600)


def verify_prompt6_agents_live(runner: Runner, paths: InstallPaths, config: CustomerConfig) -> None:
    if runner.dry_run:
        runner.log(
            "would verify Prompt 6 for all five agents: private workspaces, scoped memories, "
            "`Bạn là ai?`, and one-line role-boundary refusals"
        )
        return
    report: dict[str, object] = {
        "prompt": "Prompt 6",
        "version": VERSION,
        "owner_name": config.owner_name or "",
        "status": "running",
        "identity_question": PROMPT5_IDENTITY_QUESTION,
        "identity_checks": [],
        "boundary_checks": [],
        "workspace_checks": [],
        "report_json": str(prompt6_report_json_path(paths.project_dir)),
        "report_markdown": str(prompt6_report_markdown_path(paths.project_dir)),
    }
    all_errors: list[str] = []
    identity_checks = report["identity_checks"]
    boundary_checks = report["boundary_checks"]
    workspace_checks = report["workspace_checks"]
    assert isinstance(identity_checks, list)
    assert isinstance(boundary_checks, list)
    assert isinstance(workspace_checks, list)
    for index, agent in enumerate(AGENTS, start=1):
        workspace = prompt6_agent_workspace(paths.project_dir, agent)
        home = prompt6_agent_home(paths.hermes_home, agent)
        memory_dir = prompt6_agent_memory_dir(paths.hermes_home, agent)
        ensure_agent_workspace(paths, agent)
        marker = workspace / ".gooros-agent-workspace"
        workspace_errors = []
        if not marker.exists():
            workspace_errors.append("workspace marker missing")
        if not memory_dir.exists():
            workspace_errors.append("memory directory missing")
        workspace_checks.append(
            {
                "agent": agent,
                "workspace_path": str(workspace),
                "home_path": str(home),
                "memory_path": str(memory_dir),
                "workspace_verified": not workspace_errors,
                "errors": workspace_errors,
            }
        )
        all_errors.extend(f"{agent} workspace: {issue}" for issue in workspace_errors)

        runner.log(f"[prompt6] Bước {index}/5 — hỏi {agent}: {PROMPT5_IDENTITY_QUESTION}")
        identity_result = runner.run(
            _agent_chat_command(agent, PROMPT5_IDENTITY_QUESTION),
            cwd=workspace,
            capture=True,
            check=False,
            timeout=240,
            env={
                "TERMINAL_CWD": str(workspace),
                "GOOROS_AGENT_WORKSPACE": str(workspace),
                "GOOROS_AGENT_WORKSPACE_ROOT": str(paths.project_dir / "workspaces"),
            },
        )
        identity_answer = (identity_result.stdout or identity_result.stderr or "").strip()
        identity_errors = []
        if identity_result.returncode != 0:
            identity_errors.append(f"Hermes chat returned {identity_result.returncode}: {(identity_result.stderr or identity_result.stdout or '').strip()[:500]}")
        identity_errors.extend(validate_prompt6_identity_answer(agent, identity_answer))
        identity_checks.append(
            {
                "order": index,
                "agent": agent,
                "question": PROMPT5_IDENTITY_QUESTION,
                "answer": identity_answer,
                "returncode": identity_result.returncode,
                "verified": not identity_errors,
                "errors": identity_errors,
            }
        )
        all_errors.extend(f"{agent} identity: {issue}" for issue in identity_errors)

        boundary = PROMPT6_BOUNDARY_TESTS[agent]
        boundary_query = str(boundary["request"])
        boundary_result = runner.run(
            _agent_chat_command(agent, boundary_query),
            cwd=workspace,
            capture=True,
            check=False,
            timeout=240,
            env={
                "TERMINAL_CWD": str(workspace),
                "GOOROS_AGENT_WORKSPACE": str(workspace),
                "GOOROS_AGENT_WORKSPACE_ROOT": str(paths.project_dir / "workspaces"),
            },
        )
        boundary_answer = (boundary_result.stdout or boundary_result.stderr or "").strip()
        boundary_errors = []
        if boundary_result.returncode != 0:
            boundary_errors.append(f"Hermes chat returned {boundary_result.returncode}: {(boundary_result.stderr or boundary_result.stdout or '').strip()[:500]}")
        boundary_errors.extend(validate_boundary_answer(agent, boundary_answer))
        boundary_checks.append(
            {
                "order": index,
                "agent": agent,
                "request": boundary_query,
                "expected_teammate": boundary_teammate(agent),
                "reason": boundary["reason"],
                "answer": boundary_answer,
                "returncode": boundary_result.returncode,
                "verified": not boundary_errors,
                "errors": boundary_errors,
            }
        )
        all_errors.extend(f"{agent} boundary: {issue}" for issue in boundary_errors)
    report["status"] = "failed" if all_errors else "passed"
    _write_prompt6_report(paths, report)
    if all_errors:
        raise RuntimeError("Prompt 6 verification failed: " + "; ".join(all_errors))


def verify_prompt7_agents_live(runner: Runner, paths: InstallPaths, config: CustomerConfig) -> None:
    if runner.dry_run:
        runner.log(
            "would verify Prompt 7 for all five agents: shared team awareness in SOUL/memory, "
            "one-line handoff answers, and executable handoff-task-local.py routing"
        )
        return
    report: dict[str, object] = {
        "prompt": "Prompt 7",
        "version": VERSION,
        "owner_name": config.owner_name or "",
        "status": "running",
        "team_awareness_checks": [],
        "handoff_checks": [],
        "handoff_script_checks": [],
        "report_json": str(prompt7_report_json_path(paths.project_dir)),
        "report_markdown": str(prompt7_report_markdown_path(paths.project_dir)),
    }
    all_errors: list[str] = []
    awareness_checks = report["team_awareness_checks"]
    handoff_checks = report["handoff_checks"]
    script_checks = report["handoff_script_checks"]
    assert isinstance(awareness_checks, list)
    assert isinstance(handoff_checks, list)
    assert isinstance(script_checks, list)
    shared = paths.hermes_home / "agents" / "_shared"
    script = shared / "handoff-task-local.py"
    for index, agent in enumerate(AGENTS, start=1):
        workspace = prompt6_agent_workspace(paths.project_dir, agent)
        soul_path = paths.hermes_home / "SOUL.md" if agent == "orchestrator" else paths.hermes_home / "profiles" / agent / "SOUL.md"
        memory_path = paths.hermes_home / "memories" / "MEMORY.md" if agent == "orchestrator" else paths.hermes_home / "profiles" / agent / "memories" / "MEMORY.md"
        soul_text = soul_path.read_text(encoding="utf-8", errors="replace") if soul_path.exists() else ""
        memory_text = memory_path.read_text(encoding="utf-8", errors="replace") if memory_path.exists() else ""
        awareness_errors = []
        awareness_errors.extend(validate_prompt7_soul(agent, soul_text, workspace))
        awareness_errors.extend(validate_prompt7_memory(agent, memory_text))
        awareness_checks.append(
            {
                "order": index,
                "agent": agent,
                "soul_path": str(soul_path),
                "memory_path": str(memory_path),
                "workspace_path": str(workspace),
                "verified": not awareness_errors,
                "errors": awareness_errors,
            }
        )
        all_errors.extend(f"{agent} team awareness: {issue}" for issue in awareness_errors)

        handoff = PROMPT7_HANDOFF_TESTS[agent]
        expected = prompt7_teammate(agent)
        query = (
            "Prompt 7 live handoff check. "
            f"Yeu cau: {handoff['request']} "
            f"Neu day chu yeu la mang cua agent khac, chi tra loi dung mot dong theo mau: {prompt7_handoff_line(agent)}"
        )
        runner.log(f"[prompt7] Bước {index}/5 — kiểm tra {agent} nêu đúng teammate và chuyển việc")
        handoff_result = runner.run(
            _agent_chat_command(agent, query),
            cwd=workspace,
            capture=True,
            check=False,
            timeout=240,
            env={
                "TERMINAL_CWD": str(workspace),
                "GOOROS_AGENT_WORKSPACE": str(workspace),
                "GOOROS_AGENT_WORKSPACE_ROOT": str(paths.project_dir / "workspaces"),
            },
        )
        handoff_answer = (handoff_result.stdout or handoff_result.stderr or "").strip()
        handoff_errors = []
        if handoff_result.returncode != 0:
            handoff_errors.append(f"Hermes chat returned {handoff_result.returncode}: {(handoff_result.stderr or handoff_result.stdout or '').strip()[:500]}")
        handoff_errors.extend(validate_handoff_answer(agent, handoff_answer))
        handoff_checks.append(
            {
                "order": index,
                "agent": agent,
                "request": handoff["request"],
                "expected_teammate": expected,
                "reason": handoff["reason"],
                "answer": handoff_answer,
                "returncode": handoff_result.returncode,
                "verified": not handoff_errors,
                "errors": handoff_errors,
            }
        )
        all_errors.extend(f"{agent} handoff answer: {issue}" for issue in handoff_errors)

        script_errors = []
        script_output = ""
        script_returncode = None
        script_target = expected.lower()
        if not script.exists():
            script_errors.append("handoff-task-local.py missing")
        else:
            script_result = runner.run(
                [
                    sys.executable,
                    str(script),
                    agent,
                    str(handoff["request"]),
                    "--to",
                    expected.lower(),
                    "--project-dir",
                    str(paths.project_dir),
                    "--shared-dir",
                    str(shared),
                    "--dry-run",
                ],
                cwd=workspace,
                capture=True,
                check=False,
                timeout=120,
                env={
                    "GOOROS_PROJECT_DIR": str(paths.project_dir),
                    "GOOROS_AGENT_WORKSPACE_ROOT": str(paths.project_dir / "workspaces"),
                    "HERMES_SHARED_DIR": str(shared),
                },
            )
            script_returncode = script_result.returncode
            script_output = (script_result.stdout or script_result.stderr or "").strip()
            if script_result.returncode != 0:
                script_errors.append(f"handoff script returned {script_result.returncode}: {script_output[:500]}")
            else:
                try:
                    payload = json.loads(script_output)
                    script_target = str(payload.get("target", "")).strip()
                    if payload.get("status") != "completed":
                        script_errors.append("handoff script dry-run status is not completed")
                    if script_target != expected.lower():
                        script_errors.append(f"handoff script routed to {script_target}, expected {expected.lower()}")
                except Exception as exc:
                    script_errors.append(f"handoff script did not return JSON: {exc}")
        script_checks.append(
            {
                "order": index,
                "agent": agent,
                "target": script_target,
                "returncode": script_returncode,
                "output": script_output[:1000],
                "verified": not script_errors,
                "errors": script_errors,
            }
        )
        all_errors.extend(f"{agent} handoff script: {issue}" for issue in script_errors)
    report["status"] = "failed" if all_errors else "passed"
    _write_prompt7_report(paths, report)
    if all_errors:
        raise RuntimeError("Prompt 7 verification failed: " + "; ".join(all_errors))


def _prompt9_agent_files(paths: InstallPaths, agent: str) -> tuple[Path, Path]:
    if agent == "orchestrator":
        return paths.hermes_home / "SOUL.md", paths.hermes_home / "memories" / "MEMORY.md"
    profile = paths.hermes_home / "profiles" / agent
    return profile / "SOUL.md", profile / "memories" / "MEMORY.md"


def _resolve_prompt9_smoke_model(runner: Runner, paths: InstallPaths) -> str:
    shared = paths.hermes_home / "agents" / "_shared"
    route_model = shared / "route_model.py"
    fallback = os.environ.get("GOOROS_DEFAULT_MODEL", "").strip() or "gooros-default-model"
    if not route_model.exists():
        return fallback
    result = runner.run(
        [sys.executable, str(route_model), PROMPT9_SMOKE_TASK],
        capture=True,
        check=False,
        timeout=60,
        env={
            "GOOROS_MODEL_ROUTING": str(shared / "model-routing.json"),
            "GOOROS_DEFAULT_MODEL": fallback,
        },
    )
    output = (result.stdout or result.stderr or "").strip()
    if result.returncode == 0 and output:
        return output.splitlines()[-1].strip() or fallback
    return fallback


def _prompt9_latest_row(db_path: Path, agent: str) -> dict[str, str] | None:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT agent_name, task_description, model_used, status, created_at
            FROM agent_logs
            WHERE agent_name = ? AND task_description = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (agent, PROMPT9_SMOKE_TASK),
        ).fetchone()
    if not row:
        return None
    return {
        "agent_name": str(row[0]),
        "task_description": str(row[1]),
        "model_used": str(row[2] or ""),
        "status": str(row[3]),
        "created_at": str(row[4]),
    }


def _prompt9_recent_rows(db_path: Path) -> list[dict[str, str]]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT agent_name, status, model_used, created_at
            FROM agent_logs
            ORDER BY created_at DESC
            LIMIT 5
            """
        ).fetchall()
    return [
        {
            "agent_name": str(agent),
            "status": str(status),
            "model_used": str(model or ""),
            "created_at": str(created_at),
        }
        for agent, status, model, created_at in rows
    ]


def verify_prompt9_agents_live(runner: Runner, paths: InstallPaths, config: CustomerConfig) -> None:
    if runner.dry_run:
        runner.log(
            "would verify Prompt 9 for all five agents: response logging rule in SOUL/memory, "
            "five smoke log writes, and latest five log rows from agent-logs.db"
        )
        return
    report: dict[str, object] = {
        "prompt": "Prompt 9",
        "version": VERSION,
        "owner_name": config.owner_name or "",
        "status": "running",
        "agent_log_db": str(paths.project_dir / "agent-logs.db"),
        "memory_checks": [],
        "log_smoke_checks": [],
        "recent_rows": [],
        "report_json": str(prompt9_report_json_path(paths.project_dir)),
        "report_markdown": str(prompt9_report_markdown_path(paths.project_dir)),
    }
    all_errors: list[str] = []
    memory_checks = report["memory_checks"]
    smoke_checks = report["log_smoke_checks"]
    assert isinstance(memory_checks, list)
    assert isinstance(smoke_checks, list)
    shared = paths.hermes_home / "agents" / "_shared"
    log_script = shared / "log-task-local.sh"
    db_path = paths.project_dir / "agent-logs.db"
    model = _resolve_prompt9_smoke_model(runner, paths)
    for index, agent in enumerate(AGENTS, start=1):
        workspace = prompt6_agent_workspace(paths.project_dir, agent)
        ensure_agent_workspace(paths, agent)
        soul_path, memory_path = _prompt9_agent_files(paths, agent)
        soul_text = soul_path.read_text(encoding="utf-8", errors="replace") if soul_path.exists() else ""
        memory_text = memory_path.read_text(encoding="utf-8", errors="replace") if memory_path.exists() else ""
        memory_errors = []
        memory_errors.extend(validate_prompt9_soul(agent, soul_text, workspace))
        memory_errors.extend(validate_prompt9_memory(agent, memory_text))
        memory_checks.append(
            {
                "order": index,
                "agent": agent,
                "soul_path": str(soul_path),
                "memory_path": str(memory_path),
                "workspace_path": str(workspace),
                "verified": not memory_errors,
                "errors": memory_errors,
            }
        )
        all_errors.extend(f"{agent} Prompt 9 memory: {issue}" for issue in memory_errors)

        runner.log(f"[prompt9] Bước {index}/5 — ghi smoke log cho {agent} trước phản hồi mẫu")
        smoke_errors = []
        smoke_output = ""
        smoke_returncode = None
        latest_row: dict[str, str] | None = None
        if not log_script.exists():
            smoke_errors.append("log-task-local.sh missing")
        else:
            smoke_result = runner.run(
                ["bash", str(log_script), agent, PROMPT9_SMOKE_TASK, "completed", model],
                cwd=workspace,
                capture=True,
                check=False,
                timeout=60,
                env={"AGENT_LOG_DB": str(db_path)},
            )
            smoke_returncode = smoke_result.returncode
            smoke_output = (smoke_result.stdout or smoke_result.stderr or "").strip()
            expected_output = f"LOGGED: {agent} | completed | {model}"
            if smoke_result.returncode != 0:
                smoke_errors.append(f"log-task-local.sh returned {smoke_result.returncode}: {smoke_output[:500]}")
            elif smoke_output != expected_output:
                smoke_errors.append(f"unexpected smoke output: expected {expected_output!r}, got {smoke_output!r}")
            else:
                try:
                    latest_row = _prompt9_latest_row(db_path, agent)
                    if not latest_row:
                        smoke_errors.append("no Prompt 9 smoke row found in agent_logs")
                    elif latest_row.get("model_used") != model or latest_row.get("status") != "completed":
                        smoke_errors.append("latest Prompt 9 smoke row has wrong model/status")
                except Exception as exc:
                    smoke_errors.append(f"could not confirm Prompt 9 smoke row: {exc}")
        smoke_checks.append(
            {
                "order": index,
                "agent": agent,
                "task_description": PROMPT9_SMOKE_TASK,
                "status": "completed",
                "model_used": model,
                "returncode": smoke_returncode,
                "output": smoke_output,
                "latest_row": latest_row or {},
                "verified": not smoke_errors,
                "errors": smoke_errors,
            }
        )
        all_errors.extend(f"{agent} Prompt 9 smoke: {issue}" for issue in smoke_errors)
    try:
        report["recent_rows"] = _prompt9_recent_rows(db_path)
        if len(report["recent_rows"]) < 5:
            all_errors.append("Prompt 9 latest-five report has fewer than five rows")
    except Exception as exc:
        all_errors.append(f"could not read Prompt 9 latest five log rows: {exc}")
    report["status"] = "failed" if all_errors else "passed"
    _write_prompt9_report(paths, report)
    if all_errors:
        raise RuntimeError("Prompt 9 verification failed: " + "; ".join(all_errors))


def retire_legacy_orchestrator_profile(runner: Runner, paths: InstallPaths) -> None:
    profile_dir = paths.hermes_home / "profiles" / "orchestrator"
    if not profile_dir.exists():
        return
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    retired_root = paths.hermes_home / "profiles" / "_gooros_retired"
    target = retired_root / f"orchestrator-{stamp}"
    if runner.dry_run:
        runner.log(f"would retire legacy profiles/orchestrator to {target}")
        return
    ensure_dir(retired_root)
    suffix = 1
    while target.exists():
        target = retired_root / f"orchestrator-{stamp}-{suffix}"
        suffix += 1
    shutil.move(str(profile_dir), str(target))
    runner.log(f"[profiles] retired legacy orchestrator profile to {target}")


def _managed_begin(marker: str) -> str:
    return f"<!-- BEGIN GOOROS-HERMES-MANAGED: {marker} -->"


def _managed_end(marker: str) -> str:
    return f"<!-- END GOOROS-HERMES-MANAGED: {marker} -->"


def _managed_markdown_block(marker: str, body: str) -> str:
    return f"{_managed_begin(marker)}\n{body.strip()}\n{_managed_end(marker)}\n"


def _upsert_managed_markdown(path: Path, marker: str, body: str, *, prepend: bool = False) -> None:
    block = _managed_markdown_block(marker, body)
    old = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    pattern = re.compile(
        re.escape(_managed_begin(marker)) + r".*?" + re.escape(_managed_end(marker)) + r"\s*",
        re.S,
    )
    if pattern.search(old):
        new = pattern.sub(block + ("\n" if old.strip() else ""), old, count=1).rstrip() + "\n"
    elif old.strip():
        new = (block.rstrip() + "\n\n" + old.strip() + "\n") if prepend else (old.rstrip() + "\n\n" + block)
    else:
        new = block
    atomic_write_text(path, new)


def _read_memory_entries(path: Path) -> list[str]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        return []
    return [entry.strip() for entry in text.split(MEMORY_ENTRY_DELIMITER) if entry.strip()]


def _upsert_memory_entry(path: Path, marker: str, entry: str) -> None:
    ensure_dir(path.parent, 0o700)
    marker_text = f"GOOROS-HERMES-MANAGED: {marker}"
    managed_entry = f"<!-- {marker_text} -->\n{entry.strip()}"
    entries = [existing for existing in _read_memory_entries(path) if marker_text not in existing]
    atomic_write_text(path, MEMORY_ENTRY_DELIMITER.join([managed_entry, *entries]).rstrip() + "\n", mode=0o600)


def _owner_value(value: str) -> str:
    return (value or "unknown").strip()


def render_orchestrator_soul(config: CustomerConfig, workspace: Path, project_dir: Path) -> str:
    owner = _owner_value(config.owner_name)
    return f"""# Gooros Orchestrator

Ten cua ban la Orchestrator. Ban la dieu phoi vien cap cao nhat cua Gooros Hermes Mission Control va ban van hanh tu Telegram nhu lop dieu khien chinh cua chu so huu.

Chu so huu co quyen cao nhat va co the chi thi truc tiep cho ban hoac bat ky specialist nao bat cu luc nao. Khi co xung dot uu tien, uu tien chi thi moi nhat cua chu so huu.

## Ho so chu so huu
- Ten: {owner}
- Cong viec / vai tro: {_owner_value(config.owner_work)}
- Trong tam hien tai: {_owner_value(config.owner_focus)}
- Mui gio: {_owner_value(config.timezone)}
- Gio lam viec dai khai: {_owner_value(config.owner_working_hours)}
- Nhung nguoi / tai khoan quan trong nhat: {_owner_value(config.owner_important_people)}
- Dieu chu so huu muon nam ro va muon giao lai: {_owner_value(config.owner_cares_about)}

## Doi hinh
- Orchestrator: dieu phoi tong the, intake tren Telegram, owner-facing report.
- Scout: nghien cuu, nguon, xu huong, facts.
- Scribe: viet, bien tap, noi dung xuat ban.
- Reach: marketing, growth, monetization.
- Dev: ky thuat, automation, dashboard, tich hop.

## Cach van hanh
- Ban khong chi chuyen tiep tin nhan. Ban chiu trach nhiem ket qua cuoi cung.
- Viec nhieu buoc: tom tat muc tieu, lap ke hoach ngan, giao dung specialist, theo doi output, kiem tra chat luong, roi tong hop cau tra loi owner-ready.
- Khi uy quyen, noi ro agent nao lam gi va ly do. Neu can chay workflow that, dung `python3 ~/.hermes/agents/_shared/orchestrate-task-local.py --mission "<mission>" --step scout::"<task>" --step scribe::"<task>" ...`.
- Khong bao ket qua da xong neu specialist fail, output rong, hoac chua du bang chung. Neu fail, noi thang blocker va de xuat buoc tiep theo.
- Lan dau tren Telegram, Gooros onboarding plugin hoi ho so owner va boi canh dieu phoi theo tung cau, roi luu vao `memories/USER.md`. Sau khi da luu, xem day la source of truth de giao viec cho Scout, Scribe, Reach va Dev.
- Workspace rieng cua ban: `{workspace}`.
- Truoc moi phan hoi, ghi log bang `bash ~/.hermes/agents/_shared/log-task-local.sh "orchestrator" "<brief description>" "<completed|failed>" "<model>"`.
- Tai lieu dai hon khoang 15 dong luu vao `~/agent-mission-control/content/orchestrator/` dang Markdown.

## Quy tac van hanh co dinh (Prompt 3)
- Ngon ngu: luon tra loi bang tieng Viet co dau, tu nhien, ngan gon va de hieu.
- Tien do: voi moi nhiem vu co hon mot buoc, gui mot dong trang thai truoc khi bat dau tung buoc theo dung dinh dang `[Agent]: Bước X/Y — [viec dang lam]`. Khong im lang qua 60 giay khi dang thuc hien mot nhiem vu.
- Phe duyet: luon cho chu so huu xem ke hoach ngan gon va doi phe duyet ro rang truoc khi hanh dong theo ke hoach.
- Giao tiep: tra loi ngan gon, ro rang, khong ruom ra. Khi dua lua chon, luon danh so 1, 2, 3. Mo dau bang quyet dinh chu so huu can dua ra, khong mo dau bang `Câu hỏi hay đấy,`, `Chắc chắn rồi,`, hoac `Tất nhiên.`
- Uy quyen: khi chuyen viec, trong mot dong noi ro specialist nao nhan viec va vi sao. Khong bia ket qua; neu that bai, noi thang that bai, nguyen nhan biet duoc, va buoc tiep theo.

Khi chu so huu yeu cau xac nhan danh tinh onboarding, tra loi mot dong theo mau: `Toi la Orchestrator, chu so huu la {owner}.`

{render_prompt6_policy("orchestrator", workspace)}

{render_prompt7_policy("orchestrator", workspace)}

{render_prompt9_policy("orchestrator", workspace)}

{render_prompt29_policy("orchestrator", project_dir)}

{render_prompt11_policy(workspace)}
"""


def render_owner_user_memory(config: CustomerConfig) -> str:
    return (
        "Gooros owner profile: "
        f"name={_owner_value(config.owner_name)}; "
        f"work={_owner_value(config.owner_work)}; "
        f"focus={_owner_value(config.owner_focus)}; "
        f"timezone={_owner_value(config.timezone)}; "
        f"working_hours={_owner_value(config.owner_working_hours)}; "
        f"important_people={_owner_value(config.owner_important_people)}; "
        f"cares_about={_owner_value(config.owner_cares_about)}."
    )


def render_team_memory(config: CustomerConfig) -> str:
    return (
        "Gooros Mission Control has five roles: Orchestrator is the default Hermes agent on Telegram and coordinates; "
        "Scout researches sources/trends; Scribe writes/edits; Reach handles marketing/growth/monetization; "
        "Dev handles engineering/automation/dashboard. The owner has highest authority. "
        "For multi-step work, Orchestrator must plan, delegate to the right specialist profiles, verify outputs, "
        "log activity, save long artifacts under ~/agent-mission-control/content/<agent>/, and return one owner-ready result. "
        "Prompt 7 requires any agent that receives work mainly owned by a teammate to name that teammate and transfer it through the handoff runtime."
    )


def render_operating_rules_memory() -> str:
    return (
        "Prompt 3 fixed Orchestrator operating rules: always answer in natural Vietnamese; "
        "for any multi-step task, send a short progress line before each step in the exact format "
        "`[Agent]: Bước X/Y — [viec dang lam]` and do not stay silent for more than 60 seconds while working; "
        "show the owner a concise plan and wait for clear approval before acting on that plan; "
        "keep replies brief and clear; number options as 1, 2, 3; open with the decision the owner needs to make, not background context; "
        "never open with `Câu hỏi hay đấy,`, `Chắc chắn rồi,`, or `Tất nhiên.`; "
        "when delegating, state in one line which specialist gets the work and why; never fabricate results; if anything fails, say it failed and explain the known cause and next step."
    )


def install_orchestrator_rules(paths: InstallPaths, config: CustomerConfig, runner: Runner | None = None) -> None:
    if runner and runner.dry_run:
        runner.log(
            "would merge Orchestrator identity into SOUL.md, write GOOROS_ORCHESTRATOR.md, "
            "create Prompt 6/7/9/11/29 orchestrator workspace, and seed memories/USER.md + MEMORY.md with owner profile, team policy, Prompt 3, Prompt 6 rules, Prompt 7 handoff awareness, Prompt 9 response logging, Prompt 11 Telegram topic routing plan, and Prompt 29 document archiving"
        )
        return
    workspace = prompt6_agent_workspace(paths.project_dir, "orchestrator")
    ensure_agent_workspace(paths, "orchestrator")
    text = render_orchestrator_soul(config, workspace, paths.project_dir)
    soul_failures = validate_prompt6_soul("orchestrator", text, workspace)
    soul_failures.extend(validate_prompt7_soul("orchestrator", text, workspace))
    soul_failures.extend(validate_prompt9_soul("orchestrator", text, workspace))
    soul_failures.extend(validate_prompt29_soul("orchestrator", text))
    soul_failures.extend(validate_prompt11_soul(text, workspace))
    if soul_failures:
        raise RuntimeError("orchestrator SOUL validation failed: " + "; ".join(soul_failures))
    _upsert_managed_markdown(paths.hermes_home / "SOUL.md", ORCHESTRATOR_SOUL_MARKER, text, prepend=True)
    atomic_write_text(
        paths.hermes_home / "GOOROS_ORCHESTRATOR.md",
        _managed_markdown_block("orchestrator rules audit copy v1", text),
    )
    memories = paths.hermes_home / "memories"
    _upsert_memory_entry(memories / "USER.md", OWNER_USER_MEMORY_MARKER, render_owner_user_memory(config))
    _upsert_memory_entry(memories / "MEMORY.md", TEAM_MEMORY_MARKER, render_team_memory(config))
    _upsert_memory_entry(memories / "MEMORY.md", OPERATING_RULES_MEMORY_MARKER, render_operating_rules_memory())
    _upsert_memory_entry(memories / "MEMORY.md", f"orchestrator {PROMPT6_MEMORY_MARKER}", render_prompt6_memory("orchestrator", workspace))
    _upsert_memory_entry(memories / "MEMORY.md", f"orchestrator {PROMPT7_MEMORY_MARKER}", render_prompt7_memory("orchestrator"))
    _upsert_memory_entry(memories / "MEMORY.md", f"orchestrator {PROMPT9_MEMORY_MARKER}", render_prompt9_memory("orchestrator"))
    _upsert_memory_entry(memories / "MEMORY.md", f"orchestrator {PROMPT29_MEMORY_MARKER}", render_prompt29_memory("orchestrator", paths.project_dir))
    _upsert_memory_entry(memories / "MEMORY.md", f"orchestrator {PROMPT11_MEMORY_MARKER}", render_prompt11_memory())


def install_cleanup_schedule(paths: InstallPaths, runner: Runner) -> str:
    cron_line = prompt10_cron_line(paths.project_dir, paths.hermes_home)
    begin = "# BEGIN GOOROS-HERMES-MANAGED log cleanup"
    end = "# END GOOROS-HERMES-MANAGED log cleanup"
    block = f"{begin}\n{cron_line}\n{end}"
    if runner.dry_run:
        runner.log(f"would install weekly log cleanup crontab block:\n{block}")
        return cron_line
    if os.name != "posix":
        runner.log("[logging] crontab install skipped on non-POSIX host; production Linux install will add it")
        return cron_line
    if not shutil.which("crontab"):
        raise RuntimeError("crontab is required to install weekly Gooros log cleanup")
    result = runner.run(["crontab", "-l"], capture=True, check=False, timeout=30)
    current = result.stdout if result.returncode == 0 else ""
    pattern = re.compile(re.escape(begin) + r".*?" + re.escape(end) + r"\s*", re.S)
    cleaned = pattern.sub("", current).rstrip()
    new_text = (cleaned + "\n\n" if cleaned else "") + block + "\n"
    runner.run(["crontab", "-"], input_text=new_text, timeout=30)
    return cron_line


def verify_prompt10_log_retention_live(runner: Runner, paths: InstallPaths, *, cron_line: str | None = None) -> None:
    if runner.dry_run:
        runner.log("would run cleanup-logs.sh once, summarize deleted/remaining rows, and write Prompt 10 retention report")
        return
    shared = paths.hermes_home / "agents" / "_shared"
    script = shared / "cleanup-logs.sh"
    db_path = paths.project_dir / "agent-logs.db"
    cron = cron_line or prompt10_cron_line(paths.project_dir, paths.hermes_home)
    errors: list[str] = []
    script_text = script.read_text(encoding="utf-8", errors="replace") if script.exists() else ""
    if not script.exists():
        errors.append("cleanup-logs.sh missing")
    else:
        errors.extend(validate_prompt10_script(script_text))
        if os.name == "posix" and not (script.stat().st_mode & 0o111):
            errors.append("cleanup-logs.sh is not executable")
    output = ""
    summary = {"deleted": 0, "remaining": 0, "retention_days": PROMPT10_RETENTION_DAYS}
    returncode = None
    if not errors:
        runner.log("[prompt10] Bước 1/1 — chạy cleanup-logs.sh một lần và ghi báo cáo lưu trữ log")
        result = runner.run(
            ["bash", str(script)],
            capture=True,
            check=False,
            timeout=120,
            env={"AGENT_LOG_DB": str(db_path)},
        )
        returncode = result.returncode
        output = (result.stdout or result.stderr or "").strip()
        if result.returncode != 0:
            errors.append(f"cleanup-logs.sh returned {result.returncode}: {output[:500]}")
        else:
            try:
                summary = parse_cleanup_summary(output)
            except Exception as exc:
                errors.append(f"cleanup summary invalid: {exc}")
            if summary.get("retention_days") != PROMPT10_RETENTION_DAYS:
                errors.append("cleanup did not use 7 retention days")
            runner.log(f"[logging] cleanup summary: {output}")
    report: dict[str, object] = {
        "prompt": "Prompt 10",
        "version": VERSION,
        "status": "failed" if errors else "passed",
        "retention_days": int(summary.get("retention_days", PROMPT10_RETENTION_DAYS)),
        "deleted": int(summary.get("deleted", 0)),
        "remaining": int(summary.get("remaining", 0)),
        "cleanup_output": output,
        "returncode": returncode,
        "agent_log_db": str(db_path),
        "script_path": str(script),
        "cron_line": cron,
        "script_checks": errors,
        "report_json": str(prompt10_report_json_path(paths.project_dir)),
        "report_markdown": str(prompt10_report_markdown_path(paths.project_dir)),
    }
    _write_prompt10_report(paths, report)
    if errors:
        raise RuntimeError("Prompt 10 verification failed: " + "; ".join(errors))


def install_logging(paths: InstallPaths, runner: Runner) -> None:
    install_shared_scripts(paths, runner)
    if runner.dry_run:
        runner.log("would smoke-test log-task-local.sh")
        install_cleanup_schedule(paths, runner)
        verify_prompt10_log_retention_live(runner, paths)
        return
    runner.run(["bash", str(paths.hermes_home / "agents" / "_shared" / "log-task-local.sh"), "dev", "installed Gooros logging system", "completed", "installer"], timeout=30)
    cron_line = install_cleanup_schedule(paths, runner)
    verify_prompt10_log_retention_live(runner, paths, cron_line=cron_line)


def verify_prompt13_profile_isolation_live(runner: Runner, paths: InstallPaths) -> None:
    if runner.dry_run:
        runner.log("would verify Prompt 13: four specialist profiles exist, have SOUL/memory, and have no messaging platforms or Telegram env")
        return
    root_env = read_env_values(paths.hermes_home / ".env")
    root_has_telegram_bot = bool(root_env.get("TELEGRAM_BOT_TOKEN"))
    legacy_orchestrator_profile = paths.hermes_home / "profiles" / "orchestrator"
    checks = [validate_specialist_profile_isolation(paths.hermes_home, agent) for agent in SPECIALISTS]
    report: dict[str, object] = {
        "prompt": "Prompt 13",
        "version": VERSION,
        "status": "running",
        "orchestrator_keeps_single_bot": root_has_telegram_bot and not legacy_orchestrator_profile.exists(),
        "root_env_path": str(paths.hermes_home / ".env"),
        "root_has_telegram_bot": root_has_telegram_bot,
        "legacy_orchestrator_profile_exists": legacy_orchestrator_profile.exists(),
        "checks": checks,
        "report_json": str(prompt13_report_json_path(paths.project_dir)),
        "report_markdown": str(prompt13_report_markdown_path(paths.project_dir)),
    }
    errors: list[str] = []
    if not root_has_telegram_bot:
        errors.append("root/default Orchestrator .env does not contain TELEGRAM_BOT_TOKEN")
    if legacy_orchestrator_profile.exists():
        errors.append("profiles/orchestrator exists; Orchestrator must remain the root/default agent")
    for item in checks:
        errors.extend(str(error) for error in item.get("errors", []))
    report["status"] = "failed" if errors else "passed"
    report_errors = validate_prompt13_report(report)
    if errors:
        report_errors = [failure for failure in report_errors if "report status is not passed" not in failure]
    _write_prompt13_report(paths, report)
    if errors or report_errors:
        raise RuntimeError("Prompt 13 profile isolation verification failed: " + "; ".join(errors + report_errors))


def _gateway_status_healthy(runner: Runner, *, systemd: bool = False) -> tuple[bool, str, list[str]]:
    commands = [["hermes", "gateway", "--accept-hooks", "status", "--deep"]]
    if systemd or os.name == "posix":
        commands.append(["hermes", "gateway", "--accept-hooks", "status", "--deep", "--system"])
    details: list[str] = []
    for cmd in commands:
        result = runner.run(cmd, capture=True, check=False, timeout=60, env=_gateway_env())
        text = (result.stdout or result.stderr or "").strip()
        if result.returncode == 0:
            return True, text, cmd
        if text:
            details.append(text)
    return False, " | ".join(details[:2]), commands[-1]


def verify_prompt12_telegram_group_live(
    runner: Runner,
    paths: InstallPaths,
    config: CustomerConfig,
    *,
    config_path: Path,
    config_token_line_preserved: bool,
    gateway_restart_attempted: bool,
    systemd: bool = False,
) -> None:
    if runner.dry_run:
        runner.log("would verify Prompt 12: Hermes config path, Telegram group allowlist, gateway health, and owner round-trip instruction")
        return
    errors: list[str] = []
    config_found = config_path.exists()
    config_text = config_path.read_text(encoding="utf-8", errors="replace") if config_found else ""
    if not config_found:
        errors.append(f"Hermes config path missing: {config_path}")
    else:
        errors.extend(validate_telegram_group_config_text(config_text, config.telegram_chat_id))
    telegram_env = read_env_values(paths.hermes_home / ".env")
    token_present = bool(telegram_env.get("TELEGRAM_BOT_TOKEN"))
    if not token_present:
        errors.append("TELEGRAM_BOT_TOKEN missing from Hermes .env")
    if not config_token_line_preserved:
        errors.append("existing platforms.telegram.token line changed while merging Prompt 12 group config")
    gateway_healthy, gateway_status, gateway_status_command = _gateway_status_healthy(runner, systemd=systemd)
    if not gateway_healthy:
        errors.append("Hermes gateway is not healthy after restart")
    config_failures = validate_telegram_group_config_text(config_text, config.telegram_chat_id) if config_text else []
    report: dict[str, object] = {
        "prompt": "Prompt 12",
        "version": VERSION,
        "status": "failed" if errors else "passed",
        "hermes_config_path": str(config_path),
        "hermes_config_found": config_found,
        "telegram_group_chat_id": config.telegram_chat_id,
        "telegram_env_token_present": token_present,
        "config_token_line_preserved": config_token_line_preserved,
        "require_mention_false": "platforms.telegram.require_mention is not false" not in config_failures,
        "group_chat_allowed": not any("group_allowed_chats" in failure for failure in config_failures),
        "gateway_restart_attempted": gateway_restart_attempted,
        "gateway_healthy": gateway_healthy,
        "gateway_status_command": gateway_status_command,
        "gateway_status": gateway_status,
        "owner_round_trip_mode": "manual_owner_message_required",
        "owner_round_trip_verified": False,
        "owner_round_trip_instruction": (
            'Owner acceptance: send "xin chao" or "xin chào" in any Telegram topic. '
            "The bot should answer there; that proves inbound group permission is live."
        ),
        "errors": errors,
        "report_json": str(prompt12_report_json_path(paths.project_dir)),
        "report_markdown": str(prompt12_report_markdown_path(paths.project_dir)),
    }
    report_errors = validate_prompt12_report(report, config=config)
    if errors:
        report_errors = [failure for failure in report_errors if "report status is not passed" not in failure]
    _write_prompt12_report(paths, report)
    if errors or report_errors:
        raise RuntimeError("Prompt 12 verification failed: " + "; ".join(errors + report_errors))


def _write_prompt15_report(paths: InstallPaths, report: dict[str, object]) -> None:
    ensure_dir(prompt15_report_json_path(paths.project_dir).parent)
    atomic_write_json(prompt15_report_json_path(paths.project_dir), report, mode=0o600)
    atomic_write_text(prompt15_report_markdown_path(paths.project_dir), render_prompt15_markdown_report(report), mode=0o600)


def install_prompt15_routing_plugin(runner: Runner, paths: InstallPaths, config: CustomerConfig) -> None:
    if runner.dry_run:
        runner.log(
            "would create exact Prompt 15 telegram_topic_profiles plugin under ~/.hermes/plugins "
            "with four specialist topic routes only; would not enable plugin or restart gateway"
        )
        return
    plugin_dir = prompt15_plugin_dir(paths.hermes_home)
    ensure_dir(plugin_dir)
    expected_files = {"plugin.yaml", "topics.json", "__init__.py"}
    for path in plugin_dir.iterdir():
        if path.is_file() and path.name not in expected_files:
            path.unlink()
    atomic_write_text(plugin_dir / "plugin.yaml", PROMPT15_PLUGIN_YAML, mode=0o600)
    atomic_write_json(plugin_dir / "topics.json", render_prompt15_topics(config), mode=0o600)
    atomic_write_text(plugin_dir / "__init__.py", PROMPT15_INIT_PY, mode=0o600)
    errors = validate_prompt15_plugin(plugin_dir, config, hermes_home=paths.hermes_home)
    topics = render_prompt15_topics(config)["topics"]
    report: dict[str, object] = {
        "prompt": "Prompt 15",
        "version": VERSION,
        "status": "failed" if errors else "passed",
        "plugin_dir": str(plugin_dir),
        "out_of_tree": True,
        "plugin_yaml_exists": (plugin_dir / "plugin.yaml").exists(),
        "topics_json_exists": (plugin_dir / "topics.json").exists(),
        "init_py_exists": (plugin_dir / "__init__.py").exists(),
        "chat_id": str(config.telegram_chat_id),
        "topics": topics,
        "command_thread_included": False,
        "plugin_enabled": False,
        "gateway_restarted": False,
        "files_written": ["plugin.yaml", "topics.json", "__init__.py"],
        "errors": errors,
        "report_json": str(prompt15_report_json_path(paths.project_dir)),
        "report_markdown": str(prompt15_report_markdown_path(paths.project_dir)),
    }
    report_errors = validate_prompt15_report(report, config=config, hermes_home=paths.hermes_home)
    if report_errors:
        report["status"] = "failed"
        report["errors"] = errors + report_errors
    _write_prompt15_report(paths, report)
    if errors or report_errors:
        raise RuntimeError("Prompt 15 routing plugin creation failed: " + "; ".join(errors + report_errors))


def _clip_command_output(text: str | None, limit: int = 1200) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def _command_record(reason: str, command: list[str], result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    return {
        "reason": reason,
        "command": list(command),
        "returncode": result.returncode,
        "stdout": _clip_command_output(result.stdout),
        "stderr": _clip_command_output(result.stderr),
    }


def _combined_output(result: subprocess.CompletedProcess[str]) -> str:
    return ((result.stdout or "") + "\n" + (result.stderr or "")).strip()


def _write_prompt16_report(paths: InstallPaths, report: dict[str, object]) -> None:
    ensure_dir(prompt16_report_json_path(paths.project_dir).parent)
    atomic_write_json(prompt16_report_json_path(paths.project_dir), report, mode=0o600)
    atomic_write_text(prompt16_report_markdown_path(paths.project_dir), render_prompt16_markdown_report(report), mode=0o600)


def _scrub_specialist_messaging_for_prompt16(paths: InstallPaths) -> tuple[dict[str, object], bool]:
    scrubbed: dict[str, object] = {}
    all_verified = True
    for agent in SPECIALISTS:
        profile_dir = paths.hermes_home / "profiles" / agent
        removed_config = scrub_profile_config(profile_dir / "config.yaml")
        removed_env = scrub_profile_env(profile_dir / ".env")
        check = validate_specialist_profile_isolation(paths.hermes_home, agent)
        verified = bool(check.get("verified"))
        all_verified = all_verified and verified
        scrubbed[agent] = {
            "profile_path": str(profile_dir),
            "removed_config_keys": removed_config,
            "removed_env_keys": removed_env,
            "verified": verified,
            "errors": check.get("errors", []),
        }
    return scrubbed, all_verified


def activate_prompt16_multi_agent_mode(runner: Runner, paths: InstallPaths) -> None:
    if runner.dry_run:
        runner.log(
            "would run Prompt 16 activation: hermes plugins enable telegram_topic_profiles; "
            "hermes config set multiplex_profiles true; hermes gateway restart; then verify plugins list/config/status"
        )
        return
    plugin_dir = paths.hermes_home / "plugins" / "telegram_topic_profiles"
    plugin_present = (plugin_dir / "plugin.yaml").exists() and (plugin_dir / "__init__.py").exists()
    errors: list[str] = []
    if not plugin_present:
        errors.append(f"routing plugin files missing before Prompt 16 activation: {plugin_dir}")

    enable_result = runner.run(PROMPT16_ENABLE_COMMAND, check=False, capture=True, timeout=120, input_text="n\n")
    config_set_result = runner.run(PROMPT16_CONFIG_SET_COMMAND, check=False, capture=True, timeout=30)

    gateway_restart_attempts: list[dict[str, object]] = []
    gateway_status_checks: list[dict[str, object]] = []

    def restart_and_status(reason: str) -> bool:
        restart_result = runner.run(PROMPT16_GATEWAY_RESTART_COMMAND, check=False, capture=True, timeout=120, env=_gateway_env())
        gateway_restart_attempts.append(_command_record(reason, PROMPT16_GATEWAY_RESTART_COMMAND, restart_result))
        status_result = runner.run(PROMPT16_GATEWAY_STATUS_COMMAND, check=False, capture=True, timeout=60, env=_gateway_env())
        gateway_status_checks.append(_command_record(reason, PROMPT16_GATEWAY_STATUS_COMMAND, status_result))
        return restart_result.returncode == 0 and status_result.returncode == 0

    gateway_ok = restart_and_status("initial")
    specialist_scrub_attempted = False
    specialist_scrubbed: dict[str, object] = {}
    specialist_profiles_isolated_after_scrub = True
    if not gateway_ok:
        specialist_scrub_attempted = True
        specialist_scrubbed, specialist_profiles_isolated_after_scrub = _scrub_specialist_messaging_for_prompt16(paths)
        gateway_ok = restart_and_status("after_specialist_platform_scrub")

    plugins_list_result = runner.run(PROMPT16_PLUGINS_LIST_COMMAND, check=False, capture=True, timeout=30)
    config_get_result = runner.run(PROMPT16_CONFIG_GET_COMMAND, check=False, capture=True, timeout=30)
    plugin_enabled = plugins_list_result.returncode == 0 and parse_plugin_enabled(_combined_output(plugins_list_result))
    multiplex_enabled = config_get_result.returncode == 0 and parse_multiplex_profiles_enabled(_combined_output(config_get_result))
    gateway_restarted = bool(gateway_restart_attempts and gateway_restart_attempts[-1].get("returncode") == 0)
    gateway_status_ok = bool(gateway_status_checks and gateway_status_checks[-1].get("returncode") == 0)

    if enable_result.returncode != 0 and not plugin_enabled:
        errors.append("hermes plugins enable telegram_topic_profiles failed and plugins list did not show it enabled")
    if plugins_list_result.returncode != 0:
        errors.append("hermes plugins list failed; plugin enabled status is not verifiable")
    if not plugin_enabled:
        errors.append("telegram_topic_profiles is not enabled according to hermes plugins list")
    if config_set_result.returncode != 0 and not multiplex_enabled:
        errors.append("hermes config set multiplex_profiles true failed and config get did not confirm true")
    if config_get_result.returncode != 0:
        errors.append("hermes config get multiplex_profiles failed; multi-profile status is not verifiable")
    if not multiplex_enabled:
        errors.append("multiplex_profiles is not true according to hermes config get")
    if not gateway_restarted:
        errors.append("hermes gateway restart did not complete successfully")
    if not gateway_status_ok:
        errors.append("Hermes gateway status --deep did not confirm a healthy gateway after restart")
    if specialist_scrub_attempted and not specialist_profiles_isolated_after_scrub:
        errors.append("specialist profile messaging-platform scrub did not leave all specialists isolated")

    report: dict[str, object] = {
        "prompt": "Prompt 16",
        "version": VERSION,
        "status": "failed" if errors else "passed",
        "plugin_present_before_enable": plugin_present,
        "plugin_enable_attempted": True,
        "plugin_enable_command": PROMPT16_ENABLE_COMMAND,
        "plugin_enable_result": _command_record("enable", PROMPT16_ENABLE_COMMAND, enable_result),
        "plugin_enabled": plugin_enabled,
        "plugins_list_command": PROMPT16_PLUGINS_LIST_COMMAND,
        "plugins_list_result": _command_record("verify_enabled", PROMPT16_PLUGINS_LIST_COMMAND, plugins_list_result),
        "multiplex_set_attempted": True,
        "config_set_command": PROMPT16_CONFIG_SET_COMMAND,
        "config_set_result": _command_record("set_multiplex", PROMPT16_CONFIG_SET_COMMAND, config_set_result),
        "multiplex_profiles_enabled": multiplex_enabled,
        "config_get_command": PROMPT16_CONFIG_GET_COMMAND,
        "config_get_result": _command_record("verify_multiplex", PROMPT16_CONFIG_GET_COMMAND, config_get_result),
        "gateway_restart_attempts": gateway_restart_attempts,
        "gateway_restarted": gateway_restarted,
        "gateway_status_command": PROMPT16_GATEWAY_STATUS_COMMAND,
        "gateway_status_checks": gateway_status_checks,
        "gateway_status_ok": gateway_status_ok,
        "specialist_scrub_attempted": specialist_scrub_attempted,
        "specialist_profiles_isolated_after_scrub": specialist_profiles_isolated_after_scrub,
        "specialist_scrubbed": specialist_scrubbed,
        "errors": errors,
        "report_json": str(prompt16_report_json_path(paths.project_dir)),
        "report_markdown": str(prompt16_report_markdown_path(paths.project_dir)),
    }
    report_errors = validate_prompt16_report(report)
    if report_errors:
        report["status"] = "failed"
        report["errors"] = errors + report_errors
    _write_prompt16_report(paths, report)
    if errors or report_errors:
        raise RuntimeError("Prompt 16 multi-agent activation failed: " + "; ".join(errors + report_errors))


def _write_prompt17_report(paths: InstallPaths, report: dict[str, object]) -> None:
    ensure_dir(prompt17_report_json_path(paths.project_dir).parent)
    atomic_write_json(prompt17_report_json_path(paths.project_dir), report, mode=0o600)
    atomic_write_text(prompt17_report_markdown_path(paths.project_dir), render_prompt17_markdown_report(report), mode=0o600)


def verify_prompt17_telegram_routing_audit_live(runner: Runner, paths: InstallPaths, config: CustomerConfig) -> None:
    if runner.dry_run:
        runner.log(
            "would audit Prompt 17: top-level multiplex_profiles, group_allowed_chats, enabled plugin, "
            "four specialist topic routes with #command omitted, and specialist profile SOUL/platform isolation"
        )
        return
    errors: list[str] = []
    config_path_result = runner.run(["hermes", "config", "path"], capture=True, check=False, timeout=30)
    config_path = (
        Path(config_path_result.stdout.strip()).expanduser()
        if config_path_result.returncode == 0 and config_path_result.stdout.strip()
        else paths.hermes_home / "config.yaml"
    )
    config_text = config_path.read_text(encoding="utf-8", errors="replace") if config_path.exists() else ""
    multiplex_top_level, multiplex_nested_gateway = top_level_multiplex_profiles(config_text)
    group_allowed = bool(config_text and group_allowed_chats_contains(config_text, config.telegram_chat_id))
    if not config_path.exists():
        errors.append(f"Hermes config missing for Prompt 17 audit: {config_path}")
    if not multiplex_top_level:
        errors.append("multiplex_profiles is not true at top level of Hermes config.yaml")
    if multiplex_nested_gateway:
        errors.append("multiplex_profiles appears nested under gateway; Prompt 17 requires top-level config")
    if not group_allowed:
        errors.append("platforms.telegram.group_allowed_chats does not contain the configured group chat ID")

    plugin_dir = paths.hermes_home / "plugins" / "telegram_topic_profiles"
    plugin_exists = (plugin_dir / "plugin.yaml").exists() and (plugin_dir / "__init__.py").exists()
    if not plugin_exists:
        errors.append(f"telegram_topic_profiles plugin missing: {plugin_dir}")
    plugins_list_result = runner.run(["hermes", "plugins", "list"], capture=True, check=False, timeout=30)
    plugin_enabled = plugins_list_result.returncode == 0 and parse_plugin_enabled(_combined_output(plugins_list_result))
    if plugins_list_result.returncode != 0:
        errors.append("hermes plugins list failed during Prompt 17 audit")
    elif not plugin_enabled:
        errors.append("telegram_topic_profiles is not enabled according to hermes plugins list")

    topics_path = plugin_dir / "topics.json"
    topic_map: dict[str, str] = {}
    if topics_path.exists():
        try:
            topics_data = json.loads(topics_path.read_text(encoding="utf-8"))
            raw_topics = topics_data.get("topics") if isinstance(topics_data, dict) else {}
            if isinstance(raw_topics, dict):
                topic_map = {str(key): str(value).strip() for key, value in raw_topics.items()}
            else:
                errors.append("topics.json topics is not an object")
        except Exception as exc:
            errors.append(f"topics.json invalid during Prompt 17 audit: {exc}")
    else:
        errors.append("telegram_topic_profiles topics.json missing")
    topic_errors = validate_prompt17_topics(topic_map, config) if topic_map else ["Prompt 17 topics.json has no routes to audit"]
    errors.extend(topic_errors)
    command_thread = str(config.thread_command or "")
    command_omitted = bool(command_thread) and command_thread not in topic_map and "orchestrator" not in {value.lower() for value in topic_map.values()}
    topics_match = not topic_errors

    profile_checks = [audit_prompt17_profile(paths.hermes_home, agent) for agent in SPECIALISTS]
    for item in profile_checks:
        if not item.get("verified"):
            errors.extend(str(error) for error in item.get("errors", []))

    report: dict[str, object] = {
        "prompt": "Prompt 17",
        "version": VERSION,
        "status": "failed" if errors else "passed",
        "config_path": str(config_path),
        "config_exists": config_path.exists(),
        "multiplex_profiles_top_level": multiplex_top_level,
        "multiplex_profiles_nested_under_gateway": multiplex_nested_gateway,
        "telegram_group_chat_id": config.telegram_chat_id,
        "group_allowed": group_allowed,
        "plugin_dir": str(plugin_dir),
        "plugin_exists": plugin_exists,
        "plugin_enabled": plugin_enabled,
        "plugins_list_returncode": plugins_list_result.returncode,
        "topics_path": str(topics_path),
        "topics": topic_map,
        "expected_topics": expected_prompt17_topic_routes(config),
        "topics_match": topics_match,
        "command_omitted_from_topics": command_omitted,
        "route_table": prompt17_audit_table(config),
        "profile_checks": profile_checks,
        "symptom_guidance": PROMPT17_SYMPTOM_GUIDANCE,
        "errors": errors,
        "report_json": str(prompt17_report_json_path(paths.project_dir)),
        "report_markdown": str(prompt17_report_markdown_path(paths.project_dir)),
    }
    report_errors = validate_prompt17_report(report)
    if report_errors:
        report["status"] = "failed"
        report["errors"] = errors + report_errors
    _write_prompt17_report(paths, report)
    if errors or report_errors:
        raise RuntimeError("Prompt 17 Telegram routing audit failed: " + "; ".join(errors + report_errors))


def install_telegram_routing(runner: Runner, paths: InstallPaths, config: CustomerConfig, *, systemd: bool = False) -> None:
    if runner.dry_run:
        runner.log("would merge Hermes Telegram env/config, install telegram_topic_profiles, run Prompt 16 activation, and write Prompt 12/16 reports")
        return
    home_channel = config.telegram_home_channel or (
        f"telegram:{config.telegram_chat_id}:{config.thread_command}" if config.thread_command else f"telegram:{config.telegram_chat_id}"
    )
    merge_env_values(
        paths.hermes_home / ".env",
        {
            "TELEGRAM_BOT_TOKEN": config.telegram_bot_token,
            "TELEGRAM_HOME_CHANNEL": home_channel,
            "TELEGRAM_ALLOWED_USERS": config.telegram_allowed_users,
        },
    )
    config_path_result = runner.run(["hermes", "config", "path"], capture=True, check=False, timeout=30)
    config_path = Path(config_path_result.stdout.strip()).expanduser() if config_path_result.returncode == 0 and config_path_result.stdout.strip() else paths.hermes_home / "config.yaml"
    config_before = config_path.read_text(encoding="utf-8", errors="replace") if config_path.exists() else ""
    token_lines_before = telegram_token_lines(config_before)
    merge_telegram_group_config(config_path, config.telegram_chat_id)
    config_after = config_path.read_text(encoding="utf-8", errors="replace") if config_path.exists() else ""
    token_line_preserved = token_lines_before == telegram_token_lines(config_after)
    plugin_dir = paths.hermes_home / "plugins" / "telegram_topic_profiles"
    ensure_dir(plugin_dir)
    copy_file(asset_path("plugins", "telegram_topic_profiles", "plugin.yaml"), plugin_dir / "plugin.yaml")
    copy_file(asset_path("plugins", "telegram_topic_profiles", "__init__.py"), plugin_dir / "__init__.py")
    topics = {
        "chat_id": config.telegram_chat_id,
        "board_db": str(paths.project_dir / "board.db"),
        "onboarding": {
            "enabled": True,
            "min_deep_questions": 7,
            "max_deep_questions": 9,
            "state_path": str(paths.data_dir / "telegram-onboarding-state.json"),
            "owner_profile_path": str(paths.config_dir / "owner-profile.json"),
            "user_memory_path": str(paths.hermes_home / "memories" / "USER.md"),
        },
        "topics": {
            config.thread_scout: "scout",
            config.thread_scribe: "scribe",
            config.thread_reach: "reach",
            config.thread_dev: "dev",
        },
    }
    atomic_write_json(plugin_dir / "topics.json", topics, mode=0o600)
    activate_prompt16_multi_agent_mode(runner, paths)
    verify_prompt12_telegram_group_live(
        runner,
        paths,
        config,
        config_path=config_path,
        config_token_line_preserved=token_line_preserved,
        gateway_restart_attempted=True,
        systemd=systemd,
    )


def _multiplex_profiles_enabled(runner: Runner, paths: InstallPaths) -> bool:
    result = runner.run(["hermes", "config", "get", "multiplex_profiles"], capture=True, check=False, timeout=30)
    text = ((result.stdout or "") + "\n" + (result.stderr or "")).strip().lower()
    if result.returncode == 0 and text:
        return text in {"true", "1", "yes", "on"} or "true" in text
    config_path = paths.hermes_home / "config.yaml"
    if config_path.exists():
        config_text = config_path.read_text(encoding="utf-8", errors="replace").lower()
        return "multiplex_profiles" in config_text and "true" in config_text
    return False


def verify_prompt11_topic_routing_live(runner: Runner, paths: InstallPaths, config: CustomerConfig) -> None:
    if runner.dry_run:
        runner.log("would verify Prompt 11: #command/#scout/#scribe/#reach/#dev topic map, plugin hook routing, multiplex_profiles=true, and report")
        return
    plugin_dir = paths.hermes_home / "plugins" / "telegram_topic_profiles"
    plugin_path = plugin_dir / "__init__.py"
    topics_path = plugin_dir / "topics.json"
    topic_routes: dict[str, str] = {}
    errors: list[str] = []
    plugin_installed = plugin_path.exists() and (plugin_dir / "plugin.yaml").exists()
    if not plugin_installed:
        errors.append("telegram_topic_profiles plugin files missing")
    if not topics_path.exists():
        errors.append("telegram_topic_profiles topics.json missing")
    else:
        try:
            topics_data = json.loads(topics_path.read_text(encoding="utf-8"))
            raw_routes = topics_data.get("topics", {}) if isinstance(topics_data, dict) else {}
            if isinstance(raw_routes, dict):
                topic_routes = {str(key): str(value) for key, value in raw_routes.items()}
            else:
                errors.append("topics.json topics is not an object")
            errors.extend(validate_topic_routes(topic_routes, config))
            if str(topics_data.get("chat_id", "")).strip() != config.telegram_chat_id:
                errors.append("topics.json chat_id does not match configured Telegram group")
        except Exception as exc:
            errors.append(f"topics.json invalid: {exc}")
    multiplex_ok = _multiplex_profiles_enabled(runner, paths)
    if not multiplex_ok:
        errors.append("Hermes multiplex_profiles is not true")

    route_checks: list[dict[str, object]] = []
    if plugin_installed and topic_routes:
        try:
            spec = importlib.util.spec_from_file_location("telegram_topic_profiles_prompt11_verify", plugin_path)
            if spec is None or spec.loader is None:
                raise RuntimeError("could not load telegram_topic_profiles plugin spec")
            plugin = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(plugin)
            original_load_map = plugin._load_map
            plugin._load_map = lambda: (config.telegram_chat_id, topic_routes, str(paths.project_dir / "board.db"), {"enabled": False})

            class Platform:
                value = "telegram"

            channel_threads = {
                "command": config.thread_command,
                "scout": config.thread_scout,
                "scribe": config.thread_scribe,
                "reach": config.thread_reach,
                "dev": config.thread_dev,
            }

            try:
                for order, (channel, thread_id) in enumerate(channel_threads.items(), start=1):
                    expected = PROMPT11_CHANNELS[channel]

                    class Source:
                        pass

                    class Event:
                        pass

                    event = Event()
                    event.source = Source()
                    event.source.platform = Platform()
                    event.source.chat_id = config.telegram_chat_id
                    event.source.thread_id = str(thread_id)
                    event.source.message_id = f"prompt11-{order}"
                    event.source.profile = None
                    event.text = f"Prompt 11 neutral route check for {channel}"
                    plugin._route(event=event)
                    actual_profile = getattr(event.source, "profile", None)
                    if expected == "orchestrator":
                        verified = actual_profile in (None, "", "orchestrator")
                        display_actual = "orchestrator-root" if actual_profile in (None, "") else str(actual_profile)
                    else:
                        verified = actual_profile == expected
                        display_actual = str(actual_profile)
                    route_checks.append(
                        {
                            "order": order,
                            "channel": channel,
                            "thread_id": str(thread_id),
                            "expected_agent": expected,
                            "actual_profile": display_actual,
                            "verified": verified,
                            "errors": [] if verified else [f"actual profile {display_actual!r}"],
                        }
                    )
                    if not verified:
                        errors.append(f"Prompt 11 route check failed for #{channel}: got {display_actual}, expected {expected}")
            finally:
                plugin._load_map = original_load_map
        except Exception as exc:
            errors.append(f"Prompt 11 plugin route simulation failed: {exc}")
    report: dict[str, object] = {
        "prompt": "Prompt 11",
        "version": VERSION,
        "status": "failed" if errors else "passed",
        "plugin_installed": plugin_installed,
        "multiplex_profiles_enabled": multiplex_ok,
        "telegram_group_chat_id": config.telegram_chat_id,
        "topic_routes": topic_routes,
        "route_checks": route_checks,
        "errors": errors,
        "report_json": str(prompt11_report_json_path(paths.project_dir)),
        "report_markdown": str(prompt11_report_markdown_path(paths.project_dir)),
    }
    if not errors:
        errors.extend(validate_prompt11_report(report, config=config))
        report["status"] = "failed" if errors else "passed"
        report["errors"] = errors
    _write_prompt11_report(paths, report)
    if errors:
        raise RuntimeError("Prompt 11 verification failed: " + "; ".join(errors))


def backup_dashboard_file(path: Path, backups_dir: Path, dashboard_version: str = DASHBOARD_VERSION) -> Path | None:
    if not path.exists():
        return None
    ensure_dir(backups_dir)
    stem = "index" if path.name == "index.html" else "server" if path.name == "server.py" else path.stem
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M")
    suffix = path.suffix or ".bak"
    target = backups_dir / f"{stem}_v{dashboard_version}_{stamp}{suffix}"
    counter = 2
    while target.exists():
        target = backups_dir / f"{stem}_v{dashboard_version}_{stamp}-{counter}{suffix}"
        counter += 1
    shutil.copy2(path, target)
    return target


def seed_prompt29_scout_note(paths: InstallPaths) -> Path:
    scout_dir = paths.project_dir / "content" / "scout"
    ensure_dir(scout_dir)
    existing = sorted(scout_dir.glob("*_prompt29-scout-research-note*.md"))
    if existing:
        return existing[0]
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    base = f"{stamp}_prompt29-scout-research-note"
    target = scout_dir / f"{base}.md"
    counter = 2
    while target.exists():
        target = scout_dir / f"{base}-v{counter}.md"
        counter += 1
    atomic_write_text(
        target,
        """# Prompt 29 Scout Research Note

## Finding
- The Content tab reads Markdown files directly from `~/agent-mission-control/content/<agent>/`.
- Long reusable Scout outputs should be stored in the Scout folder instead of being left only in chat.
- The filename, heading, and one-line confirmation rules make the document easy to find in Mission Control.

## Confirmation
Scout will use this folder for research notes, trend scans, source summaries, and reusable research briefs.
""",
        mode=0o644,
    )
    return target


def install_dashboard(paths: InstallPaths, runner: Runner | None = None) -> None:
    if runner and runner.dry_run:
        runner.log(f"would backup then install dashboard files into {paths.project_dir}")
        return
    ensure_dir(paths.project_dir)
    backups_dir = paths.project_dir / "backups"
    backup_dashboard_file(paths.project_dir / "server.py", backups_dir)
    backup_dashboard_file(paths.project_dir / "index.html", backups_dir)
    copy_file(asset_path("dashboard", "server.py"), paths.project_dir / "server.py", mode=0o755)
    build_live_dashboard(asset_path("dashboard", "template.html"), paths.project_dir / "index.html")
    copy_file(asset_path("dashboard", "template.html"), paths.project_dir / "template.html")
    backup_dashboard_file(paths.project_dir / "server.py", backups_dir)
    backup_dashboard_file(paths.project_dir / "index.html", backups_dir)
    for legacy_name in ("gooros-logo.png",):
        legacy_path = paths.project_dir / legacy_name
        asset = asset_path("dashboard", legacy_name)
        try:
            if legacy_path.exists() and asset.exists() and legacy_path.read_bytes() == asset.read_bytes():
                legacy_path.unlink()
        except OSError:
            pass
    for agent in AGENTS:
        ensure_dir(paths.project_dir / "content" / agent)
        ensure_dir(prompt6_agent_workspace(paths.project_dir, agent))
    seed_prompt29_scout_note(paths)
    ensure_dir(backups_dir)


def write_model_routing(paths: InstallPaths, combo_name: str, models: list[str], runner: Runner | None = None) -> None:
    if runner and runner.dry_run:
        runner.log(f"would write model-routing.json for 9Router combo {combo_name}")
        return
    if not models:
        raise RuntimeError("cannot write model routing without free 9Router models")
    premium_model = choose_9router_model(models)
    routed_models = [
        {
            "id": premium_model,
            "tier": "premium",
            "source": "9router-free-provider",
            "purpose": "complex reasoning, code, architecture, and multi-step tasks",
        }
    ]
    if combo_name != premium_model:
        routed_models.append(
            {
                "id": combo_name,
                "tier": "fast",
                "source": "gooros-round-robin-combo",
                "purpose": "short replies, summaries, rewrites, and lightweight formatting",
            }
        )
    required_providers = [
        {
            "id": spec.provider_id,
            "name": spec.display_name,
            "alias": spec.alias,
            "members": [
                model for model in models
                if model.startswith(f"{spec.alias}/") or model.startswith(f"{spec.provider_id}/")
            ],
        }
        for spec in REQUIRED_FREE_PROVIDERS
    ]
    data = {
        "policy": "9router-free-combo-round-robin",
        "combo": {
            "name": combo_name,
            "strategy": "round-robin",
            "priority": "deepseek-first",
            "members": models,
            "preferred_model": premium_model,
            "required_providers": required_providers,
        },
        "models": routed_models,
        "complex_keywords": ["code", "debug", "architecture", "strategy", "multi-step", "longform", "reason"],
        "simple_keywords": ["short", "quick", "summary", "format", "caption", "rewrite"],
    }
    atomic_write_json(paths.hermes_home / "agents" / "_shared" / "model-routing.json", data, mode=0o600)


def choose_9router_model(models: list[str]) -> str:
    return choose_router_model(models)


def configure_hermes_for_9router(runner: Runner, paths: InstallPaths, combo_name: str, api_key: str) -> str:
    if runner.dry_run:
        runner.log(f"would configure Hermes root + profiles -> 9Router combo {combo_name}")
        return combo_name
    if not api_key:
        raise RuntimeError("cannot configure Hermes for 9Router without a real 9Router API key")
    targets: list[tuple[str, Path, list[str]]] = [
        ("orchestrator", paths.hermes_home, ["hermes"]),
    ]
    for agent in SPECIALISTS:
        profile_dir = paths.hermes_home / "profiles" / agent
        if profile_dir.exists():
            targets.append((agent, profile_dir, ["hermes", "-p", agent]))
    for agent, root, command in targets:
        merge_env_values(root / ".env", {"OPENAI_API_KEY": api_key})
        runner.run(command + ["config", "set", "--force", "model.provider", "custom"], timeout=30)
        runner.run(command + ["config", "set", "--force", "model.base_url", "http://127.0.0.1:20128/v1"], timeout=30)
        runner.run(command + ["config", "set", "--force", "model.default", combo_name], timeout=30)
        runner.log(f"[9router] Hermes profile {agent} default model -> {combo_name}")
    return combo_name


def discover_9router_models() -> list[str]:
    discovery = discover_free_router_models()
    return discovery.models


def ensure_9router_hosted_combo(runner: Runner, paths: InstallPaths) -> tuple[str, list[str], str]:
    if runner.dry_run:
        models = ["dry-run/deepseek-free", "dry-run/qwen-free"]
        runner.log(
            f"would create/reconcile 9Router API key '{GOOROS_9ROUTER_API_KEY_NAME}', combo "
            f"{GOOROS_9ROUTER_COMBO_NAME}, and round-robin settings"
        )
        return GOOROS_9ROUTER_COMBO_NAME, models, "DRY_RUN_9ROUTER_API_KEY"
    key_info = ensure_router_api_key(GOOROS_9ROUTER_API_KEY_NAME, preferred_key=router_local_api_key(paths))
    api_key = str(key_info.get("key") or "").strip()
    if not api_key:
        raise RuntimeError("9Router did not return a real API key for Hermes")
    merge_env_values(paths.secrets_env, {"GOOROS_9ROUTER_API_KEY": api_key})
    discovery = discover_free_router_models()
    for warning in discovery.warnings:
        runner.log(f"[9router] model discovery warning: {warning}")
    if discovery.missing_required_providers:
        missing = ", ".join(discovery.missing_required_providers)
        required = ", ".join(f"{spec.display_name} ({spec.alias}/...)" for spec in REQUIRED_FREE_PROVIDERS)
        raise RuntimeError(
            "9Router free combo is incomplete. Gooros requires every free model exposed by "
            f"{required}; missing provider model list: {missing}. Check 9Router dashboard auth/network, "
            "then rerun gooros-hermes update."
        )
    for spec in REQUIRED_FREE_PROVIDERS:
        count = len(discovery.required_provider_models.get(spec.provider_id, []))
        runner.log(f"[9router] {spec.display_name} models in combo: {count}")
    if not discovery.models:
        raise RuntimeError(
            "9Router has no free models available for the Gooros combo. Connect at least one working free/free-tier "
            "provider in the 9Router dashboard, then rerun gooros-hermes update."
        )
    ensure_router_combo(GOOROS_9ROUTER_COMBO_NAME, discovery.models, kind="llm")
    ensure_router_round_robin(sticky_limit=1)
    return GOOROS_9ROUTER_COMBO_NAME, discovery.models, api_key


def _service_account() -> tuple[str, str, str, str]:
    if os.name != "posix":
        return "", "", "", ""
    import grp
    import pwd

    user = getpass.getuser()
    record = pwd.getpwnam(user)
    group = grp.getgrgid(record.pw_gid).gr_name
    path = os.environ.get("PATH", "")
    extras = ["/usr/local/bin", "/usr/bin", "/bin", str(Path(record.pw_dir) / ".local" / "bin")]
    for item in extras:
        if item and item not in path.split(":"):
            path = f"{path}:{item}" if path else item
    return user, group, record.pw_dir, path


def _required_bin(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    raise RuntimeError(f"required executable not found for systemd service: {name}")


def _candidate_9router_server_paths(router_bin: str) -> list[Path]:
    executable = Path(router_bin).expanduser().resolve()
    candidates: list[Path] = []
    for parent in (executable.parent, *executable.parents):
        candidates.append(parent / "app" / "server.js")
        candidates.append(parent / "node_modules" / "9router" / "app" / "server.js")
    return candidates


def resolve_9router_server_js(runner: Runner, router_bin: str) -> Path:
    candidates = _candidate_9router_server_paths(router_bin)
    if shutil.which("npm"):
        result = runner.run(["npm", "root", "-g"], capture=True, check=False, timeout=30)
        npm_root = (result.stdout or "").strip()
        if result.returncode == 0 and npm_root:
            candidates.append(Path(npm_root).expanduser() / "9router" / "app" / "server.js")
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    searched = "\n".join(str(path) for path in candidates[:8])
    raise RuntimeError(
        "9Router server entrypoint app/server.js was not found after npm install. "
        "The 9Router package layout may have changed; inspect the installed package before enabling systemd.\n"
        f"Searched:\n{searched}"
    )


def seed_router_management_env(paths: InstallPaths) -> None:
    password = router_initial_password(paths)
    if not password:
        return
    os.environ.setdefault("GOOROS_9ROUTER_INITIAL_PASSWORD", password)
    os.environ.setdefault("INITIAL_PASSWORD", password)


def install_systemd_services(runner: Runner, paths: InstallPaths, *, with_9router: bool) -> None:
    if os.name != "posix":
        return
    if runner.dry_run:
        runner.log("would install systemd services for mission control, Hermes dashboard, and optionally 9Router")
        return
    prefix = root_prefix()
    services = ["gooros-mission-control.service", "hermes-native-dashboard.service"]
    if with_9router:
        services.append("9router.service")
    user, group, home, service_path = _service_account()
    router_bin = _required_bin("9router") if with_9router else "/usr/bin/false"
    router_server = resolve_9router_server_js(runner, router_bin) if with_9router else Path("/usr/bin/false")
    replacements = {
        "%GOOROS_SERVICE_USER%": user,
        "%GOOROS_SERVICE_GROUP%": group,
        "%GOOROS_SERVICE_HOME%": home,
        "%GOOROS_SERVICE_PATH%": service_path,
        "%GOOROS_PROJECT_DIR%": str(paths.project_dir),
        "%GOOROS_PYTHON3%": _required_bin("python3"),
        "%GOOROS_HERMES_BIN%": _required_bin("hermes"),
        "%GOOROS_9ROUTER_BIN%": router_bin,
        "%GOOROS_NODE_BIN%": _required_bin("node") if with_9router else "/usr/bin/false",
        "%GOOROS_9ROUTER_APP_DIR%": str(router_server.parent),
        "%GOOROS_9ROUTER_SERVER_JS%": str(router_server),
    }
    for service in services:
        src = asset_path("proxy", "systemd", service)
        text = src.read_text(encoding="utf-8")
        for old, new in replacements.items():
            text = text.replace(old, new)
        if "%GOOROS_" in text:
            raise RuntimeError(f"unrendered systemd placeholder in {service}")
        tmp = Path("/tmp") / service
        atomic_write_text(tmp, text)
        runner.run(prefix + ["install", "-m", "0644", str(tmp), f"/etc/systemd/system/{service}"])
    runner.run(prefix + ["systemctl", "daemon-reload"], timeout=60)
    for service in services:
        runner.run(prefix + ["systemctl", "enable", "--now", service], timeout=120, check=False)
    for service in services:
        runner.run(prefix + ["systemctl", "restart", service], timeout=120, check=False)


def _gateway_system_args(systemd: bool) -> list[str]:
    return ["--system"] if systemd and os.name == "posix" else []


def _gateway_env() -> dict[str, str]:
    return {"HERMES_ACCEPT_HOOKS": "1"}


def restart_gateway(runner: Runner, *, systemd: bool = False) -> None:
    if runner.dry_run:
        runner.log("would ensure Hermes gateway service is installed, started, and restarted")
        return
    system_args = _gateway_system_args(systemd)
    base = ["hermes", "gateway", "--accept-hooks"]
    restart = runner.run(base + ["restart", *system_args], check=False, capture=True, timeout=120, env=_gateway_env())
    if restart.returncode != 0:
        detail = (restart.stderr or restart.stdout or "").strip()
        if detail:
            runner.log(f"[telegram] gateway restart did not find a ready service: {detail}")
        install_cmd = base + ["install", "--start-now", "--start-on-login", *system_args]
        if system_args:
            user, _, _, _ = _service_account()
            install_cmd.extend(["--run-as-user", user])
        runner.run(install_cmd, check=False, capture=True, timeout=180, env=_gateway_env())
    runner.run(base + ["start", "--all", *system_args], check=False, capture=True, timeout=120, env=_gateway_env())
    status = runner.run(base + ["status", "--deep", *system_args], check=False, capture=True, timeout=60, env=_gateway_env())
    if status.returncode != 0:
        detail = (status.stderr or status.stdout or "").strip()
        runner.log(f"[telegram] gateway status warning: {detail or 'status command failed'}")


def restart_systemd_services(runner: Runner, *, with_9router: bool) -> None:
    if os.name != "posix":
        return
    if runner.dry_run:
        runner.log("would restart managed systemd services after 9Router auth/model changes")
        return
    services = ["gooros-mission-control.service", "hermes-native-dashboard.service"]
    if with_9router:
        services.insert(0, "9router.service")
    prefix = root_prefix()
    for service in services:
        runner.run(prefix + ["systemctl", "restart", service], timeout=120, check=False)


def install(args) -> int:
    paths = default_paths(args.hermes_home, args.project_dir, args.config_dir, args.data_dir)
    runner = Runner(dry_run=args.dry_run, verbose=True)
    config = collect_customer_config(args, interactive=not args.yes)
    missing = validate_required(config, public_dashboards=args.with_public_dashboards, require_telegram_token=True)
    if missing and not args.dry_run:
        raise RuntimeError("missing required config: " + ", ".join(missing))
    preflight(runner, paths, public_dashboards=args.with_public_dashboards, with_9router=args.with_9router)
    if not args.dry_run:
        snap = create_snapshot(paths, "pre-install")
        runner.log(f"[safety] snapshot: {snap}")
    install_hermes_if_needed(runner, with_hermes=args.with_hermes)
    install_9router_if_requested(runner, requested=args.with_9router)
    if args.with_public_dashboards:
        install_caddy_if_missing(runner)
    caddy_hash = caddy_hash_password(runner, config.dash_password) if args.with_public_dashboards else None
    if not args.dry_run:
        write_customer_files(paths, config, caddy_hash)
    write_system_env(runner, paths, config, caddy_hash or "") if (args.systemd or args.with_public_dashboards) else None
    if args.with_9router and not args.dry_run:
        seed_router_management_env(paths)
    install_orchestrator_rules(paths, config, runner)
    retire_legacy_orchestrator_profile(runner, paths)
    install_profiles(runner, paths, config)
    install_logging(paths, runner)
    install_telegram_routing(runner, paths, config, systemd=args.systemd)
    verify_prompt13_profile_isolation_live(runner, paths)
    verify_prompt11_topic_routing_live(runner, paths, config)
    verify_prompt17_telegram_routing_audit_live(runner, paths, config)
    install_dashboard(paths, runner)
    if args.systemd:
        install_systemd_services(runner, paths, with_9router=args.with_9router)
    if args.with_9router:
        if not args.dry_run:
            wait_for_9router()
        combo_name, models, api_key = ensure_9router_hosted_combo(runner, paths)
        write_model_routing(paths, combo_name, models, runner)
        if not args.dry_run:
            if args.systemd or args.with_public_dashboards:
                write_system_env(runner, paths, config, caddy_hash or "")
            smoke_9router_model(combo_name, api_key)
        configure_hermes_for_9router(runner, paths, combo_name, api_key)
        if not args.dry_run:
            restart_gateway(runner, systemd=args.systemd)
            if args.systemd:
                restart_systemd_services(runner, with_9router=True)
    verify_prompt5_specialist_identities_live(runner, paths, config)
    verify_prompt6_agents_live(runner, paths, config)
    verify_prompt7_agents_live(runner, paths, config)
    verify_prompt9_agents_live(runner, paths, config)
    if args.with_public_dashboards:
        install_public_proxy(runner, paths, config, caddy_hash or "")
    if not args.dry_run:
        write_install_state(
            paths,
            {
                "project_dir": str(paths.project_dir),
                "hermes_home": str(paths.hermes_home),
                "public_dashboards": bool(args.with_public_dashboards),
                "with_9router": bool(args.with_9router),
                "systemd": bool(args.systemd),
                **current_source_metadata(),
            },
        )
    failures = [] if args.dry_run else verify_install(
        paths,
        public=args.with_public_dashboards,
        with_9router=args.with_9router,
        auth_user=config.dash_user,
        auth_password=config.dash_password if args.with_public_dashboards else "",
    )
    print_install_report(paths, config, caddy_hash, failures, public=args.with_public_dashboards, with_9router=args.with_9router, dry_run=args.dry_run)
    return 1 if failures else 0


def print_install_report(paths: InstallPaths, config: CustomerConfig, caddy_hash: str | None, failures: list[str], *, public: bool, with_9router: bool, dry_run: bool = False) -> None:
    print("\nGooros Hermes install report")
    print(f"Version: {VERSION}")
    print(f"Project: {paths.project_dir}")
    print(f"Hermes home: {paths.hermes_home}")
    print(f"Mission Control local: http://127.0.0.1:51763")
    print(f"Prompt 5 identity report: {prompt5_report_markdown_path(paths.project_dir)}")
    print(f"Prompt 6 boundary/continuity report: {prompt6_report_markdown_path(paths.project_dir)}")
    print(f"Prompt 7 team handoff report: {prompt7_report_markdown_path(paths.project_dir)}")
    print(f"Prompt 9 activity logging report: {prompt9_report_markdown_path(paths.project_dir)}")
    print(f"Prompt 10 log retention report: {prompt10_report_markdown_path(paths.project_dir)}")
    print(f"Prompt 11 topic routing report: {prompt11_report_markdown_path(paths.project_dir)}")
    print(f"Prompt 12 Telegram group access report: {prompt12_report_markdown_path(paths.project_dir)}")
    print(f"Prompt 13 specialist profile isolation report: {prompt13_report_markdown_path(paths.project_dir)}")
    print(f"Prompt 17 Telegram routing audit report: {prompt17_report_markdown_path(paths.project_dir)}")
    if public:
        print(f"Mission Control HTTPS: https://{sslip_name('mission', config.public_ip)}")
        print(f"Hermes native HTTPS: https://{sslip_name('hermes', config.public_ip)}")
        print(f"9Router HTTPS: https://{sslip_name('router', config.public_ip)}")
        print(f"Auth user: {config.dash_user}")
        print(f"Auth password (shown once): {config.dash_password}")
    if with_9router:
        print("Hermes -> 9Router local endpoint: http://127.0.0.1:20128/v1")
        print(f"Hermes default model: {GOOROS_9ROUTER_COMBO_NAME} (9Router free combo, DeepSeek-first, round-robin)")
        if public:
            print("9Router initial dashboard password: same as dashboard auth password on a fresh install")
        else:
            print("9Router initial dashboard password: stored in the local Gooros secrets file")
    if dry_run:
        print("\nVerification: skipped (dry-run/plan only)")
    elif failures:
        print("\nVerification failed:")
        for failure in failures:
            print(f"- {failure}")
    else:
        print("\nVerification: passed")
    print('\nTelegram acceptance step: send "xin chao" in any topic. The bot should answer there; then send /new once in #command, #scout, #scribe, #reach, #dev if old sessions keep stale routing.')
