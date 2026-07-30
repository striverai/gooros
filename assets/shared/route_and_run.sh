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
project_dir="${GOOROS_PROJECT_DIR:-${PROJECT_DIR:-$HOME/agent-mission-control}}"
workspace_root="${GOOROS_AGENT_WORKSPACE_ROOT:-$project_dir/workspaces}"
workspace="$workspace_root/$agent"
mkdir -p "$workspace"
session_id="$(python3 "$shared/latest_session.py" "$agent" 2>/dev/null || true)"
resume_args=()
if [[ -n "$session_id" ]]; then
  resume_args=(--resume "$session_id")
fi
tmp_files=()
cleanup_tmp_files() {
  if [[ "${#tmp_files[@]}" -gt 0 ]]; then
    rm -f "${tmp_files[@]}" 2>/dev/null || true
  fi
}
trap cleanup_tmp_files EXIT
new_tmp_file() {
  local path
  path="$(mktemp)"
  tmp_files+=("$path")
  printf '%s' "$path"
}

if [[ "${GOOROS_PROMPT7_AUTO_HANDOFF:-1}" != "0" && "${GOOROS_HANDOFF_ALREADY:-0}" != "1" ]]; then
  target="$(python3 "$shared/handoff-task-local.py" "$agent" "$task" --classify-only 2>/dev/null || true)"
  if [[ -n "$target" && "$target" != "$agent" ]]; then
    label="$target"
    case "$target" in
      scout) label="Scout" ;;
      scribe) label="Scribe" ;;
      reach) label="Reach" ;;
      dev) label="Dev" ;;
    esac
    out_file="$(new_tmp_file)"
    err_file="$(new_tmp_file)"
    set +e
    python3 "$shared/handoff-task-local.py" "$agent" "$task" --to "$target" --project-dir "$project_dir" --shared-dir "$shared" >"$out_file" 2>"$err_file"
    rc=$?
    set -e
    status="completed"
    if [[ "$rc" -ne 0 ]]; then
      status="failed"
    fi
    bash "$shared/log-task-local.sh" "$agent" "Prompt 7 handoff to $target: $task" "$status" "${model:-default}" >/dev/null
    echo "Day la mang cua $label, dang chuyen viec nay cho ho."
    cat "$out_file"
    cat "$err_file" >&2
    exit "$rc"
  fi
fi

out_file="$(new_tmp_file)"
err_file="$(new_tmp_file)"
set +e
if [[ "$agent" == "orchestrator" ]]; then
  cmd="${GOOROS_ORCHESTRATOR_CMD:-hermes}"
  if [[ -n "$model" ]]; then
    (cd "$workspace" && TERMINAL_CWD="$workspace" GOOROS_AGENT_WORKSPACE="$workspace" GOOROS_AGENT_WORKSPACE_ROOT="$workspace_root" "$cmd" chat -Q --no-restore-cwd "${resume_args[@]}" -m "$model" -q "$task") >"$out_file" 2>"$err_file"
  else
    (cd "$workspace" && TERMINAL_CWD="$workspace" GOOROS_AGENT_WORKSPACE="$workspace" GOOROS_AGENT_WORKSPACE_ROOT="$workspace_root" "$cmd" chat -Q --no-restore-cwd "${resume_args[@]}" -q "$task") >"$out_file" 2>"$err_file"
  fi
else
  cmd="${GOOROS_AGENT_CMD_PREFIX:-gooros-$agent}"
  if [[ -n "$model" ]]; then
    (cd "$workspace" && TERMINAL_CWD="$workspace" GOOROS_AGENT_WORKSPACE="$workspace" GOOROS_AGENT_WORKSPACE_ROOT="$workspace_root" "$cmd" chat -Q --no-restore-cwd "${resume_args[@]}" -m "$model" -q "$task") >"$out_file" 2>"$err_file"
  else
    (cd "$workspace" && TERMINAL_CWD="$workspace" GOOROS_AGENT_WORKSPACE="$workspace" GOOROS_AGENT_WORKSPACE_ROOT="$workspace_root" "$cmd" chat -Q --no-restore-cwd "${resume_args[@]}" -q "$task") >"$out_file" 2>"$err_file"
  fi
fi
rc=$?
set -e

status="completed"
if [[ "$rc" -ne 0 ]]; then
  status="failed"
fi
bash "$shared/log-task-local.sh" "$agent" "$task" "$status" "${model:-default}" >/dev/null
cat "$out_file"
cat "$err_file" >&2
exit "$rc"
