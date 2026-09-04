from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

import tailwarden.cli as cli_module
from tailwarden.cli import (
    EXIT_CONFIGURATION_ERROR,
    EXIT_OK,
    EXIT_OPERATION_ERROR,
    EXIT_STALE,
    _render_console_report,
    build_parser,
    main,
    run_keepalive_command,
    run_recovery_key_command,
)
from tailwarden.config import Settings
from tailwarden.errors import ConfigurationError, UpstreamHTTPError
from tailwarden.models import (
    ActionError,
    DeviceIssue,
    DeviceIssueCode,
    DeviceReference,
    KeepaliveOutcome,
    KeepaliveReport,
)


def report(
    *,
    stale_devices: int = 0,
    fail_on_stale: bool = True,
    with_error: bool = False,
) -> KeepaliveReport:
    now = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
    return KeepaliveReport(
        outcome=(
            KeepaliveOutcome.PARTIAL_FAILURE
            if with_error
            else KeepaliveOutcome.STALE_DETECTED
            if stale_devices
            else KeepaliveOutcome.HEALTHY
        ),
        tailnet="-",
        server_tag="tag:server",
        rejoin_auth_key_tag="tag:server",
        fail_on_stale=fail_on_stale,
        stale_after_minutes=30,
        key_expiry_warning_hours=168,
        auth_key_expiry_seconds=3600,
        started_at=now,
        finished_at=now,
        stale_devices=stale_devices,
        action_errors=(
            [ActionError(action="disable-key-expiry", message="forbidden")] if with_error else []
        ),
    )


def test_run_parser_supports_boolean_optional_overrides() -> None:
    parsed = build_parser().parse_args(["run", "--dry-run", "--no-fail-on-stale"])
    assert parsed.dry_run is True
    assert parsed.fail_on_stale is False


def test_main_returns_configuration_exit_code_for_invalid_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STALE_AFTER_MINUTES", "0")
    assert main(["run"]) == EXIT_CONFIGURATION_ERROR


def test_main_redacts_validation_error_values_in_github_actions(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("SERVER_TAG", "tag:private environment")

    assert main(["run"]) == EXIT_CONFIGURATION_ERROR
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == "configuration is invalid or incomplete\n"
    assert "tag:private environment" not in captured.err


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "expected_exit"),
    [
        (report(), EXIT_OK),
        (report(stale_devices=1), EXIT_STALE),
        (report(stale_devices=1, fail_on_stale=False), EXIT_OK),
        (report(with_error=True), EXIT_OPERATION_ERROR),
    ],
)
async def test_run_command_maps_report_to_compatible_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    result: KeepaliveReport,
    expected_exit: int,
) -> None:
    class FakeRuntime:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        async def start(self) -> None:
            return None

        async def close(self) -> None:
            return None

        async def run_keepalive(self, **_: object) -> KeepaliveReport:
            return result

    monkeypatch.setattr(cli_module, "AppRuntime", FakeRuntime)
    settings = Settings(_env_file=None, TS_TOKEN="local-token")

    assert await run_keepalive_command(settings) == expected_exit


def test_console_report_redacts_github_log_details() -> None:
    rendered = _render_console_report(report(stale_devices=1, with_error=True), redact_details=True)

    assert '"details_redacted": true' in rendered
    assert '"tailnet"' not in rendered
    assert '"server_tag"' not in rendered
    assert '"rejoin_auth_key_tag"' not in rendered
    assert "forbidden" not in rendered
    assert "details redacted from public output" in rendered


@pytest.mark.asyncio
async def test_github_actions_forces_redacted_console_and_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    device = DeviceReference(
        device_id="private-device-id",
        name="private-hostname",
        target="100.64.0.10",
    )
    result = report(stale_devices=1, with_error=True).model_copy(
        update={
            "tailnet": "private-tailnet.example",
            "server_tag": "tag:private-server",
            "rejoin_auth_key_tag": "tag:private-rejoin",
            "issues": [
                DeviceIssue(
                    code=DeviceIssueCode.STALE,
                    device=device,
                    detail="private last-seen detail",
                    observed_at=datetime(2026, 9, 1, tzinfo=UTC),
                )
            ],
            "action_errors": [
                ActionError(
                    action="disable-key-expiry",
                    message="private upstream response",
                    status_code=403,
                    device=device,
                    body="private response body",
                )
            ],
        }
    )

    class FakeRuntime:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        async def start(self) -> None:
            return None

        async def close(self) -> None:
            return None

        async def run_keepalive(self, **_: object) -> KeepaliveReport:
            return result

    summary_path = tmp_path / "summary.md"
    monkeypatch.setattr(cli_module, "AppRuntime", FakeRuntime)
    settings = Settings(
        _env_file=None,
        GITHUB_ACTIONS=True,
        GITHUB_STEP_SUMMARY=str(summary_path),
        REPORT_REDACT_DETAILS=False,
        TS_TOKEN="must-not-be-used-by-real-runtime",
    )

    assert await run_keepalive_command(settings) == EXIT_OPERATION_ERROR
    public_output = capsys.readouterr().out + summary_path.read_text(encoding="utf-8")
    for private_value in (
        "private-tailnet.example",
        "tag:private-server",
        "tag:private-rejoin",
        "private-device-id",
        "private-hostname",
        "100.64.0.10",
        "private last-seen detail",
        "private upstream response",
        "private response body",
    ):
        assert private_value not in public_output
    assert '"details_redacted": true' in public_output
    assert "details redacted from public output" in public_output


def test_public_operation_error_omits_upstream_message_and_body() -> None:
    error = UpstreamHTTPError(
        service="Tailscale devices",
        status_code=403,
        message="private upstream message",
        body="private upstream body",
    )

    rendered = cli_module._operation_error_message(error, redact_details=True)

    assert rendered == "Tailscale devices request failed with HTTP 403"
    assert "private" not in rendered


def test_local_redaction_setting_also_redacts_early_operation_errors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fail_run_keepalive_command(
        settings: Settings,
        *,
        dry_run: bool | None = None,
        fail_on_stale: bool | None = None,
    ) -> int:
        raise UpstreamHTTPError(
            service="Tailscale devices",
            status_code=403,
            message="private upstream message",
            body="private upstream body",
        )

    monkeypatch.setattr(cli_module, "run_keepalive_command", fail_run_keepalive_command)
    monkeypatch.setenv("REPORT_REDACT_DETAILS", "true")
    monkeypatch.setenv("TS_TOKEN", "local-token")

    assert main(["run"]) == EXIT_OPERATION_ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == "Tailscale devices request failed with HTTP 403"
    assert "private" not in captured.err


@pytest.mark.asyncio
async def test_recovery_key_cli_is_disabled_in_github_actions() -> None:
    settings = Settings(_env_file=None, GITHUB_ACTIONS=True, TS_TOKEN="local-token")
    with pytest.raises(ConfigurationError):
        await run_recovery_key_command(settings)
