#!/usr/bin/env python3
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
    except Exception:
        return set()


def latest_session_id(agent: str, hermes_home: Path) -> str:
    db = hermes_home / "state.db" if agent == "orchestrator" else hermes_home / "profiles" / agent / "state.db"
    if not db.exists():
        return ""
    try:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            tables = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "sessions" not in tables:
                return ""
            cols = table_columns(conn, "sessions")
            id_col = "id" if "id" in cols else ("session_id" if "session_id" in cols else "")
            if not id_col:
                return ""
            ts_col = "started_at" if "started_at" in cols else ("created_at" if "created_at" in cols else id_col)
            where = ""
            if agent == "orchestrator" and "source" in cols:
                where = "WHERE source='telegram'"
            if "archived" in cols:
                where = (where + " AND " if where else "WHERE ") + "(archived IS NULL OR archived=0)"
            row = conn.execute(f"SELECT {id_col} AS id FROM sessions {where} ORDER BY {ts_col} DESC LIMIT 1").fetchone()
            return str(row["id"]) if row and row["id"] else ""
    except Exception:
        return ""


def main(argv: list[str] | None = None) -> int:
    args = argv or sys.argv[1:]
    agent = (args[0] if args else "").strip().lower()
    if agent not in {"orchestrator", "scout", "scribe", "reach", "dev"}:
        return 2
    hermes_home = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser().resolve()
    session_id = latest_session_id(agent, hermes_home)
    if session_id:
        print(session_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
