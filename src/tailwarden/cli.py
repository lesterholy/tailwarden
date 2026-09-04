from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence

import uvicorn
from pydantic import ValidationError

from .api import create_app
from .config import Settings
from .errors import (
    ConfigurationError,
    RunInProgressError,
    UpstreamError,
    UpstreamHTTPError,
    UpstreamPayloadError,
)
from .models import KeepaliveReport, RecoveryKeyResponse
from .runtime import AppRuntime
from .summary import write_step_summary

EXIT_OK = 0
EXIT_STALE = 2
EXIT_OPERATION_ERROR = 4
EXIT_CONFIGURATION_ERROR = 64


def _should_redact_output(settings: Settings) -> bool:
    return settings.github_actions or settings.report_redact_details


def _should_redact_startup_error(settings: Settings | None) -> bool:
    if settings is not None:
        return _should_redact_output(settings)
    return os.environ.get("GITHUB_ACTIONS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _operation_error_message(exc: Exception, *, redact_details: bool) -> str:
    if not redact_details:
        return str(exc)
    if isinstance(exc, (ConfigurationError, ValidationError)):
        return "configuration is invalid or incomplete"
    if isinstance(exc, UpstreamHTTPError):
        return f"{exc.service} request failed with HTTP {exc.status_code}"
    if isinstance(exc, UpstreamPayloadError):
        return f"{exc.service} returned an invalid response"
    if isinstance(exc, UpstreamError):
        return "upstream request failed"
    if isinstance(exc, OSError):
        return "local output operation failed"
    return str(exc)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tailwarden")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="run one keepalive pass")
    run_parser.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="override DRY_RUN for this pass",
    )
    run_parser.add_argument(
        "--fail-on-stale",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="override FAIL_ON_STALE for this pass",
    )

    subparsers.add_parser(
        "recovery-key",
        help="create and display one explicit recovery key",
    )
    subparsers.add_parser("serve", help="start the single-worker FastAPI server")
    return parser


def _render_console_report(report: KeepaliveReport, *, redact_details: bool) -> str:
    if not redact_details:
        return report.model_dump_json(indent=2) + "\n"

    redacted = {
        "outcome": report.outcome.value,
        "dry_run": report.dry_run,
        "fail_on_stale": report.fail_on_stale,
        "stale_after_minutes": report.stale_after_minutes,
        "key_expiry_warning_hours": report.key_expiry_warning_hours,
        "auth_key_expiry_seconds": report.auth_key_expiry_seconds,
        "started_at": report.started_at.isoformat(),
        "finished_at": report.finished_at.isoformat(),
        "total_devices": report.total_devices,
        "stale_devices": report.stale_devices,
        "unknown_last_seen_devices": report.unknown_last_seen_devices,
        "expiring_devices": report.expiring_devices,
        "key_expiry_actions": report.key_expiry_actions.model_dump(mode="json"),
        "action_errors": [
            {
                "action": error.action,
                "message": "details redacted from public output",
                "status_code": error.status_code,
            }
            for error in report.action_errors
        ],
        "details_redacted": True,
    }
    return json.dumps(redacted, indent=2) + "\n"


async def run_keepalive_command(
    settings: Settings,
    *,
    dry_run: bool | None = None,
    fail_on_stale: bool | None = None,
) -> int:
    runtime = AppRuntime(settings)
    await runtime.start()
    try:
        report = await runtime.run_keepalive(
            dry_run=dry_run,
            fail_on_stale=fail_on_stale,
        )
    finally:
        await runtime.close()

    redact_details = _should_redact_output(settings)
    write_step_summary(
        report,
        settings.github_step_summary,
        redact_details=redact_details,
    )
    sys.stdout.write(_render_console_report(report, redact_details=redact_details))
    if report.action_errors:
        return EXIT_OPERATION_ERROR
    if report.stale_devices and report.fail_on_stale:
        return EXIT_STALE
    return EXIT_OK


async def run_recovery_key_command(settings: Settings) -> int:
    if settings.github_actions:
        raise ConfigurationError(
            "recovery-key is disabled in GitHub Actions; use a controlled local terminal"
        )

    runtime = AppRuntime(settings)
    await runtime.start()
    try:
        recovery_key = await runtime.create_recovery_key()
    finally:
        await runtime.close()

    response = RecoveryKeyResponse.from_recovery_key(recovery_key)
    sys.stdout.write(response.model_dump_json(indent=2) + "\n")
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings: Settings | None = None
    try:
        settings = Settings()
        if args.command == "run":
            return asyncio.run(
                run_keepalive_command(
                    settings,
                    dry_run=args.dry_run,
                    fail_on_stale=args.fail_on_stale,
                )
            )
        if args.command == "recovery-key":
            return asyncio.run(run_recovery_key_command(settings))
        if args.command == "serve":
            uvicorn.run(
                create_app(settings),
                host=settings.app_host,
                port=settings.app_port,
                log_level=settings.app_log_level.lower(),
                workers=1,
            )
            return EXIT_OK
    except (ConfigurationError, ValidationError) as exc:
        print(
            _operation_error_message(
                exc,
                redact_details=_should_redact_startup_error(settings),
            ),
            file=sys.stderr,
        )
        return EXIT_CONFIGURATION_ERROR
    except (RunInProgressError, UpstreamError, OSError) as exc:
        print(
            _operation_error_message(
                exc,
                redact_details=settings is not None and _should_redact_output(settings),
            ),
            file=sys.stderr,
        )
        return EXIT_OPERATION_ERROR

    parser.print_help(sys.stderr)
    return EXIT_CONFIGURATION_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
