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

To bootstrap and install in one non-interactive pass after preparing the env file:

```bash
GOOROS_HERMES_ENV_FILE=~/gooros-customer.env \
GOOROS_HERMES_RUN_INSTALL=1 \
bash -c "$(curl -fsSL https://raw.githubusercontent.com/striverai/gooros/main/install.sh)"
```

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
  --owner-working-hours "Asia/Ho_Chi_Minh, 09:00-18:00" \
  --owner-important-people "Key clients, partners, and accounts that must never be missed" \
  --owner-cares-about "Track critical relationships; delegate research, writing, growth, and engineering" \
  --timezone "Asia/Ho_Chi_Minh" \
  --telegram-chat-id "-1001234567890" \
  --telegram-allowed-users "123456789,987654321" \
  --thread-command "10" \
  --thread-scout "11" \
  --thread-scribe "12" \
  --thread-reach "13" \
  --thread-dev "14" \
  --telegram-home-channel "telegram:-1001234567890:10" \
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

For `--with-9router`, the CLI waits for 9Router, logs into the local
management API when dashboard auth is enabled, creates/reuses a real 9Router
API key via `/api/keys`, and builds the `gooros-free-combo` from the required
no-auth free model catalogs:

- OpenCode Free (`opencode`, model prefix `oc/...`)
- MiMo Code Free (`mimo-free`, model prefix `mmf/...`)

The installer calls 9Router's suggested-model catalog endpoints, includes every
model returned for those two providers, orders DeepSeek models first, enables
round-robin combo rotation, smoke-tests the combo through
`/v1/chat/completions`, then switches Hermes root plus every Gooros-managed
profile to `model.provider=custom`,
`model.base_url=http://127.0.0.1:20128/v1`, and
`model.default=gooros-free-combo`.
On a fresh install, 9Router's initial dashboard password is set to the same
generated dashboard auth password printed once by the installer. The installer
uses that local-only password to log into 9Router management APIs before
creating/reusing API keys and combos.
If Gooros cannot fetch either required free catalog, the install/update fails
before switching Hermes to an incomplete combo. If the smoke test fails, check
9Router network access/provider status and rerun the install/update. For
infrastructure-only pilot installs or UI-only emergency updates, set
`GOOROS_9ROUTER_SMOKE=0` before `gooros-hermes install` or
`gooros-hermes update`, then re-enable the smoke test after providers work.

Telegram is also made active during install/update: the CLI writes Hermes
Telegram env/config, preserves any existing config token line, sets
`platforms.telegram.require_mention=false`, allowlists the configured
`group_allowed_chats` ID, installs the `telegram_topic_profiles` plugin, enables
`multiplex_profiles`, then ensures the Hermes gateway service is installed,
started, and restarted. On first Telegram contact, the plugin runs an adaptive
owner onboarding interview: it collects the Prompt 1 owner profile, then asks
Prompt 2 follow-ups based on those answers before saving the completed profile
into Hermes long-term memory. `verify` checks the Telegram env, group access
config, topic map, onboarding config, plugin files, and gateway status so a
non-chatting bot is reported as a failed install.

The installer also seeds Prompt 3 fixed operating rules into the Orchestrator
SOUL and Hermes long-term memory: Vietnamese replies, progress lines,
approval-before-action, concise communication, explicit delegation, and honest
failure reporting.

Prompt 4 is implemented through durable Hermes profiles: Orchestrator remains
the root/default Telegram agent, while Scout, Scribe, Reach, and Dev are
long-lived profiles with their own SOUL.md, memories, and private workspace
directories under Mission Control.

Prompt 5 is enforced as a real install gate. The four specialist SOUL files
include the exact Prompt 5 identity text with the customer's owner name
rendered into it, and the installer asks each profile `Bạn là ai?` in sequence
after model/runtime setup. Install verification writes and checks
`~/agent-mission-control/reports/prompt5-agent-identity-verification.{json,md}`
with each profile path, SOUL.md confirmation, live response, and pass/fail
rubric.

Prompt 6 adds hard boundaries across all five agents. Every agent, including
Orchestrator, gets a private workspace under `~/agent-mission-control/workspaces/<agent>/`,
a Prompt 6 memory policy, a stable identity/role/personality rule, and a
one-line out-of-scope boundary rule that names the correct teammate. Shared
runtime now resumes the latest session per agent and exposes
`append-agent-memory.py` so long-term memory writes are scope-checked before
they enter an agent's private memory. Verification writes and checks
`~/agent-mission-control/reports/prompt6-agent-boundaries-continuity-verification.{json,md}`
with identity answers, workspace checks, and role-boundary refusal checks for
all five agents.

Prompt 7 adds shared team awareness and real handoff behavior. Orchestrator and
all four specialists get a Prompt 7 SOUL block and long-term memory seed naming
the owner, Orchestrator, Scout, Scribe, Reach, and Dev. When a task clearly
belongs to another teammate, the agent must say a one-line handoff such as
`Đây là mảng của Dev, đang chuyển việc này cho họ.` and transfer through
`handoff-task-local.py`. `route_and_run.sh` auto-handoffs obvious mismatches,
and the Telegram topic plugin reroutes obvious out-of-scope topic messages while
recording `prompt7_handoff_from` in the dashboard board. Verification writes
and checks
`~/agent-mission-control/reports/prompt7-team-awareness-handoff-verification.{json,md}`.

Prompt 9 makes activity logging a fixed rule for all five agents. Orchestrator,
Scout, Scribe, Reach, and Dev each get a SOUL policy and long-term memory seed
requiring `log-task-local.sh` before any response, with lowercase agent name,
non-empty task/status/model fields, `completed` or `failed` status, exact current
model, and a description under 140 characters. The shared `route_and_run.sh`
captures agent output, writes the log first, hides logging chatter, then returns
the response. Install verification smoke-tests all five agents with
`saved activity logging rule to memory`, confirms the rows in
`~/agent-mission-control/agent-logs.db`, and writes the latest five log rows to
`~/agent-mission-control/reports/prompt9-activity-logging-verification.{json,md}`.

Prompt 10 caps local log growth. `cleanup-logs.sh` keeps
`~/agent-mission-control/agent-logs.db` at a 7-day retention window, creates the
DB/table/indexes safely on a new install, deletes old rows, runs `VACUUM`, and
prints `deleted=<n> remaining=<n> retention_days=7`. The installer adds the exact
weekly cron line for Sunday 03:00 server time, runs cleanup once immediately,
prints the summary, and writes
`~/agent-mission-control/reports/prompt10-log-retention-verification.{json,md}`.

Prompt 11 defines the Telegram command centre plan and verifies topic routing.
The required topics are `#command`, `#scout`, `#scribe`, `#reach`, and `#dev`.
`#command` stays on the root/default Orchestrator; the four specialist topics
route directly to their persistent Hermes profiles through
`telegram_topic_profiles` and `multiplex_profiles=true`. The installer writes the
four specialist `thread_id -> profile` routes, leaves `#command` out of
`topics.json` so it stays on the default Orchestrator, simulates the plugin hook for each topic, and
writes `~/agent-mission-control/reports/prompt11-telegram-topic-routing-verification.{json,md}`.

Prompt 12 verifies Telegram group access. The installer resolves the live Hermes
config path with `hermes config path`, merges only the Prompt 12 Telegram group
keys, keeps existing `platforms.telegram.token` lines unchanged, restarts the
gateway, checks gateway health, and writes
`~/agent-mission-control/reports/prompt12-telegram-group-access-verification.{json,md}`.
Final owner acceptance is explicit: send `xin chao` in any Telegram topic and
confirm the bot answers there.

Prompt 13 isolates specialist profiles for true topic routing. Scout, Scribe,
Reach, and Dev are durable Hermes profiles with their own `SOUL.md`, memory
directory, model/tool config, and workspace, but they must not own messaging
platforms. Install/update removes messaging `platforms:`/top-level platform
blocks from each specialist `config.yaml`, scrubs Telegram/Discord/Slack/etc.
keys from each specialist `.env`, retires any legacy `profiles/orchestrator`,
and writes
`~/agent-mission-control/reports/prompt13-specialist-profile-isolation-verification.{json,md}`.
Only the root/default Orchestrator keeps the Telegram bot.

Prompt 15 is available as an exact staging gate for the first routing plugin
creation step. `gooros-hermes prompt15-plugin` writes only three files under
`~/.hermes/plugins/telegram_topic_profiles/`: `plugin.yaml`, `topics.json`, and
`__init__.py`. The manifest is the required Prompt 15 `1.0.0` standalone
manifest, `topics.json` contains only the four specialist topic routes
(`#scout`, `#scribe`, `#reach`, `#dev`) and never `#command`, onboarding, or
board sync keys, and the command explicitly does not enable the plugin or
restart the gateway. It writes
`~/agent-mission-control/reports/prompt15-routing-plugin-creation-verification.{json,md}`.
Install/update still use the later production plugin that adds Prompt 1/2/7/11
behavior after Prompt 15 has been staged.

Prompt 16 is a real activation gate. `gooros-hermes prompt16-activate` runs the
three required Hermes commands in order: `hermes plugins enable
telegram_topic_profiles`, `hermes config set multiplex_profiles true`, and
`hermes gateway restart`. It then confirms `hermes plugins list` shows the
router plugin enabled, `hermes config get multiplex_profiles` returns true, and
`hermes gateway status --deep` is healthy. If the first restart/status check
fails, the stage scrubs messaging `platforms:` blocks and Telegram-style env
keys from Scout, Scribe, Reach, and Dev, then retries the restart once. It writes
`~/agent-mission-control/reports/prompt16-multi-agent-activation-verification.{json,md}`.

Prompt 17 is a read-only routing audit. `gooros-hermes prompt17-audit` confirms
`multiplex_profiles: true` is top-level in Hermes config, the Telegram group ID
is present in `platforms.telegram.group_allowed_chats`, the topic router plugin
exists and is enabled, `topics.json` maps exactly `#scout`, `#scribe`, `#reach`,
and `#dev` to their real profiles while omitting `#command`, and every
specialist profile has `SOUL.md` without Telegram/platform blocks. It writes a
short channel -> thread ID -> profile table plus symptom guidance to
`~/agent-mission-control/reports/prompt17-telegram-routing-audit.{json,md}`.

Prompt 19 is a no-write Hermes data-source discovery step. `gooros-hermes
prompt19-discover` opens `~/.hermes/state.db` and `~/.hermes/kanban.db` with
`file:...?mode=ro` plus `PRAGMA query_only=1`, reads
`~/.hermes/gateway_state.json`, prints schema/timestamp samples, and creates no
project or report files. Use `--json` for automation.

Prompt 20 is implemented as the live Mission Control data layer in
`~/agent-mission-control/server.py` plus `index.html`. The server is Python
stdlib only, binds to `127.0.0.1:${PORT:-51763}`, serves `/api/state` with the
required health, sessions, VPS, fleet, model, routing, log, board, working-agent,
and cron sections, and streams the same snapshot through `/events` every three
seconds. Hermes `state.db` and `kanban.db` are opened only with
`file:...?mode=ro` and `PRAGMA query_only=1`; the only read-write database in
the dashboard layer is the project-owned `board.db`. On first run it creates the
Prompt 20 `tasks` table and seeds six owner-facing tasks, two in each status
column, with no agent/model/assignee ownership fields.

Prompts 23-30 complete the live dashboard workflow. `install_dashboard` backs
up any existing `server.py` and `index.html` before replacement, then saves the
final versioned build to `~/agent-mission-control/backups/`, using filenames like
`index_v1.2_YYYY-MM-DDThh-mm.html`, keeps the original design reference at
`/template`, and builds the live nav badge as `v1.2`. Overview and Agents hydrate
from `/api/state` plus `/events`; the agent drawer calls `/api/agent`; Tasks uses
`board.db` through create/update/delete endpoints; Chat backfills from Hermes
session DBs and `POST /api/chat/send` runs `hermes chat --resume` with argv-only
subprocess execution; Content reads/writes Markdown under
`~/agent-mission-control/content/<agent>/`; and Schedule parses
`~/.hermes/cron/jobs.json` then uses `POST /api/cron/action` to call
`hermes cron run|pause|resume|delete` after validating the job ID. Prompt 29 is
seeded into every agent's SOUL and long-term memory so long deliverables are
stored as Markdown files in the agent's own content folder; the installer also
creates a non-overwriting Scout research note in `content/scout/` when one is
not already present.

Prompts 31-37 tighten the production behavior. The Office tab keeps the original
Three.js scene and maps live `/api/state.working_agents` to blue active tower
glow and orange idle glow; `/api/chat/send` marks the selected agent as working
for the whole Hermes subprocess turn and strips local CLI metadata such as
`Working directory:` and `session_id:` from the dashboard stream. Prompt 33 is
implemented by `gooros-hermes tailscale-serve`, which keeps Mission Control
bound to `127.0.0.1` and exposes it only through Tailscale Serve, never Funnel.
Prompt 34 writes a two-tier routing policy: the strongest discovered free
9Router provider model is `premium`, while the round-robin combo remains `fast`
for short or formatting-oriented work.

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
gooros-hermes prompt15-plugin --yes --env-file ./customer.env
gooros-hermes prompt16-activate
gooros-hermes prompt17-audit --yes --env-file ./customer.env
gooros-hermes prompt19-discover --json
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
TARGET_TAG=v0.1.31
git rev-parse --verify "$TARGET_TAG" >/dev/null 2>&1 || git tag "$TARGET_TAG"
git push origin main --tags
```

Do not give customers an install command until the target commit is clean,
tagged, pushed, and the internal pilot checklist passes.
