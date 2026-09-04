# Tailwarden

English | [中文](README.zh-CN.md)

`tailwarden` keeps long-running Tailscale nodes healthy with Python 3.12, FastAPI, `aiohttp`,
GitHub Actions OIDC, and Tailscale workload identity federation. Production scheduling and
routine operations stay in GitHub Actions plus the CLI; no FastAPI deployment is required.

## What it does

- Selects devices by `SERVER_TAG`.
- Disables device key expiry for matched server nodes.
- Marks stale or offline nodes in a run report.
- Returns `no matches` without treating it as an execution failure.
- Creates a recovery auth key only through an explicit authorized action.

## Runtime model

- Production schedule: GitHub Actions only.
- Cron: `0 0,12 * * *`.
- Expected cadence: every 12 hours at about `00:00 UTC` and `12:00 UTC`.
- Actual start time can drift slightly because GitHub-hosted scheduling is not real-time.
- No standalone FastAPI deployment is required for production keepalive runs.

## Tech stack

- Python `3.12`
- FastAPI
- `aiohttp`
- `uv` for dependency management and command execution
- `.env.example` as the configuration contract

## Quick start

1. Install `uv` and Python `3.12`.
2. Copy `.env.example` to `.env` for local development.
3. Fill in the required variables.
4. Sync the locked dependencies with `uv sync --frozen`.
5. Run a local keepalive pass with `uv run --frozen tailwarden run`.

Use non-frozen `uv lock` or `uv sync` only when intentionally updating dependencies, and commit
the resulting `uv.lock` change.

## Configuration

Use `.env.example` as the source of truth for supported environment variables.

Store non-sensitive production configuration in GitHub `production` environment variables, not
in a committed `.env` file. GitHub Variables are not a secret store: public visitors cannot browse
their values directly in repository settings, but authorized repository collaborators can read
them through the GitHub API and GitHub does not automatically mask them in logs. Only store
configuration metadata that would be acceptable if accidentally disclosed.

Important variables and where they come from:

| App variable | Set in GitHub as | Purpose | Where to get or define it |
| --- | --- | --- | --- |
| `TS_CLIENT_ID` | `TS_MANAGER_CLIENT_ID` | Tailscale workload identity client ID used by the Python app | Copy the Client ID from Tailscale admin console **Settings > Trust credentials** after creating a GitHub Actions credential |
| `TS_AUDIENCE` | `TS_MANAGER_AUDIENCE` | Tailscale OIDC audience used by the Python app | Copy the Audience from the same Trust Credential; it is normally `api.tailscale.com/<client-id>` |
| `TAILNET` | `TAILNET` | Tailnet name or `-` for the default tailnet | Use your tailnet name from the Tailscale admin console, or keep `-` for the default tailnet |
| `SERVER_TAG` | `SERVER_TAG` | Device selector; GitHub Actions requires a concrete tag | Define a dedicated tag such as `tag:tailwarden-managed` in Tailscale **Access controls** and attach it only to target devices |
| `REJOIN_AUTH_KEY_TAG` | `REJOIN_AUTH_KEY_TAG` | Concrete tag applied to explicit recovery auth keys | Choose a concrete tag from the same Access controls policy; usually the same tag as `SERVER_TAG` |
| `STALE_AFTER_MINUTES` | `STALE_AFTER_MINUTES` | How long a node may stay unseen before it is reported stale | Operator-defined threshold |
| `KEY_EXPIRY_WARNING_HOURS` | `KEY_EXPIRY_WARNING_HOURS` | Warning window before key expiry | Operator-defined threshold |
| `AUTH_KEY_EXPIRY_SECONDS` | `AUTH_KEY_EXPIRY_SECONDS` | Lifetime of an explicit recovery auth key | Operator-defined threshold |

Create the Trust Credential with issuer `GitHub Actions`, subject
`repo:lesterholy/tailwarden:environment:production`, scope `devices:core`, and the tag used by
`SERVER_TAG`.

For least privilege, use a dedicated tag such as `tag:tailwarden-managed` when `tag:server` also
covers machines this automation should not manage. Keep the Trust Credential tag and `SERVER_TAG`
identical. Avoid `all`, `*`, and `tag:*` in production unless managing every matching device is an
explicit requirement.

`TS_MANAGER_CLIENT_ID`, `TS_MANAGER_AUDIENCE`, `TAILNET`, `SERVER_TAG`,
`REJOIN_AUTH_KEY_TAG`, and policy thresholds are configuration metadata and belong in Environment
Variables. `TS_TOKEN`, `APP_API_TOKEN`, OIDC JWTs, Tailscale access tokens, and every recovery/auth
key are secrets and must never be stored as Variables. The production workflow does not require
any of those secrets.

For GitHub environment values, use the environment-scoped form:

```bash
gh variable set --env production TS_MANAGER_CLIENT_ID --body "k1xxx..."
gh variable set --env production TS_MANAGER_AUDIENCE --body "api.tailscale.com/k1xxx..."
gh variable set --env production TAILNET --body "-"
gh variable set --env production SERVER_TAG --body "tag:tailwarden-managed"
gh variable set --env production REJOIN_AUTH_KEY_TAG --body "tag:tailwarden-managed"
gh variable set --env production STALE_AFTER_MINUTES --body "30"
gh variable set --env production KEY_EXPIRY_WARNING_HOURS --body "168"
gh variable set --env production AUTH_KEY_EXPIRY_SECONDS --body "3600"
```

The workflow maps `TS_MANAGER_CLIENT_ID -> TS_CLIENT_ID` and `TS_MANAGER_AUDIENCE -> TS_AUDIENCE` before invoking the app.

Store these values at **Repository Settings > Environments > production > Variables**.
`ACTIONS_ID_TOKEN_REQUEST_TOKEN`, `ACTIONS_ID_TOKEN_REQUEST_URL`, `GITHUB_STEP_SUMMARY`, and
`GITHUB_ACTIONS` are injected automatically by GitHub Actions; do not create them yourself.

`TS_TOKEN` is not used by the scheduled workflow. For local debugging, create a short-lived API
access token in Tailscale admin console **Settings > Keys**. `APP_HOST`, `APP_PORT`,
`APP_LOG_LEVEL`, and `APP_API_TOKEN` are also local-only optional API settings. If needed, generate
`APP_API_TOKEN` yourself, for example with `openssl rand -hex 32`.

## Authentication model

- Production GitHub Actions uses GitHub OIDC to obtain a short-lived Tailscale token.
- GitHub Actions requires complete OIDC configuration and never falls back to `TS_TOKEN`, even if
  one is accidentally provided.
- `TS_TOKEN` is only for short local debugging sessions and comes from Tailscale **Settings > Keys**.
- Do not use a long-lived Tailscale API key as the normal production path.

### OIDC configuration and token lifetime

- `TS_CLIENT_ID` and `TS_AUDIENCE` identify the Tailscale Trust Credential. They are not access
  secrets, normally do not expire on their own, and can remain in GitHub Environment variables.
- `ACTIONS_ID_TOKEN_REQUEST_TOKEN` and `ACTIONS_ID_TOKEN_REQUEST_URL` are injected automatically
  for each GitHub Actions job. Do not create them manually, store them as repository variables, or
  reuse them outside that job.
- The GitHub OIDC JWT is a short-lived credential issued at runtime. The Tailscale access token
  obtained from it is also short-lived. The application obtains and exchanges fresh credentials
  for each operation instead of persisting them.
- Production therefore stores the Client ID and Audience, not a long-lived Tailscale API token.

Although the Client ID and Audience do not normally expire with time, the existing configuration
stops working if the Tailscale Trust Credential is deleted, disabled, or recreated; the GitHub
repository or `production` environment is renamed and no longer matches the OIDC subject; or the
Audience, Issuer, `devices:core` scope, or allowed tags are changed. After any such change, verify
the values in Tailscale **Settings > Trust credentials** and update the GitHub environment values
when necessary.

## Public-repo logging posture

- GitHub Actions always forces redaction. Logs and Step Summary retain outcomes, counts, and generic
  errors while hiding tailnet, tags, device names, addresses, per-device times, and upstream bodies.
- Local CLI runs keep the full detailed JSON and detailed stderr errors unless you explicitly set `REPORT_REDACT_DETAILS=true` in your local `.env`.
- If you need device-by-device detail after a scheduled run, rerun locally in a controlled terminal instead of relaxing the GitHub redaction default.

## Commands

CLI commands:

- `uv run --frozen tailwarden run`
- `uv run --frozen tailwarden recovery-key` (controlled local terminal only)

## Recovery keys

- Recovery keys are never created during the normal 12-hour keepalive pass.
- Recovery keys are generated only by an explicit CLI action in a controlled local terminal.
- Treat every returned recovery key as sensitive.
- Never write recovery keys into logs, GitHub Step Summary output, or artifacts.
- When rejoining a node, keep that node's existing `tailscale up` flags, not just the auth key.

## GitHub Actions behavior

The workflow remains the production scheduler and enforcement point:

- Manual dispatch is supported.
- Scheduled runs stay at every 12 hours.
- A stale node can fail the workflow so GitHub notifications still work.
- API and payload errors should fail the run instead of being silently ignored.
- The workflow runs only from the repository default branch and rejects wildcard device selectors.
- Restrict the `production` environment to the default branch. Do not require reviewers when the
  scheduled job must remain unattended.
- Pull-request CI has no OIDC permission and does not read the `production` environment.
- CodeQL scans Python and GitHub Actions; dependency review blocks newly introduced high-severity
  vulnerabilities; Dependabot maintains both Actions and `uv` dependencies.

Before enabling the schedule, verify these repository settings manually:

- **Settings > Environments > production**: allow deployment only from the default branch.
- **Settings > Rules > Rulesets**: protect the default branch, require pull requests and passing
  security/CI checks, and block force pushes and branch deletion.
- **Settings > Code security and analysis**: enable Dependency graph, Dependabot alerts and security
  updates, Secret scanning, Push protection, and Private vulnerability reporting.
- **Settings > Actions > General**: keep default workflow permissions read-only and do not allow
  Actions to create or approve pull requests unless a future workflow explicitly requires it.

Report security issues privately through the repository Security tab as described in
[the security policy](.github/SECURITY.md); do not open a public issue containing credentials.

## More details

See [docs/tailwarden.md](docs/tailwarden.md) for setup, recovery, and operations guidance.

## License

Released under the [MIT License](LICENSE).
