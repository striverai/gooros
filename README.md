# Gooros Hermes Mission Control CLI

Safe installer/updater for a 5-agent Hermes Mission Control setup:

- Orchestrator as the default Hermes agent.
- Specialist profiles: Scout, Scribe, Reach, Dev.
- Telegram topic routing.
- Local agent logging and cleanup.
- Live Mission Control dashboard with no demo-data fallback.
- Hermes native dashboard.
- 9Router dashboard and local OpenAI-compatible endpoint.
- Optional public HTTPS URLs through sslip.io + Caddy + auth.

For controlled customer pilots, use [INTERNAL_PILOT.md](INTERNAL_PILOT.md)
as the release, install, verify, and rollback checklist.

## Install From GitHub

Internal pilot customers can bootstrap the CLI from the configured GitHub release source:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/striverai/gooros/main/install.sh)"
```

Then run the fresh install:

```bash
gooros-hermes install --with-hermes --with-9router --with-public-dashboards --systemd
```

Interactive install prompts for the Telegram bot token, group chat ID, topic thread IDs, public IP, and dashboard auth details. Bot tokens are written only to local secret files, not to this repository.

For Codex-assisted or non-interactive customer installs, use a local env file:

```bash
cp ~/.local/share/gooros/hermes-mission-control/repo/examples/customer.env.example ~/gooros-customer.env
nano ~/gooros-customer.env
chmod 600 ~/gooros-customer.env

gooros-hermes install \
  --yes \
  --env-file ~/gooros-customer.env \
  --with-hermes \
  --with-9router \
  --with-public-dashboards \
  --systemd
```

Codex can run the final `gooros-hermes install --env-file ...` command after the customer fills the local file. Do not paste the filled token file into GitHub or chat logs.

You can also set `TELEGRAM_BOT_TOKEN` in the shell and pass values as flags:

## Fresh Install

Run from a Linux VPS as the target user. Do not prefix the whole command with
`sudo`; the installer asks sudo only for Caddy/systemd files. Use `--systemd`
only when the user has root/sudo rights.

```bash
python3 -m gooros_hermes.cli install \
  --with-hermes \
  --with-9router \
  --with-public-dashboards \
  --systemd
```

Non-interactive example:

```bash
export TELEGRAM_BOT_TOKEN="123456789:REPLACE_WITH_CUSTOMER_BOT_TOKEN"

python3 -m gooros_hermes.cli install \
  --yes \
  --with-hermes \
  --with-9router \
  --with-public-dashboards \
  --systemd \
  --owner-name "Customer Name" \
  --owner-work "Customer business" \
  --owner-focus "Current goal" \
  --timezone "Asia/Ho_Chi_Minh" \
  --telegram-chat-id "-1001234567890" \
  --telegram-allowed-users "123456789,987654321" \
  --thread-scout "11" \
  --thread-scribe "12" \
  --thread-reach "13" \
  --thread-dev "14" \
  --telegram-home-channel "telegram:-1001234567890" \
  --public-ip "203.0.113.10" \
  --acme-email "owner@example.com"
```

## Public URLs

For public VPS IP `203.0.113.10`, the installer creates:

```text
https://mission.203.0.113.10.sslip.io
https://hermes.203.0.113.10.sslip.io
https://router.203.0.113.10.sslip.io
```

Caddy handles free automatic HTTPS renewal. The upstream apps stay bound to localhost.

The installer verifies the local upstreams before reporting success:

```text
Mission Control: http://127.0.0.1:51763/api/state
Hermes native:   http://127.0.0.1:9119
9Router:         http://127.0.0.1:20128/dashboard and /v1/models
```

For `--with-9router`, the CLI waits for 9Router, discovers available models,
selects a DeepSeek/free model first when one exists, runs a tiny
`/v1/chat/completions` smoke test, then switches Hermes to
`model.provider=custom` and `model.base_url=http://127.0.0.1:20128/v1`.
On a fresh install, 9Router's initial dashboard password is set to the same
generated dashboard auth password printed once by the installer.
If the smoke test fails, connect a working free provider in the 9Router
dashboard and rerun the install/update. For UI-only emergency updates, set
`GOOROS_9ROUTER_SMOKE=0` before `gooros-hermes update`.

## Safety Contract

The CLI must not overwrite customer Hermes state:

- `~/.hermes/state.db`
- `~/.hermes/kanban.db`
- `~/.hermes/profiles/*/state.db`
- existing secrets, sessions, memories, customer plugins, customer profiles

It creates a pre-install snapshot, merges only allowed config keys, and fails on unmanaged profile/plugin conflicts.

## Commands

```bash
gooros-hermes doctor
gooros-hermes plan --yes --with-hermes --with-9router --with-public-dashboards
gooros-hermes install --with-hermes --with-9router --with-public-dashboards --systemd
gooros-hermes update
gooros-hermes verify --public --with-9router
```

## Upgrade

Customers upgrade through the Git-backed CLI:

```bash
gooros-hermes update
```

The updater reads the stored Git release source from `~/agent-mission-control/.gooros/installed.json`, fetches the latest tag, validates the release manifest, snapshots the customer instance, stages the new release, runs idempotent migrations, applies only release-owned files, merges only managed config keys, verifies the result, then switches the active release.

Preview an upgrade without touching runtime files:

```bash
gooros-hermes update --plan
```

If an upgrade fails, the CLI automatically restores the pre-update snapshot. Manual rollback is also available:

```bash
gooros-hermes rollback
```

Manual rollback preserves customer DB/content by default. Use `--restore-data` only when intentionally restoring those files from the snapshot.

`auth rotate` is still scaffolded and must be completed before publishing public dashboard password rotation.

## Release Source

This repository is the configured internal pilot release source:

```bash
git remote -v
git status --short --branch
TARGET_TAG=v0.1.8
git rev-parse --verify "$TARGET_TAG" >/dev/null 2>&1 || git tag "$TARGET_TAG"
git push origin main --tags
```

Do not give customers an install command until the target commit is clean,
tagged, pushed, and the internal pilot checklist passes.
