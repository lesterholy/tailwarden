# Tailwarden

English | [中文](README.zh-CN.md)

Use **Grok Bot** to keep Tailscale-tagged machines healthy: scheduled checks, stale/offline reports, optional device key-expiry disable, and rejoin when this bot’s own box drops offline.

This repo is a Python CLI (`tailwarden`). Prefer a dedicated Grok Bot agent for scheduling and day-to-day ops—not an in-repo GitHub Actions run workflow (this repository no longer ships one).

## What it does / does not do

**Does:**

- Select devices by `SERVER_TAG` (default `tag:tailwarden-managed`)
- Report `online=false`, nodes unseen longer than `STALE_AFTER_MINUTES`, or missing `lastSeen`
- Disable device key expiry on matched nodes (or only plan actions in dry-run)
- Create a one-shot rejoin auth key **only** via the explicit `recovery-key` command

**Does not:**

- `run` **never** creates a recovery auth key (that is `recovery-key`)
- Log into the Tailscale admin console for you; when a browser login is required, Grok Bot should pause and ask you
- Auto-delete old offline nodes; after a box rebuild you (or the bot, after you confirm) handle that in the admin UI

Field-level defaults live in [`.env.example`](.env.example) and [`docs/tailwarden.md`](docs/tailwarden.md).

## Complete Grok Bot operator flow

### 1. Create a dedicated agent

![01 Create a dedicated Grok Bot agent](docs/images/01-create-tailwarden-agent.png)

Create or open a Grok Bot agent dedicated to Tailwarden (for example named `tailwarden`). Keep checks, recovery, and token-expiry alerts in that agent so they stay separate from unrelated chats.

### 2. Point it at this GitHub repo

![02 Point the agent at the GitHub repo](docs/images/02-point-at-github-repo.png)

Point the agent at [`lesterholy/tailwarden`](https://github.com/lesterholy/tailwarden). Grok Bot can read the tree through the GitHub connector, then `git clone` / `git pull` on the box and run the CLI.

### 3. Auth: `TS_TOKEN` (never paste the token into chat)

![03 Create an API access token in Tailscale admin](docs/images/03-tailscale-api-access-token.png)

![04 Provide TS_TOKEN via Grok Bot secure secret input](docs/images/04-grok-bot-secure-secret-input.png)

For **Grok Bot / local CLI**, use an **API access token** from Tailscale admin **Settings > Keys**, supplied as `TS_TOKEN`.

Inject it with Grok Bot’s **secure secret** input so the token never appears in chat. Tokens last up to about 90 days; on 401/403 the bot should alert you to rotate.

> **Brief note for GitHub Actions users:**  
> The code still supports an optional GitHub OIDC path (`TS_CLIENT_ID` / `TS_AUDIENCE`, plus Actions-injected `ACTIONS_ID_TOKEN_REQUEST_*`). That path only works inside GitHub Actions; with `GITHUB_ACTIONS=true` authentication never falls back to `TS_TOKEN`, and `recovery-key` is disabled. This repo no longer ships a scheduling workflow—if you run Actions yourself, configure OIDC and Trust credentials on your side.

### 4. Recommended env for Grok Bot scheduled runs

| Variable | Suggested value | Notes |
|----------|-----------------|-------|
| `TAILNET` | `-` | Credential’s default tailnet |
| `SERVER_TAG` | `tag:tailwarden-managed` | Only devices with this tag |
| `REJOIN_AUTH_KEY_TAG` | `tag:tailwarden-managed` | Tag on keys from `recovery-key` (must be a concrete `tag:…`) |
| `STALE_AFTER_MINUTES` | `30` | Unseen longer than this → stale |
| `KEY_EXPIRY_WARNING_HOURS` | `168` | Keys expiring within this window are flagged |
| `AUTH_KEY_EXPIRY_SECONDS` | `3600` | Recovery key lifetime (seconds) |
| `FAIL_ON_STALE` | `false` | Scheduled bot runs: still report stale nodes, but do not treat exit code 2 as a hard failure |
| `REPORT_REDACT_DETAILS` | `true` | Hide device detail in chat attachments |
| `REPORT_DEVICE_NAMES` | `true` | Keep short names in the summary for a quick scan |
| `DRY_RUN` | `true` first, then `false` | See next section |

> **Note:** In code / `.env.example`, `FAIL_ON_STALE` defaults to `true`, and `REPORT_REDACT_DETAILS` / `REPORT_DEVICE_NAMES` default to `false`. The table above is the **Grok Bot schedule** recommendation, not hard-coded library defaults. Per-run CLI overrides: `run --no-fail-on-stale`, `run --dry-run`, and so on.

On the box:

```bash
uv sync --frozen
```

### 5. First run: dry-run, then a real `run`

![05 Dry-run report summary](docs/images/05-first-dry-run-report.png)

![06 Live run chat summary and attachment](docs/images/06-live-run-chat-summary.png)

```bash
# Plan only — do not change key expiry
DRY_RUN=true uv run --frozen tailwarden run
# or
uv run --frozen tailwarden run --dry-run
```

When the matched set and planned actions look right:

```bash
uv run --frozen tailwarden run
# For scheduled bots, prefer not failing the process on stale:
uv run --frozen tailwarden run --no-fail-on-stale
```

**What `run` does:**

1. List tailnet devices and filter by `SERVER_TAG`  
2. Count stale / offline / unknown `lastSeen` / soon-expiring keys  
3. For matched nodes that still have key expiry enabled: record “planned” in dry-run, otherwise call the API to disable  
4. Print a JSON report (optionally redacted); also write a Step Summary when `GITHUB_STEP_SUMMARY` is set  

**What `run` does not do:** create a recovery auth key, restart Tailscale, delete nodes, or edit ACLs.

Exit codes (for the bot): `0` OK; `2` stale/offline when `FAIL_ON_STALE` is on; `4` operation error; `64` configuration error.

### 6. When the Grok Bot box itself is stale

![07 Box shown offline or stale in admin](docs/images/07-box-stale-in-admin.png)

![08 recovery-key then tailscale up](docs/images/08-recovery-key-and-tailscale-up.png)

Hostnames often look like `grok-bot-vm-…`. Common cause: a rebuild wiped Tailscale state; PID 1 is `tini`, so there is **no systemd**—`systemctl restart tailscaled` will not help.

Suggested sequence (bot executes; ask you before sensitive steps):

1. Check `tailscale status` / whether the daemon is running; reinstall or restart Tailscale if needed  
2. When a fresh join is required, run `uv run --frozen tailwarden recovery-key` in a controlled local terminal and treat the one-shot auth key as secret (do not paste the full key into chat or public logs)  
3. Delete the old offline node with the same name in the admin console to avoid conflicts  
4. Join, for example:  
   `tailscale up --accept-dns --hostname=<desired-hostname> --auth-key=<recovery-key>`  
5. If ACLs need `tag:server`, restore tags to **`tag:server` + `tag:tailwarden-managed`** in the admin UI (`recovery-key` only attaches `REJOIN_AUTH_KEY_TAG`, usually the latter)  
6. Expect the IP may change; update anything that pinned the old address  

`recovery-key` creates a preauthorized, non-reusable auth key tagged with `REJOIN_AUTH_KEY_TAG` and expiry `AUTH_KEY_EXPIRY_SECONDS`. Discard after use; prefer short-lived API access tokens as well.

### 7. Schedule: twice daily via Grok Bot, not GitHub cron

![09 Grok Bot routine at 08:00 and 20:00 Asia/Shanghai](docs/images/09-routine-schedule-asia-shanghai.png)

Use a Grok Bot **routine** at **08:00** and **20:00 Asia/Shanghai** (instead of any former in-repo GitHub Actions cron). Each run should:

- Execute `tailwarden run` with the env from above  
- Post a chat summary plus a report attachment  
- If the box Tailscale is offline, try auto-restart / reinstall and recover per section 6 before the check  

### 8. Token expiry and 401/403

API access tokens last up to ~90 days. On **401 / 403**, the bot should clearly warn that `TS_TOKEN` may be expired or under-scoped, and guide you to rotate via secure secret input—never paste the new token into the conversation.

### 9. When a browser admin login is required: pause

![10 Bot pauses for admin browser login](docs/images/10-pause-for-admin-browser-login.png)

Deleting old nodes, editing tags, or confirming the device list sometimes requires the Tailscale admin console. Grok Bot should **stop**, ask you to finish login in the browser (desktop handoff or your own session), then continue.

## Command cheat sheet

```bash
uv sync --frozen
uv run --frozen tailwarden run --dry-run
uv run --frozen tailwarden run --no-fail-on-stale
uv run --frozen tailwarden recovery-key   # controlled terminal only; treat output as secret
```

Dev checks:

```bash
uv sync --frozen --group dev
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen pytest
```

## Security

- Never paste `TS_TOKEN`, recovery keys, OIDC JWTs, or full device IDs into public issues, PRs, or chat  
- On exposure, revoke at the issuer first, then rotate related credentials  
- Report vulnerabilities privately per the [security policy](.github/SECURITY.md)  

## License

[MIT License](LICENSE)
