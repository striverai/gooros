#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import mimetypes
import os
import queue
import re
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from contextlib import closing
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PROJECT_DIR = Path(os.environ.get("PROJECT_DIR", Path(__file__).resolve().parent)).expanduser().resolve()
HERMES_HOME = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser().resolve()
HOST = "127.0.0.1"
PORT = int(os.environ.get("PORT", 51763))
AGENT_LOG_DB = Path(os.environ.get("AGENT_LOG_DB", PROJECT_DIR / "agent-logs.db")).expanduser().resolve()
BOARD_DB = Path(os.environ.get("BOARD_DB", PROJECT_DIR / "board.db")).expanduser().resolve()
CONTENT_DIR = Path(os.environ.get("CONTENT_DIR", PROJECT_DIR / "content")).expanduser().resolve()
CACHE_SECONDS = 3
CHAT_TIMEOUT_SECONDS = int(os.environ.get("CHAT_TIMEOUT_SECONDS", 180))
CRON_TIMEOUT_SECONDS = int(os.environ.get("CRON_TIMEOUT_SECONDS", 60))
TELEGRAM_HOME_CHANNEL = os.environ.get("TELEGRAM_HOME_CHANNEL", "")
HERMES_BIN = os.environ.get("HERMES_BIN", "hermes")

AGENTS = ("orchestrator", "scout", "scribe", "reach", "dev")
SPECIALISTS = ("scout", "scribe", "reach", "dev")
CRON_ACTIONS = {"run", "pause", "resume", "delete"}
META = {
    "orchestrator": {"code": "A-00", "initials": "OR", "name": "Orchestrator", "role": "Coordinator", "channel": "telegram"},
    "scout": {"code": "A-01", "initials": "SC", "name": "Scout", "role": "Research", "channel": "#scout"},
    "scribe": {"code": "A-02", "initials": "SB", "name": "Scribe", "role": "Writing", "channel": "#scribe"},
    "reach": {"code": "A-03", "initials": "RE", "name": "Reach", "role": "Marketing", "channel": "#reach"},
    "dev": {"code": "A-04", "initials": "DV", "name": "Dev", "role": "Engineering", "channel": "#dev"},
}
STATUS_MAP = {"pending": "todo", "in_progress": "doing", "done": "done"}
PRIORITY_MAP = {"high": "P1", "medium": "P2", "low": "P3"}
STATE_CACHE: tuple[float, dict] = (0.0, {})
ACTIVE_AGENTS: set[str] = set()
ACTIVE_AGENTS_LOCK = threading.Lock()
BOARD_STATUSES = {"pending", "in_progress", "done"}
BOARD_PRIORITIES = {"high", "medium", "low"}
SEED_TASKS = (
    ("seed-reply-sponsor-email", "Reply to sponsor email", "pending", "high", ""),
    ("seed-plan-week", "Plan this week's priorities", "pending", "medium", ""),
    ("seed-edit-video", "Edit this week's video", "in_progress", "high", ""),
    ("seed-draft-newsletter", "Draft the launch newsletter", "in_progress", "medium", ""),
    ("seed-publish-blog", "Publish the new blog post", "done", "medium", ""),
    ("seed-schedule-posts", "Schedule this week's social posts", "done", "low", ""),
)
LIVE_DASHBOARD_TOKENS = (
    "DEMO_STATE",
    "DEMO_CHAT",
    "DEMO_CONTENT_DOCS",
    "DEMO_CONTENT_TEXT",
    "DEMO_GOOROS_CRON",
    "Pulled 14 sources",
    "Routing directive #412",
    "Sweeping 14 sources",
    "node 0x9f",
    "Outline next week's video script",
    "claude-sonnet-4.5",
    "gemini-2.5-pro",
    "text-embed-3-large",
    "hard-coded reply",
)


def connect_ro(path: Path) -> sqlite3.Connection:
    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=1")
    return conn


def connect_board_rw() -> sqlite3.Connection:
    BOARD_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(BOARD_DB)
    conn.row_factory = sqlite3.Row
    return conn


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_board_status(value: object) -> str:
    status = str(value or "pending")
    return status if status in BOARD_STATUSES else "pending"


def normalize_board_priority(value: object) -> str:
    priority = str(value or "medium")
    return priority if priority in BOARD_PRIORITIES else "medium"


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def invalidate_state_cache() -> None:
    global STATE_CACHE
    STATE_CACHE = (0.0, {})


def working_agents_snapshot() -> list[str]:
    with ACTIVE_AGENTS_LOCK:
        return sorted(ACTIVE_AGENTS)


def set_agent_working(agent: str, working: bool) -> None:
    changed = False
    with ACTIVE_AGENTS_LOCK:
        if working and agent not in ACTIVE_AGENTS:
            ACTIVE_AGENTS.add(agent)
            changed = True
        elif not working and agent in ACTIVE_AGENTS:
            ACTIVE_AGENTS.remove(agent)
            changed = True
    if changed:
        invalidate_state_cache()


def validate_live_dashboard() -> None:
    index = PROJECT_DIR / "index.html"
    if not index.exists():
        raise RuntimeError(f"dashboard index missing: {index}")
    text = index.read_text(encoding="utf-8", errors="replace")
    leftovers = [token for token in LIVE_DASHBOARD_TOKENS if token in text]
    if leftovers:
        raise RuntimeError(f"dashboard index still contains demo content: {', '.join(leftovers)}")
    if "hydrate(); connectSSE(); startPolling();" not in text:
        raise RuntimeError("dashboard index is missing live hydrate/SSE bootstrap")


def safe_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    except Exception:
        return set()


def read_health() -> dict:
    data = safe_json(HERMES_HOME / "gateway_state.json", {})
    return {
        "gateway_state": data.get("gateway_state") or data.get("state") or "unknown",
        "platforms": data.get("platforms", {}),
        "updated_at": data.get("updated_at"),
    }


def read_sessions() -> dict:
    db = HERMES_HOME / "state.db"
    totals = {"input": 0, "output": 0, "messages": 0}
    recent = []
    if not db.exists():
        return {"totals": totals, "recent": recent}
    try:
        with closing(connect_ro(db)) as conn:
            tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "messages" in tables:
                cols = table_columns(conn, "messages")
                totals["messages"] = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
                for candidate, key in (("input_tokens", "input"), ("prompt_tokens", "input"), ("output_tokens", "output"), ("completion_tokens", "output")):
                    if candidate in cols:
                        totals[key] += conn.execute(f"SELECT COALESCE(SUM({candidate}),0) FROM messages").fetchone()[0] or 0
            if "sessions" in tables:
                cols = table_columns(conn, "sessions")
                id_col = "id" if "id" in cols else "session_id"
                ts_col = "started_at" if "started_at" in cols else ("created_at" if "created_at" in cols else id_col)
                source_col = "source" if "source" in cols else None
                sql = f"SELECT {id_col} AS id, {ts_col} AS started_at" + (f", {source_col} AS source" if source_col else ", '' AS source") + f" FROM sessions ORDER BY {ts_col} DESC LIMIT 8"
                recent = [dict(r) for r in conn.execute(sql)]
    except Exception as exc:
        return {"totals": totals, "recent": recent, "error": str(exc)}
    return {"totals": totals, "recent": recent}


def read_vps() -> dict:
    try:
        with open("/proc/stat", "r", encoding="utf-8") as handle:
            first = [int(x) for x in handle.readline().split()[1:8]]
        time.sleep(0.05)
        with open("/proc/stat", "r", encoding="utf-8") as handle:
            second = [int(x) for x in handle.readline().split()[1:8]]
        idle_delta = (second[3] + second[4]) - (first[3] + first[4])
        total_delta = sum(second) - sum(first)
        cpu = 0.0 if total_delta <= 0 else round(100 * (1 - idle_delta / total_delta), 1)
    except Exception:
        cpu = 0.0
    try:
        mem = {}
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                key, value = line.split(":", 1)
                mem[key] = int(value.strip().split()[0])
        total = mem.get("MemTotal", 1)
        available = mem.get("MemAvailable", total)
        mem_pct = round(100 * (1 - available / total), 1)
    except Exception:
        mem_pct = 0.0
    try:
        st = os.statvfs(PROJECT_DIR)
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        disk_pct = round(100 * (1 - free / total), 1) if total else 0.0
    except Exception:
        disk_pct = 0.0
    return {"cpu_pct": cpu, "mem_pct": mem_pct, "disk_pct": disk_pct}


def log_rows(limit: int = 200) -> list[sqlite3.Row]:
    if not AGENT_LOG_DB.exists():
        return []
    try:
        with closing(connect_ro(AGENT_LOG_DB)) as conn:
            tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "agent_logs" not in tables:
                return []
            return list(conn.execute("SELECT * FROM agent_logs ORDER BY created_at DESC LIMIT ?", (limit,)))
    except Exception:
        return []


def init_board_db() -> None:
    now = utcnow()
    with closing(connect_board_rw()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
              id TEXT PRIMARY KEY,
              title TEXT NOT NULL,
              status TEXT NOT NULL,
              priority TEXT NOT NULL,
              notes TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        if count == 0:
            conn.executemany(
                """
                INSERT INTO tasks(id, title, status, priority, notes, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                [(task_id, title, status, priority, notes, now, now) for task_id, title, status, priority, notes in SEED_TASKS],
            )
        conn.commit()


def read_fleet(rows: list[sqlite3.Row]) -> list[dict]:
    total = len(rows)
    by_agent = {agent: [] for agent in AGENTS}
    active_agents = set(working_agents_snapshot())
    for row in rows:
        agent = str(row["agent_name"]).lower()
        if agent in by_agent:
            by_agent[agent].append(row)
    fleet = []
    for agent in AGENTS:
        agent_rows = by_agent[agent]
        count = len(agent_rows)
        completed = sum(1 for r in agent_rows if str(r["status"]).lower() == "completed")
        meta = META[agent]
        latest = agent_rows[0] if agent_rows else None
        state = "EXECUTING" if agent in active_agents else "IDLE"
        fleet.append(
            {
                **meta,
                "tasksToday": count,
                "success": round(100 * completed / count, 1) if count else 100.0,
                "defaultModel": latest["model_used"] if latest and latest["model_used"] else "",
                "share": round(100 * count / total) if total else 0,
                "load": max(35, min(100, count * 25)) if state == "EXECUTING" else min(100, count * 25),
                "tokens": f"{count} tasks",
                "latency": "\u2014",
                "state": state,
                "task": latest["task_description"] if latest else ("Agent turn running now." if state == "EXECUTING" else "No tasks logged yet."),
            }
        )
    return fleet


def read_models(rows: list[sqlite3.Row]) -> tuple[list[dict], list[dict], dict]:
    counts: dict[str, int] = {}
    for row in rows:
        model = row["model_used"] or "unknown"
        counts[model] = counts.get(model, 0) + 1
    total = sum(counts.values())
    routing_cfg = safe_json(HERMES_HOME / "agents" / "_shared" / "model-routing.json", {})
    tiers = {m.get("id"): m.get("tier", "") for m in routing_cfg.get("models", []) if isinstance(m, dict)}
    model_ids = sorted(counts)
    models = [{"id": m, "label": m, "vendor": "", "tier": tiers.get(m, "")} for m in model_ids]
    usage = [{"name": m, "count": c, "pct": round(100 * c / total) if total else 0} for m, c in sorted(counts.items(), key=lambda x: -x[1])]
    fast = sum(c for m, c in counts.items() if tiers.get(m) == "fast")
    premium = total - fast
    routing = {"total": total, "models": len(counts), "premium_calls": premium, "fast_calls": fast, "offload_pct": round(100 * fast / total) if total else 0}
    return models, usage, routing


def read_agentlogs(rows: list[sqlite3.Row]) -> tuple[list[dict], dict]:
    code = {k: v["initials"] for k, v in META.items()}
    out = []
    for row in rows[:20]:
        agent = str(row["agent_name"]).lower()
        time_text = row["created_at"]
        try:
            time_text = datetime.fromisoformat(time_text.replace("Z", "+00:00")).strftime("%H:%M:%S")
        except Exception:
            pass
        out.append({"agent": code.get(agent, agent[:2].upper()), "task": row["task_description"], "time": time_text, "model": row["model_used"] or "", "status": row["status"]})
    stats = {"total": len(rows), "completed": sum(1 for r in rows if r["status"] == "completed"), "failed": sum(1 for r in rows if r["status"] == "failed")}
    return out, stats


def read_board() -> list[dict]:
    init_board_db()
    try:
        with closing(connect_ro(BOARD_DB)) as conn:
            tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "tasks" not in tables:
                return []
            rows = conn.execute("SELECT * FROM tasks ORDER BY created_at ASC").fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def read_kanban() -> dict:
    db = HERMES_HOME / "kanban.db"
    if not db.exists():
        return {"exists": False, "tables": []}
    try:
        with closing(connect_ro(db)) as conn:
            tables = [r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            out = []
            for table in tables:
                qtable = quote_identifier(table)
                columns = [r["name"] for r in conn.execute(f"PRAGMA table_info({qtable})")]
                count = conn.execute(f"SELECT COUNT(*) FROM {qtable}").fetchone()[0]
                out.append({"name": table, "columns": columns, "rows": count})
            return {"exists": True, "tables": out}
    except Exception as exc:
        return {"exists": True, "tables": [], "error": str(exc)}


def read_cron() -> list[dict]:
    data = safe_json(HERMES_HOME / "cron" / "jobs.json", [])
    jobs = data.get("jobs", data.get("items", [])) if isinstance(data, dict) else data
    if isinstance(jobs, dict):
        jobs = [
            {**value, "id": value.get("id") or key} if isinstance(value, dict) else value
            for key, value in jobs.items()
        ]
    if not isinstance(jobs, list):
        return []
    out = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        job_id = str(job.get("id", "")).strip()
        if not job_id:
            continue
        enabled = bool(job.get("enabled", job.get("state") not in ("paused", "disabled")))
        state = str(job.get("state") or ("active" if enabled else "paused"))
        out.append(
            {
                "id": job_id,
                "name": job.get("name") or job.get("title") or job_id,
                "enabled": enabled,
                "state": state,
                "schedule": job.get("schedule") or job.get("cron") or "",
                "next_run_at": job.get("next_run_at") or job.get("nextRunAt"),
                "last_status": job.get("last_status") or job.get("lastStatus"),
                "last_error": job.get("last_error") or job.get("lastError"),
                "deliver": job.get("deliver") or job.get("delivery") or job.get("to"),
                "model": job.get("model") or job.get("model_id") or job.get("modelId"),
                "prompt": job.get("prompt") or job.get("query") or job.get("description") or "",
            }
        )
    return out


def cron_job_ids() -> set[str]:
    return {str(job["id"]) for job in read_cron() if job.get("id")}


def state_snapshot() -> dict:
    global STATE_CACHE
    now = time.time()
    active_agents = working_agents_snapshot()
    if not active_agents and now - STATE_CACHE[0] < CACHE_SECONDS:
        return STATE_CACHE[1]
    rows = log_rows()
    models, usage, routing = read_models(rows)
    agentlogs, agentlog_stats = read_agentlogs(rows)
    state = {
        "health": read_health(),
        "sessions": read_sessions(),
        "vps": read_vps(),
        "fleet": read_fleet(rows),
        "models": models,
        "model_usage": usage,
        "routing": routing,
        "agentlogs": agentlogs,
        "agentlogs_stats": agentlog_stats,
        "board": read_board(),
        "kanban": read_kanban(),
        "working_agents": active_agents,
        "hermes_cron": read_cron(),
    }
    STATE_CACHE = (now, state)
    return state


def json_response(handler: BaseHTTPRequestHandler, data, status: int = 200) -> None:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def text_response(handler: BaseHTTPRequestHandler, text: str, status: int = 200, content_type: str = "text/plain; charset=utf-8") -> None:
    body = text.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def safe_agent(value: str) -> str:
    if value not in AGENTS:
        raise ValueError("bad agent")
    return value


def safe_filename(value: str) -> str:
    if "/" in value or "\\" in value or ".." in value or not value.endswith(".md"):
        raise ValueError("bad filename")
    if not re.match(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}_[a-z0-9][a-z0-9-]*\.md$", value):
        raise ValueError("filename must be YYYY-MM-DD_kebab-title.md")
    return value


def content_path(agent: str, filename: str) -> Path:
    if is_relative_to(CONTENT_DIR, HERMES_HOME):
        raise ValueError("CONTENT_DIR must not be inside HERMES_HOME")
    agent = safe_agent(agent)
    filename = safe_filename(filename)
    path = (CONTENT_DIR / agent / filename).resolve()
    root = (CONTENT_DIR / agent).resolve()
    if not is_relative_to(path, root):
        raise ValueError("bad path")
    return path


def latest_session(agent: str) -> tuple[Path, str | None, bool]:
    db = HERMES_HOME / "state.db" if agent == "orchestrator" else HERMES_HOME / "profiles" / agent / "state.db"
    if not db.exists():
        return db, None, False
    try:
        with closing(connect_ro(db)) as conn:
            cols = table_columns(conn, "sessions")
            if not cols:
                return db, None, agent == "orchestrator"
            id_col = "id" if "id" in cols else "session_id"
            ts_col = "started_at" if "started_at" in cols else ("created_at" if "created_at" in cols else id_col)
            where = ""
            if agent == "orchestrator" and "source" in cols:
                where = "WHERE source='telegram'"
            if "archived" in cols:
                where = (where + " AND " if where else "WHERE ") + "(archived IS NULL OR archived=0)"
            row = conn.execute(f"SELECT {id_col} AS id FROM sessions {where} ORDER BY {ts_col} DESC LIMIT 1").fetchone()
            return db, (row["id"] if row else None), agent == "orchestrator"
    except Exception:
        return db, None, agent == "orchestrator"


def session_messages(agent: str) -> dict:
    db, session_id, telegram = latest_session(agent)
    if not session_id or not db.exists():
        return {"telegram": telegram, "messages": []}
    messages = []
    try:
        with closing(connect_ro(db)) as conn:
            cols = table_columns(conn, "messages")
            if not cols:
                return {"telegram": telegram, "messages": []}
            sid_col = "session_id" if "session_id" in cols else ("session" if "session" in cols else None)
            role_col = "role" if "role" in cols else ("speaker" if "speaker" in cols else None)
            text_col = "text" if "text" in cols else ("content" if "content" in cols else "message")
            ts_col = "timestamp" if "timestamp" in cols else ("created_at" if "created_at" in cols else "rowid")
            where = f"WHERE {sid_col}=?" if sid_col else ""
            role_expr = role_col or "'agent'"
            sql = f"SELECT {role_expr} AS role, {text_col} AS text, {ts_col} AS ts FROM messages {where} ORDER BY {ts_col} ASC"
            params = (session_id,) if sid_col else ()
            for row in conn.execute(sql, params):
                messages.append({"role": row["role"] or "agent", "text": row["text"] or "", "ts": row["ts"]})
    except Exception as exc:
        return {"telegram": telegram, "messages": [], "error": str(exc)}
    return {"telegram": telegram, "messages": messages}


def chat_command(agent: str, session_id: str, text: str) -> list[str]:
    argv = [HERMES_BIN]
    if agent != "orchestrator":
        argv.extend(["-p", agent])
    argv.extend(["chat", "--resume", session_id, "-Q", "-q", text])
    return argv


def hermes_env() -> dict[str, str]:
    env = os.environ.copy()
    env["HERMES_HOME"] = str(HERMES_HOME)
    env["TIRITH_ENABLED"] = "false"
    return env


def clean_stream_line(line: str) -> str:
    stripped = line.strip()
    if not stripped:
        return line
    if stripped.startswith(("↻ Resumed", "Resumed", "Working directory:", "↻ Working directory:", "session_id:")):
        return ""
    return line


def telegram_target() -> str:
    value = TELEGRAM_HOME_CHANNEL.strip()
    if not value:
        return ""
    return value if value.startswith("telegram:") else f"telegram:{value}"


def mirror_orchestrator_to_telegram(user_text: str, reply_text: str) -> None:
    target = telegram_target()
    if not target:
        return
    for prefix, text in (("Owner", user_text), ("Orchestrator", reply_text)):
        body = f"{prefix}: {text}".strip()
        if not body:
            continue
        subprocess.run(
            [HERMES_BIN, "send", "--to", target, body],
            env=hermes_env(),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=30,
            check=False,
        )


class Handler(BaseHTTPRequestHandler):
    server_version = "GoorosHermes/0.1"

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - - [%s] %s\n" % (self.client_address[0], self.log_date_time_string(), fmt % args))

    def do_GET(self) -> None:
        try:
            self.route_get()
        except Exception as exc:
            json_response(self, {"error": str(exc)}, 500)

    def do_POST(self) -> None:
        try:
            self.route_post()
        except Exception as exc:
            json_response(self, {"error": str(exc)}, 500)

    def route_get(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        if path == "/":
            return self.serve_file(PROJECT_DIR / "index.html")
        if path == "/template":
            return self.serve_file(PROJECT_DIR / "template.html")
        if path == "/api/state":
            return json_response(self, state_snapshot())
        if path == "/events":
            return self.events()
        if path == "/api/board":
            return json_response(self, read_board())
        if path == "/api/agent":
            return json_response(self, agent_detail(safe_agent(qs.get("key", [""])[0])))
        if path == "/api/chat/history":
            return json_response(self, session_messages(safe_agent(qs.get("agent", [""])[0])))
        if path == "/api/content":
            return json_response(self, content_list())
        if path == "/api/content/read":
            p = content_path(qs.get("agent", [""])[0], qs.get("file", [""])[0])
            return json_response(self, {"text": p.read_text(encoding="utf-8") if p.exists() else ""})
        self.send_error(HTTPStatus.NOT_FOUND)

    def route_post(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        if path == "/api/board":
            payload = self.read_json_body()
            now = utcnow()
            task_id = f"user-{uuid.uuid4().hex[:12]}"
            title = str(payload.get("title", "")).strip()[:180]
            if not title:
                return json_response(self, {"error": "title is required"}, HTTPStatus.BAD_REQUEST)
            row = (
                task_id,
                title,
                normalize_board_status(payload.get("status", "pending")),
                normalize_board_priority(payload.get("priority", "medium")),
                str(payload.get("notes", "")),
                now,
                now,
            )
            init_board_db()
            with closing(connect_board_rw()) as conn:
                conn.execute("INSERT INTO tasks(id, title, status, priority, notes, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?)", row)
                conn.commit()
            self.invalidate_state_cache()
            return json_response(self, {"ok": True, "id": task_id})
        if path == "/api/board/update":
            task_id = qs.get("id", [""])[0].strip()
            if not task_id:
                return json_response(self, {"error": "id is required"}, HTTPStatus.BAD_REQUEST)
            payload = self.read_json_body()
            fields: list[str] = []
            values: list[str] = []
            if "title" in payload:
                title = str(payload["title"]).strip()[:180]
                if not title:
                    return json_response(self, {"error": "title is required"}, HTTPStatus.BAD_REQUEST)
                fields.append("title=?")
                values.append(title)
            if "status" in payload:
                fields.append("status=?")
                values.append(normalize_board_status(payload["status"]))
            if "priority" in payload:
                fields.append("priority=?")
                values.append(normalize_board_priority(payload["priority"]))
            if "notes" in payload:
                fields.append("notes=?")
                values.append(str(payload["notes"]))
            fields.append("updated_at=?")
            values.append(utcnow())
            values.append(task_id)
            init_board_db()
            with closing(connect_board_rw()) as conn:
                cur = conn.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE id=?", values)
                conn.commit()
            self.invalidate_state_cache()
            return json_response(self, {"ok": True, "updated": cur.rowcount})
        if path == "/api/board/delete":
            task_id = qs.get("id", [""])[0].strip()
            if not task_id:
                return json_response(self, {"error": "id is required"}, HTTPStatus.BAD_REQUEST)
            init_board_db()
            with closing(connect_board_rw()) as conn:
                cur = conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
                conn.commit()
            self.invalidate_state_cache()
            return json_response(self, {"ok": True, "deleted": cur.rowcount})
        if path == "/api/content/save":
            p = content_path(qs.get("agent", [""])[0], qs.get("file", [""])[0])
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(self.read_body_bytes())
            return json_response(self, {"ok": True, "path": str(p), "bytes": p.stat().st_size})
        if path == "/api/content/delete":
            p = content_path(qs.get("agent", [""])[0], qs.get("file", [""])[0])
            if not p.exists():
                return json_response(self, {"error": "not found"}, HTTPStatus.NOT_FOUND)
            p.unlink()
            return json_response(self, {"ok": True})
        if path == "/api/chat/send":
            return self.chat_send()
        if path == "/api/cron/action":
            return self.cron_action(qs.get("action", [""])[0], qs.get("id", [""])[0])
        json_response(self, {"error": "unsupported write endpoint"}, HTTPStatus.METHOD_NOT_ALLOWED)

    def read_json_body(self) -> dict:
        raw = self.read_body_bytes()
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def read_body_bytes(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length", "0")))

    def invalidate_state_cache(self) -> None:
        invalidate_state_cache()

    def chat_send(self) -> None:
        payload = self.read_json_body()
        agent = safe_agent(str(payload.get("agent", "")).strip())
        text = str(payload.get("text", "")).strip()
        if not text:
            return json_response(self, {"error": "text is required"}, HTTPStatus.BAD_REQUEST)
        _db, session_id, _telegram = latest_session(agent)
        if not session_id:
            return json_response(self, {"error": f"no resumable session found for {agent}"}, HTTPStatus.CONFLICT)

        set_agent_working(agent, True)
        proc = None
        output: list[str] = []
        lines: queue.Queue[str | None] = queue.Queue()

        try:
            proc = subprocess.Popen(
                chat_command(agent, str(session_id), text),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=hermes_env(),
            )

            def reader() -> None:
                assert proc is not None and proc.stdout is not None
                for line in proc.stdout:
                    lines.put(line)
                lines.put(None)

            threading.Thread(target=reader, daemon=True).start()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            deadline = time.monotonic() + CHAT_TIMEOUT_SECONDS
            done = False
            while not done:
                if time.monotonic() > deadline:
                    proc.kill()
                    self.wfile.write(b"\n[timeout]\n")
                    break
                try:
                    item = lines.get(timeout=0.2)
                except queue.Empty:
                    continue
                if item is None:
                    done = True
                    continue
                clean = clean_stream_line(item)
                if not clean:
                    continue
                output.append(clean)
                self.wfile.write(clean.encode("utf-8"))
                self.wfile.flush()
            try:
                rc = proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                rc = -9
            if rc != 0:
                self.wfile.write(f"\n[hermes exited {rc}]\n".encode("utf-8"))
            reply = "".join(output).strip()
            if agent == "orchestrator" and reply:
                mirror_orchestrator_to_telegram(text, reply)
        finally:
            if proc is not None and proc.poll() is None:
                proc.kill()
            set_agent_working(agent, False)

    def cron_action(self, action: str, job_id: str) -> None:
        action = action.strip().lower()
        job_id = job_id.strip()
        if action not in CRON_ACTIONS:
            return json_response(self, {"error": "bad cron action"}, HTTPStatus.BAD_REQUEST)
        if not job_id or job_id not in cron_job_ids():
            return json_response(self, {"error": "cron job id not found"}, HTTPStatus.NOT_FOUND)
        result = subprocess.run(
            [HERMES_BIN, "cron", action, job_id],
            env=hermes_env(),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=CRON_TIMEOUT_SECONDS,
            check=False,
        )
        if result.returncode != 0:
            return json_response(self, {"ok": False, "stdout": result.stdout, "stderr": result.stderr}, HTTPStatus.BAD_GATEWAY)
        self.invalidate_state_cache()
        return json_response(self, {"ok": True, "stdout": result.stdout, "stderr": result.stderr})

    def serve_file(self, path: Path) -> None:
        if not path.exists():
            return self.send_error(HTTPStatus.NOT_FOUND)
        data = path.read_bytes()
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def events(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        for _ in range(3600):
            body = json.dumps(state_snapshot(), ensure_ascii=False)
            self.wfile.write(f"event: state\ndata: {body}\n\n".encode("utf-8"))
            self.wfile.flush()
            time.sleep(3)

def agent_detail(agent: str) -> dict:
    rows = [r for r in log_rows(500) if str(r["agent_name"]).lower() == agent]
    total = len(rows)
    completed = sum(1 for r in rows if r["status"] == "completed")
    models: dict[str, int] = {}
    last_error = None
    recent = []
    for row in rows:
        model = row["model_used"] or "unknown"
        models[model] = models.get(model, 0) + 1
        if row["status"] == "failed" and last_error is None:
            last_error = row["task_description"]
        if len(recent) < 12:
            recent.append({"task": row["task_description"], "status": row["status"], "time": row["created_at"], "model": model})
    return {
        **META[agent],
        "total": total,
        "success": round(100 * completed / total, 1) if total else 100.0,
        "models": [{"model": k, "count": v} for k, v in sorted(models.items(), key=lambda x: -x[1])],
        "last_error": last_error,
        "recent": recent,
    }


def content_list() -> list[dict]:
    docs = []
    for agent in AGENTS:
        root = CONTENT_DIR / agent
        if not root.exists():
            continue
        for path in root.glob("*.md"):
            try:
                first = path.read_text(encoding="utf-8").splitlines()[0]
            except Exception:
                first = ""
            title = first.lstrip("#").strip() or path.stem
            docs.append({"agent": agent, "filename": path.name, "title": title, "modified_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(), "size": path.stat().st_size})
    return sorted(docs, key=lambda d: d["modified_at"], reverse=True)


def main() -> None:
    validate_live_dashboard()
    init_board_db()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Gooros Mission Control serving on http://{HOST}:{PORT}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
