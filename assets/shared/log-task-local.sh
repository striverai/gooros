#!/usr/bin/env bash
set -euo pipefail

agent="${1:-}"
task="${2:-}"
status="${3:-}"
model="${4:-}"
db="${AGENT_LOG_DB:-$HOME/agent-mission-control/agent-logs.db}"

if [[ -z "$agent" || -z "$task" || -z "$status" || -z "$model" ]]; then
  echo "usage: log-task-local.sh <agent> <task> <completed|failed> <model>" >&2
  exit 2
fi
if [[ "$status" != "completed" && "$status" != "failed" ]]; then
  echo "status must be completed or failed" >&2
  exit 2
fi

python3 - "$db" "$agent" "$task" "$status" "$model" <<'PY'
import sqlite3, sys, uuid
from datetime import datetime, timezone
db, agent, task, status, model = sys.argv[1:6]
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
CREATE INDEX IF NOT EXISTS idx_agent_logs_agent_name ON agent_logs(agent_name);
CREATE INDEX IF NOT EXISTS idx_agent_logs_status ON agent_logs(status);
CREATE INDEX IF NOT EXISTS idx_agent_logs_created_at ON agent_logs(created_at DESC);
""")
conn.execute(
    "INSERT INTO agent_logs(id, agent_name, task_description, model_used, status, created_at) VALUES(?,?,?,?,?,?)",
    (str(uuid.uuid4()), agent, task[:140], model, status, datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")),
)
conn.commit()
print(f"LOGGED: {agent} | {status} | {model}")
PY

