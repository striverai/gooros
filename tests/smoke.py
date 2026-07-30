from __future__ import annotations

import compileall
from datetime import datetime, timedelta, timezone
from email.message import Message
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gooros_hermes.dashboard_patcher import build_live_dashboard
from gooros_hermes.configstore import CustomerConfig
from gooros_hermes.installer import (
    activate_prompt16_multi_agent_mode,
    choose_9router_model,
    hermes_plugin_enable_command,
    install_orchestrator_rules,
    install_dashboard,
    install_prompt15_routing_plugin,
    install_profiles,
    install_shared_scripts,
    install_telegram_routing,
    retire_legacy_orchestrator_profile,
    resolve_9router_server_js,
    restart_gateway,
    verify_prompt5_specialist_identities_live,
    verify_prompt6_agents_live,
    verify_prompt7_agents_live,
    verify_prompt9_agents_live,
    verify_prompt10_log_retention_live,
    verify_prompt11_topic_routing_live,
    verify_prompt12_telegram_group_live,
    verify_prompt13_profile_isolation_live,
    verify_prompt17_telegram_routing_audit_live,
    write_model_routing,
)
from gooros_hermes.paths import InstallPaths
from gooros_hermes.prompt5 import (
    PROMPT5_IDENTITY_QUESTION,
    PROMPT5_MEMORY_MARKER,
    load_prompt5_report,
    prompt5_report_json_path,
    render_prompt5_identity,
    validate_identity_answer,
)
from gooros_hermes.prompt6 import (
    PROMPT6_MEMORY_MARKER,
    boundary_teammate,
    load_prompt6_report,
    prompt6_agent_workspace,
    prompt6_report_json_path,
    render_prompt6_policy,
    validate_boundary_answer,
)
from gooros_hermes.prompt7 import (
    PROMPT7_MEMORY_MARKER,
    load_prompt7_report,
    prompt7_handoff_line,
    prompt7_report_json_path,
    prompt7_teammate,
    render_prompt7_policy,
    validate_handoff_answer,
)
from gooros_hermes.prompt9 import (
    PROMPT9_MEMORY_MARKER,
    PROMPT9_SMOKE_TASK,
    load_prompt9_report,
    prompt9_report_json_path,
    render_prompt9_policy,
)
from gooros_hermes.prompt10 import (
    PROMPT10_RETENTION_DAYS,
    load_prompt10_report,
    parse_cleanup_summary,
    prompt10_cron_line,
    prompt10_report_json_path,
    validate_prompt10_script,
)
from gooros_hermes.prompt11 import (
    PROMPT11_MEMORY_MARKER,
    load_prompt11_report,
    prompt11_report_json_path,
    render_prompt11_policy,
    validate_prompt11_memory,
)
from gooros_hermes.prompt12 import (
    load_prompt12_report,
    prompt12_report_json_path,
    validate_prompt12_report,
)
from gooros_hermes.prompt13 import (
    load_prompt13_report,
    prompt13_report_json_path,
    scrub_profile_env,
    validate_prompt13_report,
    validate_specialist_profile_isolation,
)
from gooros_hermes.prompt15 import (
    PROMPT15_INIT_PY,
    PROMPT15_PLUGIN_YAML,
    load_prompt15_report,
    prompt15_report_json_path,
    render_prompt15_topics,
    validate_prompt15_report,
)
from gooros_hermes.prompt16 import (
    load_prompt16_report,
    parse_multiplex_profiles_enabled,
    parse_plugin_enabled,
    prompt16_report_json_path,
    validate_prompt16_report,
)
from gooros_hermes.prompt17 import (
    load_prompt17_report,
    prompt17_report_json_path,
    validate_prompt17_report,
)
from gooros_hermes.prompt19 import discover_prompt19_sources, render_prompt19_markdown
from gooros_hermes.prompt29 import PROMPT29_MEMORY_MARKER, render_prompt29_policy
from gooros_hermes.proxy import _caddy_version_tuple, render_caddy_block
from gooros_hermes.release import read_release_manifest, validate_release_manifest
from gooros_hermes.runner import Runner
from gooros_hermes.tailscale import extract_login_url, parse_tailscale_status_json, validate_prompt33_source
from gooros_hermes.constants import AGENTS, SPECIALISTS
from gooros_hermes.router_api import (
    GOOROS_9ROUTER_COMBO_NAME,
    REQUIRED_FREE_PROVIDERS,
    _auth_cookie_from_headers,
    rank_router_models,
    select_free_router_models,
)
from gooros_hermes.verify import scan_telegram_topic_plugin_log_failures, verify_agent_logs_db
from gooros_hermes.yaml_merge import (
    merge_telegram_group_config,
    telegram_token_lines,
    validate_telegram_group_config_text,
)
from gooros_hermes import __version__
from gooros_hermes.constants import DASHBOARD_VERSION, VERSION


GOOROS_LOGO_URL = (
    "https://lh3.googleusercontent.com/pw/"
    "AP1GczPr8NBMmnzZc_CES2G0Sa-AqmGF_qN2hpNKAPB0OzeUorrSa-YMthSEJ8L5_sfrKEKDME57Wz_ou7jtSBdNuDi0xY_88AOEDS2eTimErPxaGRTpcP7oPN6eXjKnVWGQdDmte8XgAYx4ksTXOe7XPIc=w1378-h234-s-no-gm"
)


def _usable_bash() -> str:
    candidates = []
    env_bash = os.environ.get("GOOROS_TEST_BASH")
    if env_bash:
        candidates.append(env_bash)
    found = shutil.which("bash")
    if found:
        candidates.append(found)
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        candidates.append(str(Path(local_appdata) / "hermes" / "git" / "bin" / "bash.exe"))
        candidates.append(str(Path(local_appdata) / "hermes" / "git" / "usr" / "bin" / "bash.exe"))
    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if not Path(candidate).exists() and shutil.which(candidate) is None:
            continue
        proc = subprocess.run([candidate, "-lc", "echo ok"], text=True, capture_output=True)
        if proc.returncode == 0 and proc.stdout.strip() == "ok":
            return candidate
    raise AssertionError("no usable bash found for Prompt 8 log-task-local.sh smoke test")


def _write_python3_shim(bin_dir: Path) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    py = sys.executable.replace("\\", "/")
    shim = bin_dir / "python3"
    shim.write_text(f"#!/usr/bin/env bash\nexec '{py}' \"$@\"\n", encoding="ascii")


def main() -> int:
    ok = compileall.compile_dir(ROOT / "gooros_hermes", quiet=1)
    ok = compileall.compile_dir(ROOT / "migrations", quiet=1) and ok
    ok = compileall.compile_file(str(ROOT / "assets" / "dashboard" / "server.py"), quiet=1) and ok
    ok = compileall.compile_file(str(ROOT / "assets" / "shared" / "route_model.py"), quiet=1) and ok
    ok = compileall.compile_file(str(ROOT / "assets" / "shared" / "orchestrate-task-local.py"), quiet=1) and ok
    ok = compileall.compile_file(str(ROOT / "assets" / "shared" / "latest_session.py"), quiet=1) and ok
    ok = compileall.compile_file(str(ROOT / "assets" / "shared" / "append-agent-memory.py"), quiet=1) and ok
    ok = compileall.compile_file(str(ROOT / "assets" / "shared" / "handoff-task-local.py"), quiet=1) and ok
    manifest = read_release_manifest(ROOT)
    validate_release_manifest(ROOT, manifest)
    assert __version__ == VERSION
    assert manifest["version"] == VERSION
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == VERSION
    for migration_id in manifest["migrations"]:
        assert (ROOT / "migrations" / f"{migration_id}.py").exists()
    assert (ROOT / "assets" / "dashboard" / "gooros-logo.png").exists()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "index.html"
        build_live_dashboard(ROOT / "assets" / "dashboard" / "template.html", out)
        text = out.read_text(encoding="utf-8")
        assert '<html lang="vi">' in text
        assert "const I18N_DEFAULT_LANG = 'vi';" in text
        assert 'id="lang-toggle"' in text
        assert 'id="lang-toggle-m"' in text
        assert "Gooros — Tổng quan Mission Control" in text
        assert f'<img src="{GOOROS_LOGO_URL}"' in text
        assert '<img src="/gooros-logo.png"' not in text
        old_dashboard_copy = (
            "Hermes is coordinating",
            "Hermes Core",
            "Hermes Jobs",
            "HERMES JOBS",
            "Hermes Mission Control",
            "Hermes agent",
            "Hermes data",
            "Hermes home",
            "Hermes cron",
            "real Hermes",
            "Hermes HQ",
            "Hermes · HQ",
            "hermes cron create",
        )
        for old_copy in old_dashboard_copy:
            assert old_copy not in text
        assert "DEMO_" not in text
        assert "hard-coded reply" not in text
        old_demo_data = (
            "Pulled 14 sources",
            "Routing directive #412",
            "Sweeping 14 sources",
            "node 0x9f",
            "claude-sonnet-4.5",
            "gemini-2.5-pro",
            "text-embed-3-large",
            "Outline next week's video script",
        )
        for old in old_demo_data:
            assert old not in text
        assert "hydrate(); connectSSE(); startPolling();" in text
        for forbidden in (
            "/api/upload",
            'method:"POST"',
        ):
            assert forbidden not in text
        for required_board_hook in (
            f">v{DASHBOARD_VERSION}<",
            "/api/board",
            "/api/board/update",
            "/api/board/delete",
            "/api/chat/send",
            "/api/content/save",
            "/api/content/delete",
            "/api/cron/action",
            "method:'POST'",
        ):
            assert required_board_hook in text
        for required_office_3d_hook in (
            "OFFICE empire (three.js)",
            "cdn.jsdelivr.net/npm/three@0.160.0",
            "new THREE.WebGLRenderer",
            "WORKING_SERVER",
            "agentWorking(CODE_TO_PROFILE",
            "WORK_COL",
            "IDLE_COL",
            "b.windows.material.emissive.copy(col)",
            "b.accentMats.forEach(m => m.emissive.copy(col))",
            "setHero('office-lights', (d.working_agents||[]).length)",
        ):
            assert required_office_3d_hook in text
        assert "OFFICE skyline (stdlib / no npm)" not in text
    with tempfile.TemporaryDirectory() as tmp_prompt19:
        hermes_home = Path(tmp_prompt19) / ".hermes"
        hermes_home.mkdir()
        state_db = hermes_home / "state.db"
        conn = sqlite3.connect(state_db)
        try:
            conn.execute("CREATE TABLE sessions(id TEXT PRIMARY KEY, started_at REAL, updated_at REAL)")
            conn.execute("CREATE TABLE messages(id TEXT PRIMARY KEY, session_id TEXT, timestamp REAL, input_tokens INTEGER)")
            conn.execute("INSERT INTO sessions VALUES('s1', 1780000000.5, 1780000060.25)")
            conn.execute("INSERT INTO messages VALUES('m1', 's1', 1780000001.75, 12)")
            conn.commit()
        finally:
            conn.close()
        kanban_db = hermes_home / "kanban.db"
        conn = sqlite3.connect(kanban_db)
        try:
            conn.execute("CREATE TABLE cards(id TEXT PRIMARY KEY, title TEXT, created_at INTEGER, updated_at INTEGER)")
            conn.execute("INSERT INTO cards VALUES('k1', 'Audit Prompt 19', 1780000100, 1780000200)")
            conn.commit()
        finally:
            conn.close()
        (hermes_home / "gateway_state.json").write_text(
            json.dumps({"gateway_state": "running", "updated_at": "2026-07-30T00:00:00Z", "start_time": 42.5, "platforms": {"telegram": {"state": "connected"}}}),
            encoding="utf-8",
        )
        before = sorted(str(path.relative_to(hermes_home)) for path in hermes_home.rglob("*"))
        prompt19 = discover_prompt19_sources(hermes_home)
        after = sorted(str(path.relative_to(hermes_home)) for path in hermes_home.rglob("*"))
        assert before == after
        assert prompt19["read_only"] is True and prompt19["files_created"] is False
        assert prompt19["state_db"]["timestamp_samples"][0]["expected_format"] == "Unix REAL seconds"
        assert prompt19["kanban_db"]["timestamp_samples"][0]["expected_format"] == "Unix INTEGER seconds"
        assert prompt19["gateway_state"]["updated_at"] == "2026-07-30T00:00:00Z"
        rendered_prompt19 = render_prompt19_markdown(prompt19)
        assert "PRAGMA query_only=1" in rendered_prompt19
        assert "start_time" in rendered_prompt19 and "monotonic" in rendered_prompt19
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_server:
        server_root = Path(tmp_server)
        project = server_root / "project"
        hermes = server_root / ".hermes"
        project.mkdir()
        hermes.mkdir()
        build_live_dashboard(ROOT / "assets" / "dashboard" / "template.html", project / "index.html")
        shutil.copy2(ROOT / "assets" / "dashboard" / "template.html", project / "template.html")
        cron_dir = hermes / "cron"
        cron_dir.mkdir()
        (cron_dir / "jobs.json").write_text(
            json.dumps(
                {
                    "jobs": {
                        "job-morning": {
                            "name": "Morning briefing",
                            "enabled": False,
                            "state": "paused",
                            "schedule": "0 8 * * *",
                            "next_run_at": "2026-07-31T08:00:00Z",
                            "deliver": "telegram:-100123:10",
                            "model": "gooros-free-combo",
                            "prompt": "Morning briefing",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        state_db = hermes / "state.db"
        conn = sqlite3.connect(state_db)
        try:
            conn.execute("CREATE TABLE sessions(id TEXT PRIMARY KEY, started_at REAL, source TEXT, archived INTEGER)")
            conn.execute("CREATE TABLE messages(id TEXT PRIMARY KEY, session_id TEXT, timestamp REAL, role TEXT, text TEXT)")
            conn.execute("INSERT INTO sessions VALUES('chat-session', 1780000000.0, 'telegram', 0)")
            conn.execute("INSERT INTO messages VALUES('msg-1', 'chat-session', 1780000001.0, 'user', 'hello')")
            conn.commit()
        finally:
            conn.close()
        fake_bin = server_root / "fake-bin"
        fake_bin.mkdir()
        fake_log = server_root / "fake-hermes.log"
        fake_hermes = (
            "@echo off\r\n"
            "echo %*>> \"%HERMES_FAKE_LOG%\"\r\n"
            "if \"%1\"==\"chat\" echo Working directory: C:\\fake\r\n"
            "if \"%1\"==\"chat\" echo session_id: fake-session\r\n"
            "if \"%1\"==\"chat\" powershell -NoProfile -Command \"Start-Sleep -Milliseconds 900\" >nul\r\n"
            "if \"%1\"==\"chat\" echo fake streamed reply from orchestrator\r\n"
            "if \"%1\"==\"send\" echo sent\r\n"
            "if \"%1\"==\"cron\" echo cron %2 %3\r\n"
            "exit /b 0\r\n"
        )
        (fake_bin / "hermes.bat").write_text(fake_hermes, encoding="utf-8")
        (fake_bin / "hermes.cmd").write_text(fake_hermes, encoding="utf-8")
        env = os.environ.copy()
        env.update(
            {
                "PROJECT_DIR": str(project),
                "HERMES_HOME": str(hermes),
                "PORT": "51764",
                "AGENT_LOG_DB": str(project / "agent-logs.db"),
                "BOARD_DB": str(project / "board.db"),
                "CONTENT_DIR": str(project / "content"),
                "TELEGRAM_HOME_CHANNEL": "telegram:-100123:10",
                "HERMES_BIN": str(fake_bin / "hermes.bat"),
                "HERMES_FAKE_LOG": str(fake_log),
                "PATH": str(fake_bin) + os.pathsep + env.get("PATH", ""),
            }
        )
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / "assets" / "dashboard" / "server.py")],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            last_error = None
            for _ in range(40):
                try:
                    with urllib.request.urlopen("http://127.0.0.1:51764/api/state", timeout=1) as response:
                        state_payload = json.loads(response.read().decode("utf-8"))
                    break
                except Exception as exc:
                    last_error = exc
                    time.sleep(0.2)
            else:
                raise AssertionError(f"Prompt 20 server did not answer: {last_error}")
            assert {"health", "sessions", "vps", "fleet", "models", "model_usage", "routing", "agentlogs", "agentlogs_stats", "board", "working_agents", "hermes_cron"}.issubset(state_payload)
            assert len(state_payload["hermes_cron"]) == 1
            with urllib.request.urlopen("http://127.0.0.1:51764/template", timeout=2) as response:
                assert response.status == 200
            board = state_payload["board"]
            assert len(board) == 6
            assert {status: sum(1 for row in board if row["status"] == status) for status in ("pending", "in_progress", "done")} == {"pending": 2, "in_progress": 2, "done": 2}
            assert all("assignee" not in row and "agent" not in row and "model" not in row for row in board)

            def post_json(path: str, payload: dict | None = None) -> dict:
                request = urllib.request.Request(
                    "http://127.0.0.1:51764" + path,
                    data=json.dumps(payload or {}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=2) as response:
                    assert response.status == 200
                    return json.loads(response.read().decode("utf-8"))

            def post_bytes(path: str, payload: bytes) -> tuple[int, bytes]:
                request = urllib.request.Request(
                    "http://127.0.0.1:51764" + path,
                    data=payload,
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    return response.status, response.read()

            def post_text(path: str, payload: dict) -> str:
                request = urllib.request.Request(
                    "http://127.0.0.1:51764" + path,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    assert response.status == 200
                    return response.read().decode("utf-8")

            created = post_json("/api/board", {"title": "Review launch checklist", "status": "pending", "priority": "high"})
            task_id = created["id"]
            updated = post_json(f"/api/board/update?id={task_id}", {"status": "done", "notes": "checked"})
            deleted = post_json(f"/api/board/delete?id={task_id}")
            assert created["ok"] is True and updated["updated"] == 1 and deleted["deleted"] == 1
            content_status, _ = post_bytes(
                "/api/content/save?agent=scout&file=2026-07-30_prompt29-note.md",
                b"# Prompt 29 Scout Note\n\nShort research note.",
            )
            assert content_status == 200
            with urllib.request.urlopen("http://127.0.0.1:51764/api/content", timeout=2) as response:
                content_rows = json.loads(response.read().decode("utf-8"))
            assert any(row["filename"] == "2026-07-30_prompt29-note.md" and row["agent"] == "scout" for row in content_rows)
            with urllib.request.urlopen("http://127.0.0.1:51764/api/content/read?agent=scout&file=2026-07-30_prompt29-note.md", timeout=2) as response:
                assert json.loads(response.read().decode("utf-8"))["text"].startswith("# Prompt 29 Scout Note")
            assert post_bytes("/api/content/delete?agent=scout&file=2026-07-30_prompt29-note.md", b"")[0] == 200
            assert post_json("/api/cron/action?action=pause&id=job-morning")["ok"] is True
            chat_result: dict[str, str | BaseException] = {}

            def run_chat_turn() -> None:
                try:
                    chat_result["text"] = post_text("/api/chat/send", {"agent": "orchestrator", "text": "hello from dashboard"})
                except BaseException as exc:
                    chat_result["error"] = exc

            chat_thread = threading.Thread(target=run_chat_turn)
            chat_thread.start()
            time.sleep(0.25)
            with urllib.request.urlopen("http://127.0.0.1:51764/api/state", timeout=2) as response:
                active_state = json.loads(response.read().decode("utf-8"))
            assert "orchestrator" in active_state["working_agents"]
            active_fleet = {str(row["name"]).lower(): row for row in active_state["fleet"]}
            assert active_fleet["orchestrator"]["state"] == "EXECUTING"
            chat_thread.join(timeout=5)
            assert not chat_thread.is_alive()
            if "error" in chat_result:
                raise chat_result["error"]
            chat_text = str(chat_result["text"])
            assert "fake streamed reply from orchestrator" in chat_text
            assert "Working directory:" not in chat_text
            assert "session_id:" not in chat_text
            with urllib.request.urlopen("http://127.0.0.1:51764/api/state", timeout=2) as response:
                idle_state = json.loads(response.read().decode("utf-8"))
            assert "orchestrator" not in idle_state["working_agents"]
            fake_log_text = fake_log.read_text(encoding="utf-8")
            assert "chat --resume chat-session -Q -q" in fake_log_text
            assert "hello from dashboard" in fake_log_text
            assert "send --to telegram:-100123:10" in fake_log_text
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    assert choose_9router_model(["openai/gpt-4o", "kr/deepseek-3.2", "oc/qwen3-coder"]) == "kr/deepseek-3.2"
    assert choose_9router_model(["paid/model", "free/model"]) == "free/model"
    assert rank_router_models(["oc/qwen-free", "kr/deepseek-3.2", "kr/deepseek-3.2"]) == ["kr/deepseek-3.2", "oc/qwen-free"]
    assert GOOROS_9ROUTER_COMBO_NAME == "gooros-free-combo"
    assert [spec.provider_id for spec in REQUIRED_FREE_PROVIDERS] == ["opencode", "mimo-free"]
    selected = select_free_router_models(
        providers=[{"id": "mimo-conn", "provider": "mimo-free", "name": "MiMo Code Free"}],
        provider_models_by_connection_id={"mimo-conn": [{"id": "mimo-extra"}]},
        pricing={},
        suggested_models_by_provider_id={
            "opencode": [{"id": "deepseek-v4-flash-free"}, {"id": "big-pickle"}],
            "mimo-free": [{"id": "mimo-auto"}],
        },
        fallback_models=[{"id": GOOROS_9ROUTER_COMBO_NAME, "owned_by": "combo"}, {"id": "oc/qwen-free"}],
    )
    assert selected.missing_required_providers == []
    assert selected.models[0] == "oc/deepseek-v4-flash-free"
    for required_model in ("oc/big-pickle", "oc/qwen-free", "mmf/mimo-auto", "mmf/mimo-extra"):
        assert required_model in selected.models
    assert all("/" in model for model in selected.models)
    with tempfile.TemporaryDirectory() as tmp_routing:
        paths = InstallPaths(
            hermes_home=Path(tmp_routing) / ".hermes",
            project_dir=Path(tmp_routing) / "project",
            config_dir=Path(tmp_routing) / "config",
            data_dir=Path(tmp_routing) / "data",
        )
        write_model_routing(paths, GOOROS_9ROUTER_COMBO_NAME, selected.models)
        routing_path = paths.hermes_home / "agents" / "_shared" / "model-routing.json"
        routing_text = routing_path.read_text(encoding="utf-8")
        routing = json.loads(routing_text)
        assert '"required_providers"' in routing_text
        assert '"alias": "oc"' in routing_text
        assert '"alias": "mmf"' in routing_text
        assert any(row["tier"] == "premium" and row["id"] == "oc/deepseek-v4-flash-free" for row in routing["models"])
        assert any(row["tier"] == "fast" and row["id"] == GOOROS_9ROUTER_COMBO_NAME for row in routing["models"])
        route_env = os.environ.copy()
        route_env["GOOROS_MODEL_ROUTING"] = str(routing_path)
        complex_choice = subprocess.run(
            [sys.executable, str(ROOT / "assets" / "shared" / "route_model.py"), "debug architecture issue"],
            text=True,
            capture_output=True,
            env=route_env,
            check=True,
        ).stdout.strip()
        simple_choice = subprocess.run(
            [sys.executable, str(ROOT / "assets" / "shared" / "route_model.py"), "short summary"],
            text=True,
            capture_output=True,
            env=route_env,
            check=True,
        ).stdout.strip()
        assert complex_choice == "oc/deepseek-v4-flash-free"
        assert simple_choice == GOOROS_9ROUTER_COMBO_NAME
    assert _caddy_version_tuple("v2.7.6 h1:test") == (2, 7, 6)
    assert _caddy_version_tuple("2.10.0") == (2, 10, 0)
    assert extract_login_url("Authenticate at https://login.tailscale.com/a/abc123 now") == "https://login.tailscale.com/a/abc123"
    tailscale_status = parse_tailscale_status_json(
        json.dumps(
            {
                "BackendState": "Running",
                "Self": {
                    "DNSName": "gooros.tailnet.ts.net.",
                    "HostName": "gooros",
                    "TailscaleIPs": ["100.64.0.10", "fd7a:115c:a1e0::10"],
                },
            }
        )
    )
    assert tailscale_status["backend_state"] == "Running"
    assert tailscale_status["hostname"] == "gooros.tailnet.ts.net"
    assert tailscale_status["ip4"] == "100.64.0.10"
    assert validate_prompt33_source(
        (ROOT / "assets" / "dashboard" / "server.py").read_text(encoding="utf-8", errors="replace"),
        (ROOT / "gooros_hermes" / "cli.py").read_text(encoding="utf-8", errors="replace"),
        (ROOT / "gooros_hermes" / "tailscale.py").read_text(encoding="utf-8", errors="replace"),
    ) == []
    caddy_config = CustomerConfig(
        owner_name="Customer",
        owner_work="Work",
        owner_focus="Focus",
        owner_working_hours="Asia/Ho_Chi_Minh, 09:00-18:00",
        owner_important_people="Key clients and launch partners",
        owner_cares_about="Track critical relationships; delegate research, writing, growth, and engineering",
        timezone="Asia/Ho_Chi_Minh",
        telegram_chat_id="-1001234567890",
        telegram_bot_token="token",
        telegram_allowed_users="",
        thread_command="10",
        thread_scout="11",
        thread_scribe="12",
        thread_reach="13",
        thread_dev="14",
        telegram_home_channel="telegram:-1001234567890:10",
        public_ip="203.0.113.10",
        acme_email="owner@example.com",
        dash_user="gooros",
        dash_password="secret",
        model_policy="9router-free-combo-round-robin",
    )
    with tempfile.TemporaryDirectory() as tmp_prompt15:
        prompt15_paths = InstallPaths(
            hermes_home=Path(tmp_prompt15) / ".hermes",
            project_dir=Path(tmp_prompt15) / "project",
            config_dir=Path(tmp_prompt15) / "config",
            data_dir=Path(tmp_prompt15) / "data",
        )
        prompt15_plugin_dir = prompt15_paths.hermes_home / "plugins" / "telegram_topic_profiles"
        prompt15_plugin_dir.mkdir(parents=True)
        (prompt15_plugin_dir / "old-runtime-file.py").write_text("# stale\n", encoding="utf-8")

        class Prompt15Runner:
            dry_run = False

            def log(self, _message):
                return None

            def run(self, argv, **_kwargs):
                raise AssertionError(f"Prompt 15 must not run external commands: {argv}")

        install_prompt15_routing_plugin(Prompt15Runner(), prompt15_paths, caddy_config)
        assert {path.name for path in prompt15_plugin_dir.iterdir() if path.is_file()} == {"plugin.yaml", "topics.json", "__init__.py"}
        assert (prompt15_plugin_dir / "plugin.yaml").read_text(encoding="utf-8") == PROMPT15_PLUGIN_YAML
        assert (prompt15_plugin_dir / "__init__.py").read_text(encoding="utf-8") == PROMPT15_INIT_PY
        prompt15_topics = json.loads((prompt15_plugin_dir / "topics.json").read_text(encoding="utf-8"))
        assert prompt15_topics == render_prompt15_topics(caddy_config)
        assert prompt15_topics["topics"] == {"11": "scout", "12": "scribe", "13": "reach", "14": "dev"}
        assert "10" not in prompt15_topics["topics"]
        assert "orchestrator" not in set(prompt15_topics["topics"].values())
        assert "board_db" not in prompt15_topics
        log_dir = prompt15_paths.hermes_home / "logs"
        log_dir.mkdir()
        (log_dir / "gateway.log").write_text("telegram_topic_profiles Traceback: failed to load hook\n", encoding="utf-8")
        assert scan_telegram_topic_plugin_log_failures(prompt15_paths.hermes_home)
        assert "onboarding" not in prompt15_topics
        spec = importlib.util.spec_from_file_location("telegram_topic_profiles_prompt15_smoke", prompt15_plugin_dir / "__init__.py")
        assert spec and spec.loader
        prompt15_plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(prompt15_plugin)

        class Prompt15Platform:
            value = "telegram"

        class Prompt15Source:
            platform = Prompt15Platform()
            chat_id = "-1001234567890"
            thread_id = "11"
            profile = None

        class Prompt15Event:
            source = Prompt15Source()

        prompt15_event = Prompt15Event()
        prompt15_plugin._route(event=prompt15_event)
        assert prompt15_event.source.profile == "scout"
        prompt15_report = load_prompt15_report(prompt15_report_json_path(prompt15_paths.project_dir))
        assert validate_prompt15_report(prompt15_report, config=caddy_config, hermes_home=prompt15_paths.hermes_home) == []
        assert prompt15_report["plugin_enabled"] is False
        assert prompt15_report["gateway_restarted"] is False
    caddy_block = render_caddy_block(caddy_config, "HASH", auth_directive="basicauth")
    assert "basicauth {" in caddy_block
    assert "header_up Host 127.0.0.1:9119" in caddy_block
    assert "\troute {" in caddy_block
    assert "redir @router_root /dashboard 302" in caddy_block
    assert "{$" not in caddy_block
    with tempfile.TemporaryDirectory() as tmp_prompt12:
        prompt12_dir = Path(tmp_prompt12)
        standard = prompt12_dir / "standard.yaml"
        standard.write_text("platforms:\n  telegram:\n    token: 123:ABC\n", encoding="utf-8")
        standard_before_tokens = telegram_token_lines(standard.read_text(encoding="utf-8"))
        assert merge_telegram_group_config(standard, caddy_config.telegram_chat_id) is True
        standard_after = standard.read_text(encoding="utf-8")
        assert telegram_token_lines(standard_after) == standard_before_tokens
        assert validate_telegram_group_config_text(standard_after, caddy_config.telegram_chat_id) == []
        assert merge_telegram_group_config(standard, caddy_config.telegram_chat_id) is False

        require_true = prompt12_dir / "require_true.yaml"
        require_true.write_text(
            "platforms:\n  telegram:\n    token: 123:ABC\n    require_mention: true\n",
            encoding="utf-8",
        )
        merge_telegram_group_config(require_true, caddy_config.telegram_chat_id)
        assert "require_mention: false" in require_true.read_text(encoding="utf-8")
        assert validate_telegram_group_config_text(require_true.read_text(encoding="utf-8"), caddy_config.telegram_chat_id) == []

        inline_allowed = prompt12_dir / "inline_allowed.yaml"
        inline_allowed.write_text(
            'platforms:\n  telegram:\n    token: 123:ABC\n    group_allowed_chats: ["-100old"]\n',
            encoding="utf-8",
        )
        merge_telegram_group_config(inline_allowed, caddy_config.telegram_chat_id)
        inline_allowed_text = inline_allowed.read_text(encoding="utf-8")
        assert '      - "-100old"' in inline_allowed_text
        assert f'      - "{caddy_config.telegram_chat_id}"' in inline_allowed_text
        assert validate_telegram_group_config_text(inline_allowed_text, caddy_config.telegram_chat_id) == []

        inline_map = prompt12_dir / "inline_map.yaml"
        inline_map.write_text("platforms:\n  telegram: {token: 123:ABC}\n", encoding="utf-8")
        merge_telegram_group_config(inline_map, caddy_config.telegram_chat_id)
        inline_map_text = inline_map.read_text(encoding="utf-8")
        assert "  telegram:\n" in inline_map_text
        assert "    token: 123:ABC" in inline_map_text
        assert validate_telegram_group_config_text(inline_map_text, caddy_config.telegram_chat_id) == []

        bad_config = "platforms:\n  telegram:\n    require_mention: true\n    group_allowed_chats: []\n"
        assert len(validate_telegram_group_config_text(bad_config, caddy_config.telegram_chat_id)) == 2
    service_text = (ROOT / "assets" / "proxy" / "systemd" / "9router.service").read_text(encoding="utf-8")
    assert "WorkingDirectory=%GOOROS_9ROUTER_APP_DIR%" in service_text
    assert "ExecStart=%GOOROS_NODE_BIN% %GOOROS_9ROUTER_SERVER_JS%" in service_text
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_identity:
        paths = InstallPaths(
            hermes_home=Path(tmp_identity) / ".hermes",
            project_dir=Path(tmp_identity) / "project",
            config_dir=Path(tmp_identity) / "config",
            data_dir=Path(tmp_identity) / "data",
        )
        paths.project_dir.mkdir()
        (paths.project_dir / "server.py").write_text("# old server\n", encoding="utf-8")
        (paths.project_dir / "index.html").write_text("<span data-version>v1.0</span>\n", encoding="utf-8")
        install_dashboard(paths)
        assert (paths.project_dir / "server.py").exists()
        assert (paths.project_dir / "index.html").exists()
        assert (paths.project_dir / "template.html").exists()
        assert list((paths.project_dir / "backups").glob(f"server_v{DASHBOARD_VERSION}_*.py"))
        assert list((paths.project_dir / "backups").glob(f"index_v{DASHBOARD_VERSION}_*.html"))
        prompt29_notes = list((paths.project_dir / "content" / "scout").glob("*_prompt29-scout-research-note*.md"))
        assert prompt29_notes
        assert prompt29_notes[0].read_text(encoding="utf-8").startswith("# Prompt 29 Scout Research Note")
        assert not (paths.project_dir / "gooros-logo.png").exists()
        install_orchestrator_rules(paths, caddy_config)
        soul = (paths.hermes_home / "SOUL.md").read_text(encoding="utf-8")
        assert "GOOROS-HERMES-MANAGED: orchestrator SOUL v1" in soul
        assert "Ten cua ban la Orchestrator" in soul
        assert "Customer" in soul
        assert "Key clients and launch partners" in soul
        assert "Quy tac van hanh co dinh (Prompt 3)" in soul
        assert "[Agent]: Bước X/Y —" in soul
        orchestrator_workspace = prompt6_agent_workspace(paths.project_dir, "orchestrator")
        assert render_prompt6_policy("orchestrator", orchestrator_workspace) in soul
        assert render_prompt7_policy("orchestrator", orchestrator_workspace) in soul
        assert render_prompt9_policy("orchestrator", orchestrator_workspace) in soul
        assert render_prompt29_policy("orchestrator", paths.project_dir) in soul
        assert render_prompt11_policy(orchestrator_workspace) in soul
        assert (orchestrator_workspace / ".gooros-agent-workspace").exists()
        assert "GOOROS-HERMES-MANAGED: owner profile USER v1" in (paths.hermes_home / "memories" / "USER.md").read_text(encoding="utf-8")
        memory_text = (paths.hermes_home / "memories" / "MEMORY.md").read_text(encoding="utf-8")
        assert "GOOROS-HERMES-MANAGED: mission control team MEMORY v1" in memory_text
        assert "GOOROS-HERMES-MANAGED: orchestrator operating rules Prompt 3 v1" in memory_text
        assert f"GOOROS-HERMES-MANAGED: orchestrator {PROMPT6_MEMORY_MARKER}" in memory_text
        assert f"GOOROS-HERMES-MANAGED: orchestrator {PROMPT7_MEMORY_MARKER}" in memory_text
        assert f"GOOROS-HERMES-MANAGED: orchestrator {PROMPT9_MEMORY_MARKER}" in memory_text
        assert f"GOOROS-HERMES-MANAGED: orchestrator {PROMPT29_MEMORY_MARKER}" in memory_text
        assert f"GOOROS-HERMES-MANAGED: orchestrator {PROMPT11_MEMORY_MARKER}" in memory_text
        assert validate_prompt11_memory(memory_text) == []
        legacy_orchestrator = paths.hermes_home / "profiles" / "orchestrator"
        legacy_orchestrator.mkdir(parents=True)
        (legacy_orchestrator / "SOUL.md").write_text("legacy orchestrator profile", encoding="utf-8")
        retire_legacy_orchestrator_profile(Runner(verbose=False), paths)
        assert not legacy_orchestrator.exists()
        retired = list((paths.hermes_home / "profiles" / "_gooros_retired").glob("orchestrator-*"))
        assert len(retired) == 1
        assert (retired[0] / "SOUL.md").read_text(encoding="utf-8") == "legacy orchestrator profile"

        class NoopRunner:
            dry_run = False

            def log(self, _message):
                return None

            def run(self, argv, **_kwargs):
                return subprocess.CompletedProcess(argv, 0, "", "")

        for agent in SPECIALISTS:
            profile_dir = paths.hermes_home / "profiles" / agent
            profile_dir.mkdir(parents=True)
            (profile_dir / "config.yaml").write_text(
                "model:\n  default: keep-model\n"
                "tools:\n  enabled: true\n"
                "platforms:\n  telegram:\n    token: cloned-config-token\n"
                "telegram:\n  token: top-level-cloned-token\n"
                "gateway:\n  platforms:\n    telegram:\n      token: nested-cloned-token\n",
                encoding="utf-8",
            )
            (profile_dir / ".env").write_text(
                "OPENAI_API_KEY=keep-openai\n"
                "TELEGRAM_BOT_TOKEN=remove-telegram\n"
                "GOOROS_TELEGRAM_ALLOWED_USERS=remove-users\n"
                "SLACK_BOT_TOKEN=remove-slack\n",
                encoding="utf-8",
            )
        install_profiles(NoopRunner(), paths, caddy_config)
        for agent in SPECIALISTS:
            profile_dir = paths.hermes_home / "profiles" / agent
            soul_text = (profile_dir / "SOUL.md").read_text(encoding="utf-8")
            config_text = (profile_dir / "config.yaml").read_text(encoding="utf-8")
            env_text = (profile_dir / ".env").read_text(encoding="utf-8")
            workspace = prompt6_agent_workspace(paths.project_dir, agent)
            assert render_prompt5_identity(agent, "Customer") in soul_text
            assert render_prompt6_policy(agent, workspace) in soul_text
            assert render_prompt7_policy(agent, workspace) in soul_text
            assert render_prompt9_policy(agent, workspace) in soul_text
            assert render_prompt29_policy(agent, paths.project_dir) in soul_text
            assert f"profile Hermes lâu dài `{agent}`" in soul_text
            assert f"workspaces\\{agent}" in soul_text or f"workspaces/{agent}" in soul_text
            assert "Telegram, không phải Discord" in soul_text
            assert "model:\n  default: keep-model" in config_text
            assert "tools:\n  enabled: true" in config_text
            assert "platforms:" not in config_text
            assert "telegram:" not in config_text
            assert "cloned-config-token" not in config_text
            assert "nested-cloned-token" not in config_text
            assert "OPENAI_API_KEY=keep-openai" in env_text
            assert "TELEGRAM_BOT_TOKEN" not in env_text
            assert "GOOROS_TELEGRAM_ALLOWED_USERS" not in env_text
            assert "SLACK_BOT_TOKEN" not in env_text
            assert validate_specialist_profile_isolation(paths.hermes_home, agent)["verified"] is True
            profile_memory = (profile_dir / "memories" / "MEMORY.md").read_text(encoding="utf-8")
            profile_user = (profile_dir / "memories" / "USER.md").read_text(encoding="utf-8")
            assert f"GOOROS-HERMES-MANAGED: {agent} specialist profile Prompt 4 v1" in profile_memory
            assert f"GOOROS-HERMES-MANAGED: {agent} {PROMPT5_MEMORY_MARKER}" in profile_memory
            assert f"GOOROS-HERMES-MANAGED: {agent} {PROMPT6_MEMORY_MARKER}" in profile_memory
            assert f"GOOROS-HERMES-MANAGED: {agent} {PROMPT7_MEMORY_MARKER}" in profile_memory
            assert f"GOOROS-HERMES-MANAGED: {agent} {PROMPT9_MEMORY_MARKER}" in profile_memory
            assert f"GOOROS-HERMES-MANAGED: {agent} {PROMPT29_MEMORY_MARKER}" in profile_memory
            assert "GOOROS-HERMES-MANAGED: specialist owner profile USER v1" in profile_user
            assert (paths.project_dir / "workspaces" / agent / ".gooros-agent-workspace").exists()
        temp_env = paths.hermes_home / "profiles" / "scout" / "scratch.env"
        temp_env.write_text("TELEGRAM_BOT_TOKEN=x\nDISCORD_TOKEN=y\nOPENAI_API_KEY=z\n", encoding="utf-8")
        assert sorted(scrub_profile_env(temp_env)) == ["DISCORD_TOKEN", "TELEGRAM_BOT_TOKEN"]
        assert temp_env.read_text(encoding="utf-8") == "OPENAI_API_KEY=z\n"
        assert validate_identity_answer("scout", "Tôi là Scout, chuyên gia nghiên cứu của đội hình Hermes.") == []
        assert validate_identity_answer("scout", "Tôi là Scribe, chuyên gia viết lách.")
        assert validate_boundary_answer("scribe", "Đó là phần việc của Dev.") == []
        assert validate_boundary_answer("scribe", "Tôi có thể viết code Python cho bạn.\nDưới đây là code.")

        assert validate_handoff_answer("scribe", prompt7_handoff_line("scribe")) == []
        assert validate_handoff_answer("scribe", "Đó là phần việc của Dev.")

        class IdentityRunner:
            dry_run = False

            def log(self, _message):
                return None

            def run(self, argv, **_kwargs):
                agent = argv[2]
                answers = {
                    "scout": "Tôi là Scout, chuyên gia nghiên cứu và nguồn thực của đội hình Hermes.",
                    "scribe": "Tôi là Scribe, chuyên gia viết nội dung và biên tập của đội hình Hermes.",
                    "reach": "Tôi là Reach, chiến lược gia marketing, tăng trưởng và kiếm tiền của đội hình Hermes.",
                    "dev": "Tôi là Dev, kỹ sư kỹ thuật và code của đội hình Hermes.",
                }
                assert argv[:2] == ["hermes", "-p"]
                assert argv[4:] == ["-Q", "--no-restore-cwd", "-q", PROMPT5_IDENTITY_QUESTION]
                return subprocess.CompletedProcess(argv, 0, answers[agent], "")

        verify_prompt5_specialist_identities_live(IdentityRunner(), paths, caddy_config)
        prompt5_report = load_prompt5_report(prompt5_report_json_path(paths.project_dir))
        assert prompt5_report["status"] == "passed"
        assert [item["agent"] for item in prompt5_report["checks"]] == list(SPECIALISTS)
        assert all(item["verified"] and item["soul_verified"] for item in prompt5_report["checks"])

        class Prompt6Runner:
            dry_run = False

            def log(self, _message):
                return None

            def run(self, argv, **_kwargs):
                if argv[:2] == ["hermes", "-p"]:
                    agent = argv[2]
                else:
                    agent = "orchestrator"
                query = argv[-1]
                identities = {
                    "orchestrator": "Tôi là Orchestrator, điều phối viên chính của đội hình Hermes.",
                    "scout": "Tôi là Scout, chuyên gia nghiên cứu và nguồn thực của đội hình Hermes.",
                    "scribe": "Tôi là Scribe, chuyên gia viết nội dung và biên tập của đội hình Hermes.",
                    "reach": "Tôi là Reach, chiến lược gia marketing, tăng trưởng và kiếm tiền của đội hình Hermes.",
                    "dev": "Tôi là Dev, kỹ sư kỹ thuật và code của đội hình Hermes.",
                }
                if query == PROMPT5_IDENTITY_QUESTION:
                    answer = identities[agent]
                else:
                    answer = f"Đó là phần việc của {boundary_teammate(agent)}."
                return subprocess.CompletedProcess(argv, 0, answer, "")

        verify_prompt6_agents_live(Prompt6Runner(), paths, caddy_config)
        prompt6_report = load_prompt6_report(prompt6_report_json_path(paths.project_dir))
        assert prompt6_report["status"] == "passed"
        assert [item["agent"] for item in prompt6_report["identity_checks"]] == ["orchestrator", *SPECIALISTS]
        assert all(item["verified"] for item in prompt6_report["boundary_checks"])
        install_shared_scripts(paths)
        log_script = paths.hermes_home / "agents" / "_shared" / "log-task-local.sh"
        if os.name == "posix":
            assert log_script.stat().st_mode & 0o111
        log_text = log_script.read_text(encoding="utf-8")
        assert "AGENT_LOG_DB:-$HOME/agent-mission-control/agent-logs.db" in log_text
        assert "usage: log-task-local.sh <agent> <task> <completed|failed> [model]" in log_text
        assert "import sqlite3" in log_text and "import uuid" in log_text
        bash_bin = _usable_bash()
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_log:
            log_project = Path(tmp_log) / "project"
            log_db = log_project / "nested" / "agent-logs.db"
            shim_dir = Path(tmp_log) / "bin"
            _write_python3_shim(shim_dir)
            log_env = os.environ.copy()
            log_env["PATH"] = str(shim_dir) + os.pathsep + log_env.get("PATH", "")
            log_env["AGENT_LOG_DB"] = str(log_db)
            first_log = subprocess.run(
                [bash_bin, str(log_script), "dev", "built the logging system", "completed", "smoke-model"],
                text=True,
                capture_output=True,
                check=True,
                env=log_env,
            )
            assert first_log.stdout.strip() == "LOGGED: dev | completed | smoke-model"
            second_log = subprocess.run(
                [bash_bin, str(log_script), "scout", "logged without model", "failed"],
                text=True,
                capture_output=True,
                check=True,
                env=log_env,
            )
            assert second_log.stdout.strip() == "LOGGED: scout | failed |"
            assert log_db.exists()
            conn = sqlite3.connect(log_db)
            try:
                columns = conn.execute("PRAGMA table_info(agent_logs)").fetchall()
                indexes = {row[1] for row in conn.execute("PRAGMA index_list(agent_logs)").fetchall()}
                rows = conn.execute(
                    "SELECT agent_name, task_description, model_used, status FROM agent_logs ORDER BY created_at ASC"
                ).fetchall()
            finally:
                conn.close()
            assert [(c[1], c[2], c[3], c[5]) for c in columns] == [
                ("id", "TEXT", 0, 1),
                ("agent_name", "TEXT", 1, 0),
                ("task_description", "TEXT", 1, 0),
                ("model_used", "TEXT", 0, 0),
                ("status", "TEXT", 1, 0),
                ("created_at", "TEXT", 1, 0),
            ]
            assert {"idx_agent_logs_agent_name", "idx_agent_logs_status", "idx_agent_logs_created_at"}.issubset(indexes)
            assert rows == [
                ("dev", "built the logging system", "smoke-model", "completed"),
                ("scout", "logged without model", "", "failed"),
            ]
            assert verify_agent_logs_db(log_db, project_dir=log_project, hermes_home=paths.hermes_home) == []

        cleanup_script = paths.hermes_home / "agents" / "_shared" / "cleanup-logs.sh"
        cleanup_text = cleanup_script.read_text(encoding="utf-8")
        assert validate_prompt10_script(cleanup_text) == []
        assert "RETENTION_DAYS=7" in cleanup_text and "VACUUM" in cleanup_text
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_cleanup:
            cleanup_root = Path(tmp_cleanup)
            cleanup_shim = cleanup_root / "bin"
            _write_python3_shim(cleanup_shim)
            cleanup_env = os.environ.copy()
            cleanup_env["PATH"] = str(cleanup_shim) + os.pathsep + cleanup_env.get("PATH", "")
            cleanup_db = cleanup_root / "project" / "agent-logs.db"
            cleanup_db.parent.mkdir(parents=True)
            conn = sqlite3.connect(cleanup_db)
            try:
                conn.executescript(
                    """
CREATE TABLE agent_logs (
  id TEXT PRIMARY KEY,
  agent_name TEXT NOT NULL,
  task_description TEXT NOT NULL,
  model_used TEXT,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""
                )
                old_time = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat().replace("+00:00", "Z")
                new_time = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                conn.executemany(
                    "INSERT INTO agent_logs VALUES(?,?,?,?,?,?)",
                    [
                        ("old", "dev", "old row", "model", "completed", old_time),
                        ("new", "dev", "new row", "model", "completed", new_time),
                    ],
                )
                conn.commit()
            finally:
                conn.close()
            cleanup_env["AGENT_LOG_DB"] = str(cleanup_db)
            cleanup_run = subprocess.run([bash_bin, str(cleanup_script)], text=True, capture_output=True, check=True, env=cleanup_env)
            cleanup_summary = parse_cleanup_summary(cleanup_run.stdout)
            assert cleanup_summary == {"deleted": 1, "remaining": 1, "retention_days": PROMPT10_RETENTION_DAYS}
            conn = sqlite3.connect(cleanup_db)
            try:
                remaining_ids = [row[0] for row in conn.execute("SELECT id FROM agent_logs ORDER BY id").fetchall()]
                indexes = {row[1] for row in conn.execute("PRAGMA index_list(agent_logs)").fetchall()}
            finally:
                conn.close()
            assert remaining_ids == ["new"]
            assert {"idx_agent_logs_agent_name", "idx_agent_logs_status", "idx_agent_logs_created_at"}.issubset(indexes)

            missing_parent_db = cleanup_root / "missing" / "nested" / "agent-logs.db"
            cleanup_env["AGENT_LOG_DB"] = str(missing_parent_db)
            fresh_run = subprocess.run([bash_bin, str(cleanup_script)], text=True, capture_output=True, check=True, env=cleanup_env)
            fresh_summary = parse_cleanup_summary(fresh_run.stdout)
            assert fresh_summary == {"deleted": 0, "remaining": 0, "retention_days": PROMPT10_RETENTION_DAYS}
            assert missing_parent_db.exists()

        prompt10_shim_dir = paths.project_dir / "prompt10-bin"
        _write_python3_shim(prompt10_shim_dir)

        class Prompt10Runner:
            dry_run = False

            def log(self, _message):
                return None

            def run(self, argv, **kwargs):
                if argv and argv[0] == "bash":
                    env = os.environ.copy()
                    env.update(kwargs.get("env") or {})
                    env["PATH"] = str(prompt10_shim_dir) + os.pathsep + env.get("PATH", "")
                    return subprocess.run(
                        [bash_bin, *argv[1:]],
                        cwd=kwargs.get("cwd"),
                        text=True,
                        capture_output=kwargs.get("capture", False),
                        timeout=kwargs.get("timeout"),
                        env=env,
                    )
                return subprocess.CompletedProcess(argv, 0, "", "")

        verify_prompt10_log_retention_live(Prompt10Runner(), paths)
        prompt10_report = load_prompt10_report(prompt10_report_json_path(paths.project_dir))
        assert prompt10_report["status"] == "passed"
        assert prompt10_report["retention_days"] == PROMPT10_RETENTION_DAYS
        assert prompt10_report["cron_line"] == prompt10_cron_line(paths.project_dir, paths.hermes_home)
        assert parse_cleanup_summary(prompt10_report["cleanup_output"])["remaining"] >= 0

        class Prompt7Runner:
            dry_run = False

            def log(self, _message):
                return None

            def run(self, argv, **_kwargs):
                if len(argv) > 1 and str(argv[1]).endswith("handoff-task-local.py"):
                    agent = argv[2]
                    target = argv[argv.index("--to") + 1]
                    payload = {
                        "prompt": "Prompt 7",
                        "from_agent": agent,
                        "target": target,
                        "status": "completed",
                        "returncode": 0,
                    }
                    return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")
                if argv[:2] == ["hermes", "-p"]:
                    agent = argv[2]
                else:
                    agent = "orchestrator"
                return subprocess.CompletedProcess(argv, 0, prompt7_handoff_line(agent), "")

        verify_prompt7_agents_live(Prompt7Runner(), paths, caddy_config)
        prompt7_report = load_prompt7_report(prompt7_report_json_path(paths.project_dir))
        assert prompt7_report["status"] == "passed"
        assert [item["agent"] for item in prompt7_report["handoff_checks"]] == ["orchestrator", *SPECIALISTS]
        assert all(item["verified"] for item in prompt7_report["handoff_script_checks"])

        prompt9_shim_dir = paths.project_dir / "prompt9-bin"
        _write_python3_shim(prompt9_shim_dir)

        class Prompt9Runner:
            dry_run = False

            def log(self, _message):
                return None

            def run(self, argv, **kwargs):
                if len(argv) > 1 and str(argv[1]).endswith("route_model.py"):
                    return subprocess.CompletedProcess(argv, 0, "prompt9-smoke-model\n", "")
                if argv and argv[0] == "bash":
                    env = os.environ.copy()
                    env.update(kwargs.get("env") or {})
                    env["PATH"] = str(prompt9_shim_dir) + os.pathsep + env.get("PATH", "")
                    return subprocess.run(
                        [bash_bin, *argv[1:]],
                        cwd=kwargs.get("cwd"),
                        text=True,
                        capture_output=kwargs.get("capture", False),
                        timeout=kwargs.get("timeout"),
                        env=env,
                    )
                return subprocess.CompletedProcess(argv, 0, "", "")

        verify_prompt9_agents_live(Prompt9Runner(), paths, caddy_config)
        prompt9_report = load_prompt9_report(prompt9_report_json_path(paths.project_dir))
        assert prompt9_report["status"] == "passed"
        assert [item["agent"] for item in prompt9_report["memory_checks"]] == list(AGENTS)
        assert [item["agent"] for item in prompt9_report["log_smoke_checks"]] == list(AGENTS)
        assert all(item["verified"] for item in prompt9_report["memory_checks"])
        assert all(item["verified"] for item in prompt9_report["log_smoke_checks"])
        assert all(item["task_description"] == PROMPT9_SMOKE_TASK for item in prompt9_report["log_smoke_checks"])
        assert len(prompt9_report["recent_rows"]) == 5
        assert {row["agent_name"] for row in prompt9_report["recent_rows"]} == set(AGENTS)
        assert all(row["model_used"] == "prompt9-smoke-model" for row in prompt9_report["recent_rows"])
        assert parse_plugin_enabled("telegram_topic_profiles enabled")
        assert parse_plugin_enabled('{"plugins":[{"name":"telegram_topic_profiles","enabled":true}]}')
        assert parse_plugin_enabled('{"telegram_topic_profiles":{"enabled":true}}')
        assert not parse_plugin_enabled("telegram_topic_profiles disabled")
        assert parse_multiplex_profiles_enabled("true\n")
        assert parse_multiplex_profiles_enabled("multiplex_profiles: true")

        class TelegramRoutingRunner:
            dry_run = False

            def __init__(self):
                self.commands = []
                self.plugin_enabled = False

            def log(self, _message):
                return None

            def run(self, argv, **_kwargs):
                self.commands.append(list(argv))
                if argv[:3] == ["hermes", "config", "path"]:
                    return subprocess.CompletedProcess(argv, 0, str(paths.hermes_home / "config.yaml"), "")
                if argv == ["hermes", "plugins", "enable", "telegram_topic_profiles"]:
                    self.plugin_enabled = True
                    return subprocess.CompletedProcess(argv, 0, "enabled\n", "")
                if argv == ["hermes", "plugins", "list"]:
                    status = "enabled" if self.plugin_enabled else "disabled"
                    return subprocess.CompletedProcess(argv, 0, f"telegram_topic_profiles {status}\n", "")
                if argv == ["hermes", "config", "get", "multiplex_profiles"]:
                    return subprocess.CompletedProcess(argv, 0, "true\n", "")
                if argv == ["hermes", "config", "set", "multiplex_profiles", "true"]:
                    cfg = paths.hermes_home / "config.yaml"
                    current = cfg.read_text(encoding="utf-8") if cfg.exists() else ""
                    if "multiplex_profiles:" not in current:
                        cfg.write_text((current.rstrip() + "\n" if current.strip() else "") + "multiplex_profiles: true\n", encoding="utf-8")
                    return subprocess.CompletedProcess(argv, 0, "", "")
                if argv == ["hermes", "gateway", "restart"]:
                    return subprocess.CompletedProcess(argv, 0, "restarted\n", "")
                if argv == ["hermes", "gateway", "status", "--deep"]:
                    return subprocess.CompletedProcess(argv, 0, "running\n", "")
                return subprocess.CompletedProcess(argv, 0, "", "")

        telegram_runner = TelegramRoutingRunner()
        (paths.hermes_home / "config.yaml").parent.mkdir(parents=True, exist_ok=True)
        (paths.hermes_home / "config.yaml").write_text("platforms:\n  telegram:\n    token: keep-this-token-line\n", encoding="utf-8")
        install_telegram_routing(telegram_runner, paths, caddy_config)
        assert ["hermes", "config", "set", "multiplex_profiles", "true"] in telegram_runner.commands
        assert ["hermes", "config", "set", "--force", "multiplex_profiles", "true"] not in telegram_runner.commands
        assert ["hermes", "plugins", "list"] in telegram_runner.commands
        assert ["hermes", "gateway", "restart"] in telegram_runner.commands
        prompt12_config = (paths.hermes_home / "config.yaml").read_text(encoding="utf-8")
        assert "    token: keep-this-token-line" in prompt12_config
        assert validate_telegram_group_config_text(prompt12_config, caddy_config.telegram_chat_id) == []
        prompt16_report = load_prompt16_report(prompt16_report_json_path(paths.project_dir))
        assert validate_prompt16_report(prompt16_report) == []
        assert prompt16_report["plugin_enabled"] is True
        assert prompt16_report["multiplex_profiles_enabled"] is True
        assert prompt16_report["gateway_restarted"] is True
        prompt12_report = load_prompt12_report(prompt12_report_json_path(paths.project_dir))
        assert validate_prompt12_report(prompt12_report, config=caddy_config) == []
        verify_prompt12_telegram_group_live(
            telegram_runner,
            paths,
            caddy_config,
            config_path=paths.hermes_home / "config.yaml",
            config_token_line_preserved=True,
            gateway_restart_attempted=True,
        )
        verify_prompt13_profile_isolation_live(telegram_runner, paths)
        prompt13_report = load_prompt13_report(prompt13_report_json_path(paths.project_dir))
        assert validate_prompt13_report(prompt13_report) == []
        topics_data = json.loads((paths.hermes_home / "plugins" / "telegram_topic_profiles" / "topics.json").read_text(encoding="utf-8"))
        assert topics_data["topics"] == {"11": "scout", "12": "scribe", "13": "reach", "14": "dev"}
        assert "10" not in topics_data["topics"]
        assert "orchestrator" not in set(topics_data["topics"].values())
        assert (paths.hermes_home / ".env").read_text(encoding="utf-8").count("TELEGRAM_HOME_CHANNEL=telegram:-1001234567890:10") == 1
        verify_prompt11_topic_routing_live(telegram_runner, paths, caddy_config)
        prompt11_report = load_prompt11_report(prompt11_report_json_path(paths.project_dir))
        assert prompt11_report["status"] == "passed"
        assert prompt11_report["topic_routes"] == topics_data["topics"]
        assert [item["channel"] for item in prompt11_report["route_checks"]] == ["command", "scout", "scribe", "reach", "dev"]
        command_check = next(item for item in prompt11_report["route_checks"] if item["channel"] == "command")
        assert command_check["expected_agent"] == "orchestrator"
        assert command_check["actual_profile"] == "orchestrator-root"
        verify_prompt17_telegram_routing_audit_live(telegram_runner, paths, caddy_config)
        prompt17_report = load_prompt17_report(prompt17_report_json_path(paths.project_dir))
        assert validate_prompt17_report(prompt17_report) == []
        assert prompt17_report["topics"] == topics_data["topics"]
        assert prompt17_report["command_omitted_from_topics"] is True
        assert [row["channel"] for row in prompt17_report["route_table"]] == ["#command", "#scout", "#scribe", "#reach", "#dev"]
        assert prompt17_report["route_table"][0]["in_topics_json"] is False
        assert prompt17_report["symptom_guidance"]["specialist_channel_answers_as_orchestrator"]
        with tempfile.TemporaryDirectory() as tmp_prompt16_retry:
            prompt16_paths = InstallPaths(
                hermes_home=Path(tmp_prompt16_retry) / ".hermes",
                project_dir=Path(tmp_prompt16_retry) / "project",
                config_dir=Path(tmp_prompt16_retry) / "config",
                data_dir=Path(tmp_prompt16_retry) / "data",
            )
            prompt16_plugin_dir = prompt16_paths.hermes_home / "plugins" / "telegram_topic_profiles"
            prompt16_plugin_dir.mkdir(parents=True)
            (prompt16_plugin_dir / "plugin.yaml").write_text(PROMPT15_PLUGIN_YAML, encoding="utf-8")
            (prompt16_plugin_dir / "__init__.py").write_text(PROMPT15_INIT_PY, encoding="utf-8")
            for agent in SPECIALISTS:
                profile_dir = prompt16_paths.hermes_home / "profiles" / agent
                (profile_dir / "memories").mkdir(parents=True)
                (profile_dir / "SOUL.md").write_text(f"I am {agent}.\n", encoding="utf-8")
                (profile_dir / "config.yaml").write_text(
                    "model:\n  default: keep-model\nplatforms:\n  telegram:\n    token: duplicate-token\n",
                    encoding="utf-8",
                )
                (profile_dir / ".env").write_text("TELEGRAM_BOT_TOKEN=duplicate-token\nKEEP_ME=1\n", encoding="utf-8")

            class Prompt16RetryRunner:
                dry_run = False

                def __init__(self):
                    self.plugin_enabled = False
                    self.multiplex = False
                    self.restart_count = 0

                def log(self, _message):
                    return None

                def run(self, argv, **_kwargs):
                    if argv == ["hermes", "plugins", "enable", "telegram_topic_profiles"]:
                        self.plugin_enabled = True
                        return subprocess.CompletedProcess(argv, 0, "enabled\n", "")
                    if argv == ["hermes", "plugins", "list"]:
                        status = "enabled" if self.plugin_enabled else "disabled"
                        return subprocess.CompletedProcess(argv, 0, f"telegram_topic_profiles {status}\n", "")
                    if argv == ["hermes", "config", "set", "multiplex_profiles", "true"]:
                        self.multiplex = True
                        return subprocess.CompletedProcess(argv, 0, "", "")
                    if argv == ["hermes", "config", "get", "multiplex_profiles"]:
                        return subprocess.CompletedProcess(argv, 0, "true\n" if self.multiplex else "false\n", "")
                    if argv == ["hermes", "gateway", "restart"]:
                        self.restart_count += 1
                        if self.restart_count == 1:
                            return subprocess.CompletedProcess(argv, 1, "", "two profiles share telegram token")
                        return subprocess.CompletedProcess(argv, 0, "restarted\n", "")
                    if argv == ["hermes", "gateway", "status", "--deep"]:
                        if self.restart_count < 2:
                            return subprocess.CompletedProcess(argv, 1, "", "gateway not running")
                        return subprocess.CompletedProcess(argv, 0, "running\n", "")
                    return subprocess.CompletedProcess(argv, 0, "", "")

            retry_runner = Prompt16RetryRunner()
            activate_prompt16_multi_agent_mode(retry_runner, prompt16_paths)
            retry_report = load_prompt16_report(prompt16_report_json_path(prompt16_paths.project_dir))
            assert validate_prompt16_report(retry_report) == []
            assert retry_report["specialist_scrub_attempted"] is True
            assert len(retry_report["gateway_restart_attempts"]) == 2
            for agent in SPECIALISTS:
                config_text = (prompt16_paths.hermes_home / "profiles" / agent / "config.yaml").read_text(encoding="utf-8")
                env_text = (prompt16_paths.hermes_home / "profiles" / agent / ".env").read_text(encoding="utf-8")
                assert "platforms:" not in config_text
                assert "telegram:" not in config_text
                assert "TELEGRAM_BOT_TOKEN" not in env_text
                assert "KEEP_ME=1" in env_text
        install_shared_scripts(paths)
        route_script = (paths.hermes_home / "agents" / "_shared" / "route_and_run.sh").read_text(encoding="utf-8")
        assert "latest_session.py" in route_script and "--resume" in route_script
        assert "GOOROS_AGENT_WORKSPACE_ROOT" in route_script and "GOOROS_AGENT_WORKSPACE" in route_script
        assert "handoff-task-local.py" in route_script and "GOOROS_HANDOFF_ALREADY" in route_script
        assert "new_tmp_file" in route_script and "cat \"$out_file\"" in route_script
        assert "log-task-local.sh" in route_script and ">/dev/null" in route_script
        classify_proc = subprocess.run(
            [
                sys.executable,
                str(paths.hermes_home / "agents" / "_shared" / "handoff-task-local.py"),
                "scribe",
                "Hãy viết code Python để sửa dashboard",
                "--classify-only",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        assert classify_proc.stdout.strip() == "dev"
        classify_marketing_proc = subprocess.run(
            [
                sys.executable,
                str(paths.hermes_home / "agents" / "_shared" / "handoff-task-local.py"),
                "dev",
                "marketing plan 30 days",
                "--classify-only",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        assert classify_marketing_proc.stdout.strip() == "reach"
        handoff_proc = subprocess.run(
            [
                sys.executable,
                str(paths.hermes_home / "agents" / "_shared" / "handoff-task-local.py"),
                "scribe",
                "Hãy viết code Python để sửa dashboard",
                "--to",
                "dev",
                "--project-dir",
                str(paths.project_dir),
                "--shared-dir",
                str(paths.hermes_home / "agents" / "_shared"),
                "--dry-run",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        handoff_payload = json.loads(handoff_proc.stdout)
        assert handoff_payload["status"] == "completed"
        assert handoff_payload["from_agent"] == "scribe"
        assert handoff_payload["target"] == "dev"
        state_db = paths.hermes_home / "profiles" / "scout" / "state.db"
        state_db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(state_db)
        try:
            conn.execute("CREATE TABLE sessions (id TEXT, created_at TEXT, archived INTEGER)")
            conn.execute("INSERT INTO sessions VALUES ('old-session', '2026-01-01T00:00:00Z', 0)")
            conn.execute("INSERT INTO sessions VALUES ('new-session', '2026-01-02T00:00:00Z', 0)")
            conn.commit()
        finally:
            conn.close()
        env = os.environ.copy()
        env["HERMES_HOME"] = str(paths.hermes_home)
        latest_proc = subprocess.run(
            [sys.executable, str(paths.hermes_home / "agents" / "_shared" / "latest_session.py"), "scout"],
            text=True,
            capture_output=True,
            check=True,
            env=env,
        )
        assert latest_proc.stdout.strip() == "new-session"
        append_ok = subprocess.run(
            [sys.executable, str(paths.hermes_home / "agents" / "_shared" / "append-agent-memory.py"), "scribe", "content draft voice for launch newsletter"],
            text=True,
            capture_output=True,
            env=env,
        )
        assert append_ok.returncode == 0
        append_bad = subprocess.run(
            [sys.executable, str(paths.hermes_home / "agents" / "_shared" / "append-agent-memory.py"), "scribe", "code dashboard api bug fix"],
            text=True,
            capture_output=True,
            env=env,
        )
        assert append_bad.returncode == 3
        proc = subprocess.run(
            [
                sys.executable,
                str(paths.hermes_home / "agents" / "_shared" / "orchestrate-task-local.py"),
                "--mission",
                "Prompt 1 audit",
                "--step",
                "scout::Check market signals",
                "--dry-run",
                "--project-dir",
                str(paths.project_dir),
                "--shared-dir",
                str(paths.hermes_home / "agents" / "_shared"),
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        payload = json.loads(proc.stdout)
        assert payload["status"] == "completed"
        assert Path(payload["report"]).exists()
    with tempfile.TemporaryDirectory() as tmp_route:
        env = os.environ.copy()
        env["GOOROS_MODEL_ROUTING"] = str(Path(tmp_route) / "missing-model-routing.json")
        env["GOOROS_DEFAULT_MODEL"] = "fallback-model"
        proc = subprocess.run(
            [sys.executable, str(ROOT / "assets" / "shared" / "route_model.py"), "quick summary"],
            text=True,
            capture_output=True,
            check=True,
            env=env,
        )
        assert proc.stdout.strip() == "fallback-model"

    class HelpRunner:
        def __init__(self, help_text: str):
            self.help_text = help_text

        def run(self, argv, **_kwargs):
            return subprocess.CompletedProcess(argv, 0, self.help_text, "")

    assert hermes_plugin_enable_command(HelpRunner("--no-allow-tool-override"), "telegram_topic_profiles") == [
        "hermes",
        "plugins",
        "enable",
        "telegram_topic_profiles",
        "--no-allow-tool-override",
    ]
    assert hermes_plugin_enable_command(HelpRunner("usage"), "telegram_topic_profiles") == [
        "hermes",
        "plugins",
        "enable",
        "telegram_topic_profiles",
    ]
    with tempfile.TemporaryDirectory() as tmp_plugin:
        plugin_dir = Path(tmp_plugin)
        board_db = plugin_dir / "board.db"
        (plugin_dir / "__init__.py").write_text(
            (ROOT / "assets" / "plugins" / "telegram_topic_profiles" / "__init__.py").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (plugin_dir / "topics.json").write_text(
            json.dumps({"chat_id": "-100123", "board_db": str(board_db), "onboarding": {"enabled": False}, "topics": {"10": "orchestrator", "11": "scout"}}),
            encoding="utf-8",
        )
        spec = importlib.util.spec_from_file_location("telegram_topic_profiles_smoke", plugin_dir / "__init__.py")
        assert spec and spec.loader
        plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin)

        class Platform:
            value = "telegram"

        class Source:
            platform = Platform()
            chat_id = "-100123"
            thread_id = "11"
            message_id = "42"

        class Event:
            source = Source()
            text = "Research 5 competitors for the Gooros launch"

        event = Event()
        plugin._route(event=event)
        plugin._route(event=event)
        assert event.source.profile == "scout"

        class SourceCommand:
            platform = Platform()
            chat_id = "-100123"
            thread_id = "10"
            message_id = "41"
            profile = None

        class EventCommand:
            source = SourceCommand()
            text = "Coordinate the team"

        command_event = EventCommand()
        plugin._route(event=command_event)
        assert command_event.source.profile is None
        conn = sqlite3.connect(board_db)
        try:
            rows = conn.execute("SELECT title, status, priority FROM tasks WHERE title LIKE 'Scout:%'").fetchall()
            command_rows = conn.execute("SELECT title, status, priority FROM tasks WHERE title LIKE 'Orchestrator:%'").fetchall()
        finally:
            conn.close()
        assert rows == [("Scout: Research 5 competitors for the Gooros launch", "in_progress", "medium")]
        assert command_rows == [("Orchestrator: Coordinate the team", "in_progress", "medium")]

        class SourceHandoff:
            platform = Platform()
            chat_id = "-100123"
            thread_id = "11"
            message_id = "43"
            profile = None

        class EventHandoff:
            source = SourceHandoff()
            text = "Write a launch blog post from this brief"

        handoff_event = EventHandoff()
        plugin._route(event=handoff_event)
        assert handoff_event.source.profile == "scribe"
        conn = sqlite3.connect(board_db)
        try:
            handoff_row = conn.execute("SELECT title, notes FROM tasks WHERE title LIKE 'Scribe:%'").fetchone()
        finally:
            conn.close()
        assert handoff_row and handoff_row[0] == "Scribe: Write a launch blog post from this brief"
        assert json.loads(handoff_row[1])["prompt7_handoff_from"] == "scout"
    with tempfile.TemporaryDirectory() as tmp_onboarding:
        plugin_dir = Path(tmp_onboarding)
        board_db = plugin_dir / "board.db"
        state_path = plugin_dir / "telegram-onboarding-state.json"
        memory_path = plugin_dir / "USER.md"
        owner_profile_path = plugin_dir / "owner-profile.json"
        (plugin_dir / "__init__.py").write_text(
            (ROOT / "assets" / "plugins" / "telegram_topic_profiles" / "__init__.py").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (plugin_dir / "topics.json").write_text(
            json.dumps(
                {
                    "chat_id": "-100123",
                    "board_db": str(board_db),
                    "onboarding": {
                        "enabled": True,
                        "min_deep_questions": 7,
                        "max_deep_questions": 9,
                        "state_path": str(state_path),
                        "owner_profile_path": str(owner_profile_path),
                        "user_memory_path": str(memory_path),
                    },
                    "topics": {"11": "scout"},
                }
            ),
            encoding="utf-8",
        )
        spec = importlib.util.spec_from_file_location("telegram_topic_profiles_onboarding_smoke", plugin_dir / "__init__.py")
        assert spec and spec.loader
        plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin)
        old_allowed_users = os.environ.get("TELEGRAM_ALLOWED_USERS")
        os.environ["TELEGRAM_ALLOWED_USERS"] = "owner-1"

        class Platform2:
            value = "telegram"

        class Source2:
            platform = Platform2()
            chat_id = "-100123"
            thread_id = "11"
            message_id = "84"
            user_id = "owner-1"
            user_name = "Owner"
            profile = None

        class Event2:
            source = Source2()
            message_id = "84"
            text = "/start"

        class Adapter:
            def __init__(self):
                self.sent = []

            async def send(self, chat_id, content, reply_to=None, metadata=None):
                self.sent.append({"chat_id": chat_id, "content": content, "reply_to": reply_to, "metadata": metadata or {}})
                return type("SendResult", (), {"success": True})()

        event = Event2()
        adapter = Adapter()
        gateway = type("Gateway", (), {"adapters": {event.source.platform: adapter}})()
        assert plugin._route(event=event, gateway=gateway) == {"action": "skip", "reason": "gooros-onboarding-start"}
        assert "Cau 1/6" in adapter.sent[-1]["content"]
        basic_answers = [
            "Linh",
            "Founder cua Gooros",
            "Launch Hermes Mission Control",
            "Asia/Ho_Chi_Minh, 09:00-18:00",
            "Khach hang pilot va doi tac chien luoc",
            "Nam ro revenue va khach hang lon; giao lai research, content, growth, dev",
        ]
        for answer in basic_answers:
            event.text = answer
            result = plugin._route(event=event, gateway=gateway)
            assert result and result["action"] == "skip"
        assert "Launch Hermes Mission Control" in adapter.sent[-1]["content"]
        state = json.loads(state_path.read_text(encoding="utf-8"))
        record = next(iter(state["chats"].values()))
        assert record["phase"] == "deep"
        assert record["pending_deep_field"] == "audience_voice"
        deep_answers = {
            "audience_voice": "B2B founders and operators running small AI-enabled teams; voice should be direct, strategic, concrete, calm, and never hypey.",
            "offer_monetization": "Gooros Hermes Mission Control subscription with setup service; value is a Telegram-first AI operating team, dashboard visibility, and automation readiness.",
            "goals_limits": "In 90 days I want 3 pilot customers and a reliable demo. Avoid losing customer data, overpromising autonomy, or spending on paid ads before tracking works.",
            "tools_platforms": "Telegram is command center; Git repo is source of truth; dashboard shows tasks; 9Router handles model routing; website and Stripe will handle acquisition and payment.",
            "scout_context": "Scout should track AI agent ops, Telegram assistants, dashboard automation, competitors in personal AI chief-of-staff tools, and pricing signals from small business SaaS.",
            "scribe_context": "Scribe should create short launch posts, onboarding copy, docs, and sales follow-ups; style should be practical, premium, concise, and specific.",
            "reach_context": "Reach should prioritize pilot founders, warm outreach, referrals, demo calls, activation rate, and paid conversion; no spammy cold blasts.",
            "dev_context": "Dev should know Python, Hermes plugins, Telegram gateway, SQLite board DB, dashboard assets, 9Router config, installer and verify scripts; ask before destructive migrations.",
            "delegation_style": "Orchestrator can decide research, drafts, and internal task routing; ask me before public claims, paid spend, data deletion, or customer-facing commitments. Reports should be concise with blockers.",
        }
        for _ in range(20):
            state = json.loads(state_path.read_text(encoding="utf-8"))
            record = next(iter(state["chats"].values()))
            if record.get("status") == "completed":
                break
            pending = record.get("pending_deep_field")
            assert pending in deep_answers, f"unexpected pending field: {pending}"
            event.text = deep_answers[pending]
            result = plugin._route(event=event, gateway=gateway)
            assert result and result["action"] == "skip"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        record = next(iter(state["chats"].values()))
        assert record["status"] == "completed"
        assert record["answers"]["owner_name"] == "Linh"
        assert record["answers"]["scout_context"].startswith("Scout should track AI agent ops")
        memory_text = memory_path.read_text(encoding="utf-8")
        assert "GOOROS-HERMES-MANAGED: telegram onboarding owner profile v1" in memory_text
        assert "Prompt 2 adaptive coordination interview" in memory_text
        assert "Boi canh cho Dev" in memory_text
        owner_profile = json.loads(owner_profile_path.read_text(encoding="utf-8"))
        assert "Stripe" in owner_profile["answers"]["tools_platforms"]
        assert "da luu ho so owner vao tri nho dai han" in adapter.sent[-1]["content"]
        event.text = "Research competitors after onboarding"
        event.source.profile = None
        plugin._route(event=event, gateway=gateway)
        assert event.source.profile == "scout"
        if old_allowed_users is None:
            os.environ.pop("TELEGRAM_ALLOWED_USERS", None)
        else:
            os.environ["TELEGRAM_ALLOWED_USERS"] = old_allowed_users
    cookie_headers = Message()
    cookie_headers.add_header("Set-Cookie", "auth_token=abc123; Path=/; Secure; HttpOnly")
    assert _auth_cookie_from_headers(cookie_headers) == "auth_token=abc123"
    with tempfile.TemporaryDirectory() as tmp_router:
        package = Path(tmp_router) / "lib" / "node_modules" / "9router"
        (package / "app").mkdir(parents=True)
        server_js = package / "app" / "server.js"
        server_js.write_text("// server\n", encoding="utf-8")
        cli_js = package / "cli.js"
        cli_js.write_text("// cli\n", encoding="utf-8")
        assert resolve_9router_server_js(HelpRunner(""), str(cli_js)) == server_js.resolve()

    class GatewayRunner:
        dry_run = False

        def __init__(self):
            self.commands = []

        def log(self, _message):
            return None

        def run(self, argv, **_kwargs):
            self.commands.append(argv)
            if argv[:4] == ["hermes", "gateway", "--accept-hooks", "restart"]:
                return subprocess.CompletedProcess(argv, 1, "", "service not installed")
            return subprocess.CompletedProcess(argv, 0, "ok", "")

    gateway_runner = GatewayRunner()
    restart_gateway(gateway_runner)
    assert ["hermes", "gateway", "--accept-hooks", "install", "--start-now", "--start-on-login"] in gateway_runner.commands
    assert ["hermes", "gateway", "--accept-hooks", "start", "--all"] in gateway_runner.commands
    print("smoke ok")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
