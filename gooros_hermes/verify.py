from __future__ import annotations

import json
import shutil
import sqlite3
import urllib.request
from pathlib import Path

from .constants import AGENTS, SPECIALISTS
from .paths import InstallPaths
from .runner import Runner


def check_http(url: str, timeout: int = 5) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status == 200, str(response.status)
    except Exception as exc:
        return False, str(exc)


def verify_install(paths: InstallPaths, *, public: bool = False) -> list[str]:
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
        for token in ("DEMO_STATE", "DEMO_CHAT", "DEMO_CONTENT_DOCS", "DEMO_CONTENT_TEXT", "DEMO_HERMES_CRON", "hard-coded reply"):
            if token in text:
                failures.append(f"dashboard still contains demo token: {token}")
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
            count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            if count < 6:
                failures.append(f"board.db has {count} tasks, expected at least 6 seeded tasks")
    except Exception as exc:
        failures.append(f"board.db invalid: {exc}")
    if public and not shutil.which("caddy"):
        failures.append("public dashboard requested but caddy is not installed")
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
