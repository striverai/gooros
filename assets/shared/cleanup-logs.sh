#!/usr/bin/env bash
set -euo pipefail

RETENTION_DAYS="${RETENTION_DAYS:-7}"
db="${AGENT_LOG_DB:-$HOME/agent-mission-control/agent-logs.db}"

python3 - "$db" "$RETENTION_DAYS" <<'PY'
import sqlite3, sys
from datetime import datetime, timedelta, timezone
db, days = sys.argv[1], int(sys.argv[2])
cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat().replace("+00:00", "Z")
conn = sqlite3.connect(db)
conn.executescript("""
CREATE TABLE IF NOT EXISTS agent_logs (
  id TEXT PRIMARY KEY,
  agent_name TEXT NOT NULL,
  task_description TEXT NOT NULL,
  model_used TEXT,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_logs_created_at ON agent_logs(created_at DESC);
""")
before = conn.execute("SELECT COUNT(*) FROM agent_logs").fetchone()[0]
conn.execute("DELETE FROM agent_logs WHERE created_at < ?", (cutoff,))
conn.commit()
after = conn.execute("SELECT COUNT(*) FROM agent_logs").fetchone()[0]
conn.execute("VACUUM")
print(f"deleted={before-after} remaining={after} retention_days={days}")
PY

