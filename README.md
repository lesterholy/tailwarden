# Tailwarden

English | [中文](README.zh-CN.md)

`tailwarden` uses Python 3.12, FastAPI, and `aiohttp` to inspect Tailscale devices and
disable device key expiry for selected server nodes. Scheduling and automation are
maintained outside this repository. A keepalive pass runs through the CLI; it does not
require a standalone FastAPI deployment.

## What it does

- Selects devices by `SERVER_TAG` and reports stale or offline nodes.
- Disables device key expiry for matched server nodes.
- Returns `no matches` when the selector finds no devices.
- Creates a recovery auth key only through the explicit `recovery-key` command.

## Local use

Install `uv` and Python 3.12, then copy `.env.example` to `.env` and fill in the
settings needed for your run. For a short local session, `TS_TOKEN` can hold a
short-lived API access token from Tailscale **Settings > Keys**. Keep real credentials
out of the repository.

```bash
uv sync --frozen
uv run --frozen tailwarden run
```

Use `uv run --frozen tailwarden recovery-key` only in a controlled local terminal.
Treat the returned key as sensitive. The normal `run` command never creates one.
The full environment-variable contract is in `.env.example`.

## Runtime behavior

`FAIL_ON_STALE` defaults to `true`. `run --fail-on-stale` and
`run --no-fail-on-stale` override it for one pass. Stale or offline devices return
exit code `2` when the policy is enabled; operation errors return `4`, configuration
errors return `64`, and a report without a failing condition returns `0`.

The application retains optional GitHub Actions runtime support. In that runtime,
complete GitHub OIDC settings are required and authentication never falls back to
`TS_TOKEN`. When `GITHUB_STEP_SUMMARY` is available, the application can write a Step
Summary. `REPORT_REDACT_DETAILS` controls local output redaction; GitHub Actions
runtime forces it. `REPORT_DEVICE_NAMES` controls whether short device names appear
in the Step Summary. No workflow in this repository configures or schedules a run.

## Development checks

```bash
uv sync --frozen --group dev
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen pytest
```

Use non-frozen `uv lock` or `uv sync` only when intentionally updating dependencies,
and commit the resulting `uv.lock` change. Dependabot continues to track `uv`
dependencies.

Report security issues privately as described in [the security policy](.github/SECURITY.md).
See [docs/tailwarden.md](docs/tailwarden.md) for operations and recovery guidance.

Released under the [MIT License](LICENSE).
