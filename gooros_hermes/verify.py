from __future__ import annotations

import json
import shutil
import sqlite3
import urllib.request
from urllib.error import HTTPError
from pathlib import Path

from .constants import AGENTS, SPECIALISTS
from .paths import InstallPaths
from .configstore import read_customer_files
from .proxy import verify_public_proxy
from .runner import Runner


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


def verify_install(paths: InstallPaths, *, public: bool = False, with_9router: bool = False) -> list[str]:
    failures: list[str] = []
    if (paths.hermes_home / "profiles" / "orchestrator").exists():
        failures.append("profiles/orchestrator exists; Orchestrator must remain the default Hermes agent")
    for agent in SPECIALISTS:
        root = paths.hermes_home / "profiles" / agent
        if not root.exists():
            failures.append(f"profile missing: {agent}")
        soul = root / "SOUL.md"
        if not soul.exists():
            failures.append(f"SOUL.md missing for {agent}")
        cfg = root / "config.yaml"
        if cfg.exists() and "platforms:" in cfg.read_text(encoding="utf-8"):
            failures.append(f"profile {agent} still has platforms block")
    for name in ("server.py", "index.html", "template.html", "gooros-logo.png"):
        if not (paths.project_dir / name).exists():
            failures.append(f"dashboard file missing: {name}")
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
    ok, detail = check_http("http://127.0.0.1:51763/api/state")
    if not ok:
        failures.append(f"Mission Control /api/state not reachable: {detail}")
    try:
        with sqlite3.connect(paths.project_dir / "agent-logs.db") as conn:
            conn.execute("SELECT COUNT(*) FROM agent_logs").fetchone()
    except Exception as exc:
        failures.append(f"agent-logs.db invalid: {exc}")
    try:
        with sqlite3.connect(paths.project_dir / "board.db") as conn:
            conn.execute("SELECT COUNT(*) FROM tasks").fetchone()
    except Exception as exc:
        failures.append(f"board.db invalid: {exc}")
    if public and not shutil.which("caddy"):
        failures.append("public dashboard requested but caddy is not installed")
    if public:
        ok, detail = check_http("http://127.0.0.1:9119", ok_statuses=(200, 301, 302, 303, 307, 308, 401, 403))
        if not ok:
            failures.append(f"Hermes native dashboard upstream not reachable on 127.0.0.1:9119: {detail}")
        config = read_customer_files(paths)
        if config.public_ip and shutil.which("curl"):
            failures.extend(verify_public_proxy(Runner(verbose=False), config))
        elif config.public_ip:
            failures.append("public dashboard requested but curl is not installed")
    if with_9router:
        ok, detail = check_http("http://127.0.0.1:20128/dashboard", ok_statuses=(200, 301, 302, 303, 307, 308, 401, 403))
        if not ok:
            failures.append(f"9Router dashboard upstream not reachable on 127.0.0.1:20128/dashboard: {detail}")
        models, detail = _router_models()
        if not models:
            failures.append(f"9Router /v1/models returned no usable models: {detail}")
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
