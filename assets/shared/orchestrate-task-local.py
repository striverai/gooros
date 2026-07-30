#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


SPECIALISTS = {"scout", "scribe", "reach", "dev"}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:48] or "mission"


def parse_step(raw: str) -> tuple[str, str]:
    if "::" not in raw:
        raise ValueError("step must use agent::task format")
    agent, task = raw.split("::", 1)
    agent = agent.strip().lower()
    task = task.strip().strip('"').strip("'")
    if agent not in SPECIALISTS:
        raise ValueError(f"unsupported specialist '{agent}'")
    if not task:
        raise ValueError("step task cannot be empty")
    return agent, task


def run_step(shared: Path, project_dir: Path, agent: str, task: str, *, dry_run: bool, timeout: int) -> dict:
    if dry_run:
        return {
            "agent": agent,
            "task": task,
            "status": "completed",
            "returncode": 0,
            "started_at": utcnow(),
            "completed_at": utcnow(),
            "output": f"DRY RUN: would run {agent}: {task}",
        }
    started = utcnow()
    env = os.environ.copy()
    env["GOOROS_AGENT_WORKSPACE_ROOT"] = str(project_dir / "workspaces")
    proc = subprocess.run(
        ["bash", str(shared / "route_and_run.sh"), agent, task],
        text=True,
        capture_output=True,
        timeout=timeout,
        env=env,
    )
    output = ((proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")).strip()
    return {
        "agent": agent,
        "task": task,
        "status": "completed" if proc.returncode == 0 else "failed",
        "returncode": proc.returncode,
        "started_at": started,
        "completed_at": utcnow(),
        "output": output,
    }


def write_report(project_dir: Path, mission: str, mission_id: str, steps: list[dict]) -> Path:
    out_dir = project_dir / "content" / "orchestrator"
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}_{slugify(mission)}-{mission_id[:8]}.md"
    path = out_dir / filename
    lines = [
        f"# {mission}",
        "",
        f"- Mission ID: {mission_id}",
        f"- Created at: {utcnow()}",
        f"- Overall status: {'completed' if all(s['status'] == 'completed' for s in steps) else 'failed'}",
        "",
        "## Specialist Steps",
        "",
    ]
    for i, step in enumerate(steps, 1):
        lines.extend(
            [
                f"### {i}. {step['agent']}",
                "",
                f"- Status: {step['status']}",
                f"- Task: {step['task']}",
                "",
                "```text",
                (step.get("output") or "").strip()[:12000],
                "```",
                "",
            ]
        )
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a real Gooros multi-specialist workflow.")
    parser.add_argument("--mission", required=True)
    parser.add_argument("--step", action="append", required=True, help="agent::task; agent must be scout, scribe, reach, or dev")
    parser.add_argument("--project-dir", default=os.environ.get("PROJECT_DIR", "~/agent-mission-control"))
    parser.add_argument("--shared-dir", default=os.environ.get("HERMES_SHARED_DIR", "~/.hermes/agents/_shared"))
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    args = parser.parse_args(argv)

    shared = Path(args.shared_dir).expanduser().resolve()
    project_dir = Path(args.project_dir).expanduser().resolve()
    mission_id = uuid.uuid4().hex
    parsed = [parse_step(raw) for raw in args.step]

    results: list[dict] = []
    for agent, task in parsed:
        result = run_step(shared, project_dir, agent, task, dry_run=args.dry_run, timeout=args.timeout)
        results.append(result)
        if result["status"] != "completed" and not args.continue_on_error:
            break

    report = write_report(project_dir, args.mission, mission_id, results)
    payload = {
        "mission_id": mission_id,
        "mission": args.mission,
        "status": "completed" if all(s["status"] == "completed" for s in results) and len(results) == len(parsed) else "failed",
        "report": str(report),
        "steps": results,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
