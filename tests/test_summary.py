from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from tailwarden.models import (
    ActionCounts,
    ActionError,
    DeviceConnectivity,
    DeviceIssue,
    DeviceIssueCode,
    DeviceReference,
    DeviceStatus,
    KeepaliveOutcome,
    KeepaliveReport,
    KeyExpiryState,
)
from tailwarden.summary import render_markdown_summary, write_step_summary

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


def sample_report() -> KeepaliveReport:
    device = DeviceReference(
        device_id="device-1",
        name="srv|\\<b>`x\nnext",
        target="<script>alert(1)</script>",
    )
    return KeepaliveReport(
        outcome=KeepaliveOutcome.PARTIAL_FAILURE,
        tailnet="owner`name",
        server_tag="tag:server",
        rejoin_auth_key_tag="tag:server",
        stale_after_minutes=30,
        key_expiry_warning_hours=168,
        auth_key_expiry_seconds=3600,
        started_at=NOW,
        finished_at=NOW,
        total_devices=1,
        stale_devices=1,
        key_expiry_actions=ActionCounts(already=2, failed=1),
        issues=[
            DeviceIssue(
                code=DeviceIssueCode.STALE,
                device=device,
                detail="line1\nline2 | risky <html>",
                observed_at=NOW,
            )
        ],
        action_errors=[
            ActionError(
                action="disable|expiry",
                message="bad\nmessage <tag>",
                status_code=500,
                device=device,
                body="tskey-auth-must-not-appear",
            )
        ],
    )


def test_summary_escapes_markdown_html_backticks_and_newlines() -> None:
    summary = render_markdown_summary(sample_report())

    assert "srv\\|\\\\&lt;b&gt;&#96;x<br>next" in summary
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in summary
    assert "owner&#96;name" in summary
    assert "line1<br>line2 \\| risky &lt;html&gt;" in summary
    assert "disable\\|expiry" in summary
    assert "bad<br>message &lt;tag&gt;" in summary
    assert "tskey-auth-must-not-appear" not in summary


def test_summary_reports_already_planned_succeeded_and_failed_separately() -> None:
    summary = render_markdown_summary(sample_report())

    assert "key expiry already disabled: `2`" in summary
    assert "key expiry planned this run: `0`" in summary
    assert "key expiry disabled this run: `0`" in summary
    assert "key expiry action failures: `1`" in summary


def test_public_summary_redacts_identifiers_and_upstream_details() -> None:
    summary = render_markdown_summary(sample_report(), redact_details=True)

    for private_value in (
        "owner",
        "tag:server",
        "device-1",
        "srv",
        "script",
        "line1",
        "bad",
        "tskey-auth-must-not-appear",
    ):
        assert private_value not in summary
    assert "identifiers and per-device details: `redacted" in summary
    assert "details redacted from public output" in summary
    assert "key expiry action failures: `1`" in summary


def test_summary_recovery_command_uses_frozen_lockfile() -> None:
    summary = render_markdown_summary(sample_report())

    assert "uv run --frozen tailwarden recovery-key" in summary


def test_redacted_summary_hides_identifiers_and_device_details() -> None:
    summary = render_markdown_summary(sample_report(), redact_details=True)

    assert "owner&#96;name" not in summary
    assert "tag:server" not in summary
    assert "srv\\|\\\\&lt;b&gt;&#96;x<br>next" not in summary
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" not in summary
    assert "redacted for GitHub/public logs" in summary
    assert "disable\\|expiry" in summary


def test_step_summary_is_appended_instead_of_overwritten(tmp_path: Path) -> None:
    destination = tmp_path / "summary.md"
    destination.write_text("existing section\n", encoding="utf-8")

    write_step_summary(sample_report(), str(destination))

    content = destination.read_text(encoding="utf-8")
    assert content.startswith("existing section\n")
    assert "## Tailwarden" in content


def test_device_table_reports_control_plane_and_key_maintenance_states(tmp_path: Path) -> None:
    report = sample_report().model_copy(
        update={
            "total_devices": 4,
            "device_statuses": [
                DeviceStatus(
                    machine_name="srv-ok",
                    connectivity=DeviceConnectivity.SEEN_RECENTLY,
                    key_expiry=KeyExpiryState.ALREADY_DISABLED,
                ),
                DeviceStatus(
                    machine_name="srv-off",
                    connectivity=DeviceConnectivity.OFFLINE,
                    key_expiry=KeyExpiryState.DISABLED_THIS_RUN,
                ),
                DeviceStatus(
                    machine_name="srv-stale",
                    connectivity=DeviceConnectivity.STALE,
                    key_expiry=KeyExpiryState.PLANNED,
                ),
                DeviceStatus(
                    machine_name=None,
                    connectivity=DeviceConnectivity.UNKNOWN_LAST_SEEN,
                    key_expiry=KeyExpiryState.FAILED,
                ),
            ],
        }
    )
    destination = tmp_path / "summary.md"
    write_step_summary(report, str(destination), redact_details=True, include_device_names=True)
    summary = destination.read_text(encoding="utf-8")

    assert "does not ping devices" in summary
    assert "| `srv-ok` | seen within 30 min | already disabled |" in summary
    assert "| `srv-off` | offline (online=false) | disabled this run |" in summary
    assert "| `srv-stale` | stale (lastSeen older than 30 min) | planned (dry-run) |" in summary
    assert "| (name unavailable) | unknown (lastSeen unavailable) | disable failed |" in summary
    assert "machine names: shown (REPORT_DEVICE_NAMES)" in summary
    assert "per-device timestamps and upstream error details: redacted" in summary
    assert "identifiers and per-device details:" not in summary
    assert NOW.isoformat() in summary
    for private_value in ("device-1", "tag:server", "owner", "script", "bad", "must-not-appear"):
        assert private_value not in summary


@pytest.mark.parametrize(
    "name",
    [
        None,
        "",
        "[x](https://evil)",
        "@admin",
        "<img src=x>",
        "a|b",
        "a\nb",
        "a`b",
        "a" * 64,
        "srv.tailnet.ts.net",
        "srv\n",
        "srv\r",
        "srv\t",
        "srv\x00",
        "é",
        "-srv",
        "srv-",
    ],
)
def test_device_table_revalidates_names_before_rendering(name: str | None) -> None:
    report = sample_report().model_copy(
        update={
            "device_statuses": [
                DeviceStatus(
                    machine_name=name,
                    connectivity=DeviceConnectivity.SEEN_RECENTLY,
                    key_expiry=KeyExpiryState.ALREADY_DISABLED,
                )
            ],
        }
    )
    summary = render_markdown_summary(report, redact_details=True, include_device_names=True)
    row = next(line for line in summary.splitlines() if line.startswith("| (name unavailable)"))
    assert row == "| (name unavailable) | seen within 30 min | already disabled |"
    if name:
        assert name not in summary


@pytest.mark.parametrize("name", ["a", "A9", "srv-01", "a" * 63])
def test_device_table_formats_valid_dns_labels_as_code(name: str) -> None:
    report = sample_report().model_copy(
        update={
            "device_statuses": [
                DeviceStatus(
                    machine_name=name,
                    connectivity=DeviceConnectivity.SEEN_RECENTLY,
                    key_expiry=KeyExpiryState.ALREADY_DISABLED,
                )
            ],
        }
    )
    summary = render_markdown_summary(report, redact_details=True, include_device_names=True)
    assert f"| `{name}` | seen within 30 min | already disabled |" in summary


def test_device_table_explains_zero_matches_without_disclosing_selector() -> None:
    report = sample_report().model_copy(update={"total_devices": 0, "device_statuses": []})
    summary = render_markdown_summary(report, redact_details=True, include_device_names=True)

    assert "No devices matched the configured selector (0 matches)." in summary
    assert "| Machine |" not in summary
    assert "tag:server" not in summary


@pytest.mark.parametrize(
    ("redact_details", "expected"),
    [
        (
            False,
            (
                "## Tailwarden\n"
                "\n"
                "- outcome: <code>partial_failure</code>\n"
                "- tailnet: <code>owner&#96;name</code>\n"
                "- server selector: <code>tag:server</code>\n"
                "- rejoin auth key tag: <code>tag:server</code>\n"
                "- stale threshold: `30 minutes`\n"
                "- key-expiry warning window: `168 hours`\n"
                "- run mode: `apply`\n"
                "- servers checked: `1`\n"
                "- key expiry already disabled: `2`\n"
                "- key expiry planned this run: `0`\n"
                "- key expiry disabled this run: `0`\n"
                "- key expiry action failures: `1`\n"
                "- devices expiring inside warning window: `0`\n"
                "- stale or offline devices: `1`\n"
                "- devices with unknown lastSeen: `0`\n"
                "- started at: `2026-09-02T12:00:00+00:00`\n"
                "- finished at: `2026-09-02T12:00:00+00:00`\n"
                "\n"
                "### Stale Or Offline\n"
                "\n"
                "| Device | Target | Detail | Observed at |\n"
                "| --- | --- | --- | --- |\n"
                "| srv\\|\\\\&lt;b&gt;&#96;x<br>next | &lt;script&gt;alert(1)&lt;/script&gt; | "
                "line1<br>line2 \\| risky &lt;html&gt; | 2026-09-02T12:00:00+00:00 |\n"
                "\n"
                "### Tailscale API Action Errors\n"
                "\n"
                "| Device | Action | HTTP | Message |\n"
                "| --- | --- | --- | --- |\n"
                "| srv\\|\\\\&lt;b&gt;&#96;x<br>next | disable\\|expiry | 500 | "
                "bad<br>message &lt;tag&gt; |\n"
                "\n"
                "Recovery requires an explicit operator action.\n"
                "Use `uv run --frozen tailwarden recovery-key` in a controlled local terminal.\n"
            ),
        ),
        (
            True,
            (
                "## Tailwarden\n"
                "\n"
                "- outcome: <code>partial_failure</code>\n"
                "- stale threshold: `30 minutes`\n"
                "- key-expiry warning window: `168 hours`\n"
                "- run mode: `apply`\n"
                "- servers checked: `1`\n"
                "- key expiry already disabled: `2`\n"
                "- key expiry planned this run: `0`\n"
                "- key expiry disabled this run: `0`\n"
                "- key expiry action failures: `1`\n"
                "- devices expiring inside warning window: `0`\n"
                "- stale or offline devices: `1`\n"
                "- devices with unknown lastSeen: `0`\n"
                "- started at: `2026-09-02T12:00:00+00:00`\n"
                "- finished at: `2026-09-02T12:00:00+00:00`\n"
                "\n"
                "- identifiers and per-device details: `redacted for GitHub/public logs`\n"
                "\n"
                "### Tailscale API Action Errors\n"
                "\n"
                "| Action | HTTP | Message |\n"
                "| --- | --- | --- |\n"
                "| disable\\|expiry | 500 | details redacted from public output |\n"
                "\n"
                "Recovery requires an explicit operator action.\n"
                "Use `uv run --frozen tailwarden recovery-key` in a controlled local terminal.\n"
                "Run the same command locally for per-device detail; "
                "GitHub/public logs stay redacted.\n"
            ),
        ),
    ],
)
def test_names_disabled_preserves_existing_summary_bytes(
    redact_details: bool,
    expected: str,
) -> None:
    report = sample_report()
    report.device_statuses = [
        DeviceStatus(
            machine_name="summary-only",
            connectivity=DeviceConnectivity.OFFLINE,
            key_expiry=KeyExpiryState.FAILED,
        )
    ]
    assert render_markdown_summary(report, redact_details=redact_details) == expected
    assert (
        render_markdown_summary(
            report,
            redact_details=redact_details,
            include_device_names=False,
        )
        == expected
    )
