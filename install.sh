#!/usr/bin/env bash
set -euo pipefail

DEFAULT_REPO_URL="https://github.com/<owner>/gooros-hermes.git"
REPO_URL="${GOOROS_HERMES_REPO:-$DEFAULT_REPO_URL}"
INSTALL_DIR="${GOOROS_HERMES_SOURCE:-$HOME/.local/share/gooros/hermes-mission-control/repo}"
REF="${GOOROS_HERMES_REF:-main}"

if [[ "$REPO_URL" == *"<owner>"* || "$REPO_URL" == *"<org>"* ]]; then
  cat >&2 <<'EOF'
GOOROS_HERMES_REPO is not configured.

Run with your real GitHub repo URL, for example:
  GOOROS_HERMES_REPO=https://github.com/YOUR_OWNER/gooros-hermes.git bash install.sh

Or replace DEFAULT_REPO_URL in install.sh before publishing.
EOF
  exit 2
fi

need() {
  command -v "$1" >/dev/null 2>&1 || { echo "missing required command: $1" >&2; exit 1; }
}

need git
need python3

mkdir -p "$(dirname "$INSTALL_DIR")"
if [[ -d "$INSTALL_DIR/.git" ]]; then
  git -C "$INSTALL_DIR" fetch --tags --prune
else
  git clone "$REPO_URL" "$INSTALL_DIR"
fi

git -C "$INSTALL_DIR" checkout "$REF"
python3 -m pip install --user -e "$INSTALL_DIR" >/dev/null 2>&1 || true

cat <<EOF
Gooros Hermes CLI source is ready at:
  $INSTALL_DIR

Run:
  python3 -m gooros_hermes.cli install --with-hermes --with-9router --with-public-dashboards --systemd

Or, if the script entry point is on PATH:
  gooros-hermes install --with-hermes --with-9router --with-public-dashboards --systemd

Upgrade later:
  gooros-hermes update
EOF
