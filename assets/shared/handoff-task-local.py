#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path


AGENTS = {"orchestrator", "scout", "scribe", "reach", "dev"}
SPECIALISTS = {"scout", "scribe", "reach", "dev"}
LABELS = {
    "orchestrator": "Orchestrator",
    "scout": "Scout",
    "scribe": "Scribe",
    "reach": "Reach",
    "dev": "Dev",
}
KEYWORDS = {
    "scout": (
        "research",
        "source",
        "sources",
        "market",
        "trend",
        "trends",
        "competitor",
        "competitors",
        "nghien cuu",
        "nguon",
        "xu huong",
        "thi truong",
        "doi thu",
        "kiem chung",
    ),
    "scribe": (
        "write",
        "draft",
        "edit",
        "blog",
        "article",
        "caption",
        "newsletter",
        "script",
        "copy",
        "content",
        "viet",
        "bai viet",
        "noi dung",
        "bien tap",
    ),
    "reach": (
        "marketing",
        "growth",
        "sales",
        "lead",
        "funnel",
        "campaign",
        "launch",
        "monetization",
        "revenue",
        "affiliate",
        "tang truong",
        "kiem tien",
        "doanh thu",
        "chien dich",
    ),
    "dev": (
        "code",
        "bug",
        "api",
        "dashboard",
        "automation",
        "script",
        "deploy",
        "repo",
        "database",
        "integration",
        "ky thuat",
        "tu dong hoa",
        "tich hop",
        "kiem thu",
    ),
}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(text or ""))
    ascii_text = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    ascii_text = ascii_text.replace("Đ", "D").replace("đ", "d")
    return re.sub(r"\s+", " ", ascii_text.casefold()).strip()


def keyword_hit(text: str, keyword: str) -> bool:
    if " " in keyword:
        return keyword in text
    return re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", text) is not None


def classify_target(task: str, current_agent: str = "") -> str:
    text = normalize(task)
    scores: dict[str, int] = {}
    for agent, keywords in KEYWORDS.items():
        scores[agent] = sum(2 if " " in keyword else 1 for keyword in keywords if keyword_hit(text, keyword))
    best_agent, best_score = max(scores.items(), key=lambda item: (item[1], item[0]))
    current = current_agent.lower().strip()
    if best_score <= 0:
        return current if current in AGENTS else ""
    if current in SPECIALISTS and scores.get(current, 0) >= best_score:
        return current
    return best_agent


def append_report(project_dir: Path, payload: dict) -> None:
    report = project_dir / "reports" / "prompt7-handoffs.jsonl"
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prompt 7 handoff router for Gooros agents.")
    parser.add_argument("from_agent", choices=sorted(AGENTS))
    parser.add_argument("task")
    parser.add_argument("--to", choices=sorted(SPECIALISTS), default="")
    parser.add_argument("--project-dir", default=os.environ.get("PROJECT_DIR") or os.environ.get("GOOROS_PROJECT_DIR") or "~/agent-mission-control")
    parser.add_argument("--shared-dir", default=os.environ.get("HERMES_SHARED_DIR") or "~/.hermes/agents/_shared")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--classify-only", action="store_true")
    args = parser.parse_args(argv)

    from_agent = args.from_agent.lower()
    target = (args.to or classify_target(args.task, from_agent)).lower()
    if args.classify_only:
        print(target)
        return 0

    project_dir = Path(args.project_dir).expanduser()
    shared_dir = Path(args.shared_dir).expanduser()
    payload = {
        "prompt": "Prompt 7",
        "created_at": utcnow(),
        "from_agent": from_agent,
        "target": target,
        "task": args.task,
        "notice": f"Đây là mảng của {LABELS.get(target, target.title())}, đang chuyển việc này cho họ." if target and target != from_agent else "",
        "status": "kept" if not target or target == from_agent else "running",
        "returncode": 0,
        "output": "",
    }

    if not target or target == from_agent:
        append_report(project_dir, payload)
        print(json.dumps(payload, ensure_ascii=True, sort_keys=True))
        return 0

    if args.dry_run:
        payload["status"] = "completed"
        payload["output"] = f"DRY RUN: would hand off from {from_agent} to {target}"
        append_report(project_dir, payload)
        print(json.dumps(payload, ensure_ascii=True, sort_keys=True))
        return 0

    env = os.environ.copy()
    env["GOOROS_HANDOFF_ALREADY"] = "1"
    env["GOOROS_PROJECT_DIR"] = str(project_dir)
    env.setdefault("GOOROS_AGENT_WORKSPACE_ROOT", str(project_dir / "workspaces"))
    env.setdefault("HERMES_SHARED_DIR", str(shared_dir))
    proc = subprocess.run(
        ["bash", str(shared_dir / "route_and_run.sh"), target, args.task],
        text=True,
        capture_output=True,
        timeout=args.timeout,
        env=env,
    )
    output = ((proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")).strip()
    payload["returncode"] = proc.returncode
    payload["status"] = "completed" if proc.returncode == 0 else "failed"
    payload["output"] = output[:12000]
    append_report(project_dir, payload)
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True))
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
