#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from email.parser import BytesParser
from email.policy import default as email_policy
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PROJECT_DIR = Path(os.environ.get("PROJECT_DIR", Path(__file__).resolve().parent)).expanduser().resolve()
HERMES_HOME = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser().resolve()
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "51763"))
AGENT_LOG_DB = Path(os.environ.get("AGENT_LOG_DB", PROJECT_DIR / "agent-logs.db")).expanduser().resolve()
BOARD_DB = Path(os.environ.get("BOARD_DB", PROJECT_DIR / "board.db")).expanduser().resolve()
CONTENT_DIR = Path(os.environ.get("CONTENT_DIR", PROJECT_DIR / "content")).expanduser().resolve()
TELEGRAM_HOME_CHANNEL = os.environ.get("TELEGRAM_HOME_CHANNEL", "")
CACHE_SECONDS = 3

AGENTS = ("orchestrator", "scout", "scribe", "reach", "dev")
SPECIALISTS = ("scout", "scribe", "reach", "dev")
META = {
    "orchestrator": {"code": "A-00", "initials": "OR", "name": "Orchestrator", "role": "Coordinator", "channel": "telegram"},
    "scout": {"code": "A-01", "initials": "SC", "name": "Scout", "role": "Research", "channel": "#scout"},
    "scribe": {"code": "A-02", "initials": "SB", "name": "Scribe", "role": "Writing", "channel": "#scribe"},
    "reach": {"code": "A-03", "initials": "RE", "name": "Reach", "role": "Marketing", "channel": "#reach"},
    "dev": {"code": "A-04", "initials": "DV", "name": "Dev", "role": "Engineering", "channel": "#dev"},
}
STATUS_MAP = {"pending": "todo", "in_progress": "doing", "done": "done"}
PRIORITY_MAP = {"high": "P1", "medium": "P2", "low": "P3"}
WORKING_AGENTS: set[str] = set()
WORKING_LOCK = threading.Lock()
STATE_CACHE: tuple[float, dict] = (0.0, {})


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def ensure_dirs() -> None:
    PROJECT_DIR.mkdir(parents=True, exist_ok=True)
    CONTENT_DIR.mkdir(parents=True, exist_ok=True)
    for agent in AGENTS:
        (CONTENT_DIR / agent).mkdir(parents=True, exist_ok=True)


def connect_rw(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def connect_ro(path: Path) -> sqlite3.Connection:
    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=1")
    return conn


def init_log_db() -> None:
    with connect_rw(AGENT_LOG_DB) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS agent_logs (
              id TEXT PRIMARY KEY,
              agent_name TEXT NOT NULL,
              task_description TEXT NOT NULL,
              model_used TEXT,
              status TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_agent_logs_agent_name ON agent_logs(agent_name);
            CREATE INDEX IF NOT EXISTS idx_agent_logs_status ON agent_logs(status);
            CREATE INDEX IF NOT EXISTS idx_agent_logs_created_at ON agent_logs(created_at DESC);
            """
        )


def init_board_db() -> None:
    with connect_rw(BOARD_DB) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
              id TEXT PRIMARY KEY,
              title TEXT NOT NULL,
              status TEXT NOT NULL,
              priority TEXT NOT NULL,
              notes TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            """
        )
        count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        if count == 0:
            seeds = [
                ("seed-outline-script", "Outline next week's video script", "pending", "high"),
                ("seed-sponsor-emails", "Reply to sponsor and collab emails", "pending", "medium"),
                ("seed-edit-video", "Edit this week's YouTube video", "in_progress", "high"),
                ("seed-launch-newsletter", "Write the launch-day newsletter", "in_progress", "medium"),
                ("seed-publish-blog", "Publish the new blog post", "done", "medium"),
                ("seed-social-posts", "Schedule this week's social posts", "done", "low"),
            ]
            now = utcnow()
            conn.executemany(
                "INSERT INTO tasks(id,title,status,priority,notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                [(i, t, s, p, "", now, now) for i, t, s, p in seeds],
            )


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
        with connect_ro(db) as conn:
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
    init_log_db()
    with connect_rw(AGENT_LOG_DB) as conn:
        return list(conn.execute("SELECT * FROM agent_logs ORDER BY created_at DESC LIMIT ?", (limit,)))


def read_fleet(rows: list[sqlite3.Row]) -> list[dict]:
    total = len(rows)
    by_agent = {agent: [] for agent in AGENTS}
    for row in rows:
        agent = str(row["agent_name"]).lower()
        if agent in by_agent:
            by_agent[agent].append(row)
    fleet = []
    with WORKING_LOCK:
        working = set(WORKING_AGENTS)
    for agent in AGENTS:
        agent_rows = by_agent[agent]
        count = len(agent_rows)
        completed = sum(1 for r in agent_rows if str(r["status"]).lower() == "completed")
        meta = META[agent]
        latest = agent_rows[0] if agent_rows else None
        fleet.append(
            {
                **meta,
                "tasksToday": count,
                "success": round(100 * completed / count, 1) if count else 100.0,
                "defaultModel": latest["model_used"] if latest and latest["model_used"] else "",
                "share": round(100 * count / total) if total else 0,
                "load": min(100, count * 25),
                "tokens": f"{count} tasks",
                "latency": "-",
                "state": "EXECUTING" if agent in working else "IDLE",
                "task": latest["task_description"] if latest else "No tasks logged yet.",
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
    models = [{"id": m, "label": m, "vendor": "", "tier": tiers.get(m, "")} for m in sorted(counts)]
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
    with connect_rw(BOARD_DB) as conn:
        rows = conn.execute("SELECT * FROM tasks ORDER BY created_at ASC").fetchall()
    return [dict(r) for r in rows]


def read_cron() -> list[dict]:
    data = safe_json(HERMES_HOME / "cron" / "jobs.json", [])
    if isinstance(data, dict):
        jobs = data.get("jobs", data.get("items", []))
        if isinstance(jobs, dict):
            jobs = list(jobs.values())
    elif isinstance(data, list):
        jobs = data
    else:
        jobs = []
    out = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        out.append(
            {
                "id": str(job.get("id", "")),
                "name": job.get("name") or job.get("title") or "Untitled",
                "enabled": bool(job.get("enabled", job.get("state") != "paused")),
                "state": job.get("state") or ("active" if job.get("enabled", True) else "paused"),
                "schedule": job.get("schedule") or job.get("cron") or "",
                "next_run_at": job.get("next_run_at") or job.get("nextRunAt"),
                "last_status": job.get("last_status") or job.get("lastStatus"),
                "last_error": job.get("last_error") or job.get("lastError"),
                "deliver": job.get("deliver") or job.get("delivery") or job.get("to"),
                "model": job.get("model") or job.get("model_id"),
                "prompt": job.get("prompt") or job.get("query") or "",
            }
        )
    return out


def state_snapshot() -> dict:
    global STATE_CACHE
    now = time.time()
    if now - STATE_CACHE[0] < CACHE_SECONDS:
        return STATE_CACHE[1]
    rows = log_rows()
    models, usage, routing = read_models(rows)
    agentlogs, agentlog_stats = read_agentlogs(rows)
    with WORKING_LOCK:
        working = sorted(WORKING_AGENTS)
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
        "working_agents": working,
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
    agent = safe_agent(agent)
    filename = safe_filename(filename)
    path = (CONTENT_DIR / agent / filename).resolve()
    root = (CONTENT_DIR / agent).resolve()
    if root not in path.parents:
        raise ValueError("bad path")
    return path


def latest_session(agent: str) -> tuple[Path, str | None, bool]:
    db = HERMES_HOME / "state.db" if agent == "orchestrator" else HERMES_HOME / "profiles" / agent / "state.db"
    if not db.exists():
        return db, None, False
    try:
        with connect_ro(db) as conn:
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
        with connect_ro(db) as conn:
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


def agent_command(agent: str) -> list[str]:
    if agent == "orchestrator":
        return ["hermes"]
    env_key = f"GOOROS_{agent.upper()}_CMD"
    configured = os.environ.get(env_key)
    if configured:
        return configured.split()
    wrapper = shutil.which(f"gooros-{agent}") or shutil.which(agent)
    if wrapper:
        return [wrapper]
    raise RuntimeError(f"profile wrapper for {agent} not found; run the Gooros profile alias setup before sending chat")


def clean_stream_text(text: str) -> str:
    lines = []
    for line in text.splitlines(True):
        stripped = line.strip()
        if stripped.startswith("↻ Working directory:") or stripped.startswith("Working directory:"):
            continue
        if stripped.startswith("↻ Resumed") or stripped.startswith("session_id:"):
            continue
        lines.append(line)
    return "".join(lines)


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
        if path == "/gooros-logo.png":
            return self.serve_file(PROJECT_DIR / "gooros-logo.png")
        if path == "/upload":
            return text_response(self, upload_html(), content_type="text/html; charset=utf-8")
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
            with connect_rw(BOARD_DB) as conn:
                conn.execute(
                    "INSERT INTO tasks(id,title,status,priority,notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                    (task_id, str(payload.get("title", "Untitled"))[:180], payload.get("status", "pending"), payload.get("priority", "medium"), payload.get("notes", ""), now, now),
                )
            return json_response(self, {"ok": True, "id": task_id})
        if path == "/api/board/update":
            task_id = qs.get("id", [""])[0]
            payload = self.read_json_body()
            fields = []
            values = []
            for key in ("title", "status", "priority", "notes"):
                if key in payload:
                    fields.append(f"{key}=?")
                    values.append(str(payload[key]))
            fields.append("updated_at=?")
            values.append(utcnow())
            values.append(task_id)
            with connect_rw(BOARD_DB) as conn:
                conn.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE id=?", values)
            return json_response(self, {"ok": True})
        if path == "/api/board/delete":
            with connect_rw(BOARD_DB) as conn:
                conn.execute("DELETE FROM tasks WHERE id=?", (qs.get("id", [""])[0],))
            return json_response(self, {"ok": True})
        if path == "/api/chat/send":
            return self.chat_send()
        if path == "/api/content/save":
            p = content_path(qs.get("agent", [""])[0], qs.get("file", [""])[0])
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            return json_response(self, {"ok": True})
        if path == "/api/content/delete":
            p = content_path(qs.get("agent", [""])[0], qs.get("file", [""])[0])
            if not p.exists():
                return json_response(self, {"ok": False, "error": "not found"}, 404)
            p.unlink()
            return json_response(self, {"ok": True})
        if path == "/api/cron/action":
            return self.cron_action(qs.get("action", [""])[0], qs.get("id", [""])[0])
        if path == "/api/upload":
            return self.upload()
        self.send_error(HTTPStatus.NOT_FOUND)

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

    def read_json_body(self) -> dict:
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        return json.loads(raw.decode("utf-8") or "{}")

    def chat_send(self) -> None:
        payload = self.read_json_body()
        agent = safe_agent(str(payload.get("agent", "")))
        text = str(payload.get("text", "")).strip()
        if not text:
            return json_response(self, {"error": "empty text"}, 400)
        _, session_id, _ = latest_session(agent)
        cmd = agent_command(agent) + ["chat", "-Q", "--no-restore-cwd", "--source", "gooros-dashboard"]
        if session_id:
            cmd += ["--resume", session_id]
        cmd += ["-q", text]
        env = os.environ.copy()
        env["HERMES_HOME"] = str(HERMES_HOME)
        env["TIRITH_ENABLED"] = "false"
        with WORKING_LOCK:
            WORKING_AGENTS.add(agent)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
            assert proc.stdout is not None
            full = []
            for chunk in iter(lambda: proc.stdout.readline(), ""):
                clean = clean_stream_text(chunk)
                if clean:
                    full.append(clean)
                    self.wfile.write(clean.encode("utf-8"))
                    self.wfile.flush()
            proc.wait(timeout=180)
            if agent == "orchestrator" and TELEGRAM_HOME_CHANNEL and full:
                subprocess.run(["hermes", "send", "--to", TELEGRAM_HOME_CHANNEL, "".join(full).strip()], env=env, timeout=30, check=False)
        finally:
            with WORKING_LOCK:
                WORKING_AGENTS.discard(agent)

    def cron_action(self, action: str, job_id: str) -> None:
        if action not in {"run", "pause", "resume", "delete"}:
            return json_response(self, {"error": "bad action"}, 400)
        jobs = read_cron()
        if not any(j.get("id") == job_id for j in jobs):
            return json_response(self, {"error": "job not found"}, 404)
        env = os.environ.copy()
        env["HERMES_HOME"] = str(HERMES_HOME)
        subprocess.run(["hermes", "cron", action, job_id], env=env, timeout=120, check=True)
        global STATE_CACHE
        STATE_CACHE = (0, {})
        return json_response(self, {"ok": True})

    def upload(self) -> None:
        content_type = self.headers.get("Content-Type", "")
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        header = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
        message = BytesParser(policy=email_policy).parsebytes(header + body)
        field = None
        for part in message.iter_parts():
            if part.get_param("name", header="content-disposition") == "file":
                field = part
                break
        if field is None or not field.get_filename():
            return json_response(self, {"ok": False, "error": "missing file"}, 400)
        filename = Path(field.get_filename() or "").name
        if "/" in filename or "\\" in filename or ".." in filename:
            return json_response(self, {"ok": False, "error": "bad filename"}, 400)
        if Path(filename).suffix.lower() not in {".html", ".png", ".jpg", ".jpeg", ".webp", ".svg"}:
            return json_response(self, {"ok": False, "error": "unsupported file"}, 400)
        dst = PROJECT_DIR / filename
        dst.write_bytes(field.get_payload(decode=True) or b"")
        return json_response(self, {"ok": True, "filename": filename, "path": str(dst)})


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
        root.mkdir(parents=True, exist_ok=True)
        for path in root.glob("*.md"):
            try:
                first = path.read_text(encoding="utf-8").splitlines()[0]
            except Exception:
                first = ""
            title = first.lstrip("#").strip() or path.stem
            docs.append({"agent": agent, "filename": path.name, "title": title, "modified_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(), "size": path.stat().st_size})
    return sorted(docs, key=lambda d: d["modified_at"], reverse=True)


def upload_html() -> str:
    return """<!doctype html><html><head><meta charset='utf-8'><title>Upload template</title>
<style>body{font-family:system-ui;margin:40px;max-width:720px}button,input{font:inherit;padding:10px}pre{background:#f4f4f4;padding:12px}</style></head>
<body><h1>Upload dashboard template</h1><input id='f' type='file' accept='.html,.png,.jpg,.jpeg,.webp,.svg'>
<button id='b'>Upload</button><pre id='out'></pre><script>
document.getElementById('b').onclick=async()=>{const file=document.getElementById('f').files[0];if(!file)return;
const fd=new FormData();fd.append('file',file);const r=await fetch('/api/upload',{method:'POST',body:fd});
document.getElementById('out').textContent=JSON.stringify(await r.json(),null,2)};
</script></body></html>"""


def main() -> None:
    ensure_dirs()
    init_log_db()
    init_board_db()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Gooros Mission Control serving on http://{HOST}:{PORT}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
