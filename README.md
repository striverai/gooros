# Gooros Hermes Mission Control CLI

Safe installer/updater for a 5-agent Hermes Mission Control setup:

- Orchestrator as the default Hermes agent.
- Specialist profiles: Scout, Scribe, Reach, Dev.
- Telegram topic routing.
- Local agent logging and cleanup.
- Live Mission Control dashboard.
- Hermes native dashboard.
- 9Router dashboard and local OpenAI-compatible endpoint.
- Optional public HTTPS URLs through sslip.io + Caddy + auth.

## Install From GitHub

After publishing this repository, customers can bootstrap the CLI from GitHub:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/striverai/gooros/main/install.sh)"
```

Then run the fresh install:

```bash
gooros-hermes install --with-hermes --with-9router --with-public-dashboards --systemd
```

For non-interactive customer installs, pass the required `--owner-*`, Telegram topic IDs, public IP, and ACME email flags shown below.

## Fresh Install

Run from a Linux VPS as the target user. Use `--systemd` only when the user has root/sudo rights.

```bash
python3 -m gooros_hermes.cli install \
  --with-hermes \
  --with-9router \
  --with-public-dashboards \
  --systemd
```

Non-interactive example:

```bash
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
gooros-hermes verify --public
```

## Upgrade

Customers upgrade like an open-source CLI:

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

## Publish To GitHub

Create an empty GitHub repository, then push this local repo:

```bash
git remote add origin https://github.com/striverai/gooros.git
git branch -M main
git push -u origin main
git tag v0.1.0
git push origin v0.1.0
```

The repository is configured for public installs from `striverai/gooros`.
