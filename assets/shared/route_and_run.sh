#!/usr/bin/env bash
set -euo pipefail

agent="${1:-}"
task="${2:-}"
if [[ -z "$agent" || -z "$task" ]]; then
  echo "usage: route_and_run.sh <agent> \"<task>\"" >&2
  exit 2
fi

shared="${HERMES_SHARED_DIR:-$HOME/.hermes/agents/_shared}"
model="$(python3 "$shared/route_model.py" "$task")"

if [[ "$agent" == "orchestrator" ]]; then
  hermes chat -Q --no-restore-cwd -m "$model" -q "$task"
else
  cmd="${GOOROS_AGENT_CMD_PREFIX:-gooros-$agent}"
  "$cmd" chat -Q --no-restore-cwd -m "$model" -q "$task"
fi

bash "$shared/log-task-local.sh" "$agent" "$task" "completed" "$model"

