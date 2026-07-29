# Gooros Hermes Internal Pilot Runbook

This runbook is for controlled customer pilots, not a public open-source launch.
Keep the repository proprietary and use GitHub only as the release source.

## 1. Release Readiness Gate

Run these checks before giving a customer an install command:

```bash
git status --short --branch
python tests/smoke.py
gooros-hermes plan --yes --env-file ~/gooros-customer.env --with-hermes --with-9router --with-public-dashboards --systemd
```

The release is ready only when:

- `git status` is clean.
- `pyproject.toml`, `gooros_hermes/constants.py`, `gooros_hermes/__init__.py`, and `manifests/release.json` all use the same version.
- Every migration in `manifests/release.json` has a matching file under `migrations/`.
- `python tests/smoke.py` passes.
- `gooros-hermes update --plan --source-dir . ...` shows the expected target version and migration list.

For a customer-visible release, tag the exact commit:

```bash
TARGET_TAG=v0.1.8
git rev-parse --verify "$TARGET_TAG" >/dev/null 2>&1 || git tag "$TARGET_TAG"
git push origin main --tags
git ls-remote origin refs/heads/main "refs/tags/$TARGET_TAG"
```

## 2. Customer Fresh Install

Prepare a local customer env file on the VPS. Never paste the filled file into
GitHub, chat, issue trackers, or logs.

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/striverai/gooros/main/install.sh)"

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

Do not run the whole install command with `sudo`. The installer asks for sudo
only when it needs to write Caddy or systemd files.

## 3. Customer Upgrade Test

Preview first:

```bash
gooros-hermes update --plan
```

Apply only if the plan shows the intended target version, release id, modules,
and migrations:

```bash
gooros-hermes update
```

If the customer only needs dashboard/UI fixes and 9Router provider setup is not
ready yet:

```bash
GOOROS_9ROUTER_SMOKE=0 gooros-hermes update
```

Use that bypass only for a short pilot window. Re-enable the 9Router smoke test
once the provider is connected.

## 4. Post-Install Verification

Run:

```bash
gooros-hermes verify --public --with-9router
gooros-hermes doctor
```

Confirm manually:

- Mission Control opens at the `mission.<ip>.sslip.io` URL and requires auth.
- Hermes native dashboard opens at the `hermes.<ip>.sslip.io` URL and requires auth.
- 9Router dashboard opens at the `router.<ip>.sslip.io/dashboard` URL and requires auth.
- `https://router.<ip>.sslip.io/v1/*` is blocked publicly.
- Telegram routes the four forum topics to Scout, Scribe, Reach, and Dev.
- `~/.hermes/state.db`, `~/.hermes/kanban.db`, profile state DBs, secrets, sessions, memories, and customer content are still present after an update.

## 5. Rollback

Rollback preserves customer board/content by default:

```bash
gooros-hermes rollback --yes
```

Only restore customer data when explicitly required:

```bash
gooros-hermes rollback --yes --restore-data
```

After rollback, run:

```bash
gooros-hermes verify --public --with-9router
```

## 6. Pilot Acceptance

Mark the pilot install as accepted only when:

- Fresh install or upgrade finishes with `Verification: passed`.
- Dashboard has no demo-data fallback.
- Public dashboards are behind basic auth.
- 9Router `/v1/models` returns at least one usable model unless the pilot is explicitly UI-only.
- Telegram topic routing works after sending `/new` once in each target topic if old sessions were cached.
- A snapshot exists under the Gooros data directory before any install/update mutation.
