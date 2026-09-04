from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from tailwarden.models import (
    ActionCounts,
    ActionError,
    DeviceIssue,
    DeviceIssueCode,
    DeviceReference,
    KeepaliveOutcome,
    KeepaliveReport,
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
