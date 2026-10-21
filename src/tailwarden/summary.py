from __future__ import annotations

import html
import re
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from .models import (
    ActionError,
    DeviceConnectivity,
    DeviceIssue,
    DeviceIssueCode,
    KeepaliveReport,
    KeyExpiryState,
)


def _escape(value: object | None) -> str:
    if value is None:
        return "unknown"
    text = html.escape(str(value), quote=True)
    text = "".join(
        character
        if ord(character) >= 32 or character in {"\r", "\n"}
        else f"&#x{ord(character):02x};"
        for character in text
    )
    text = text.replace("\\", "\\\\").replace("`", "&#96;").replace("|", "\\|")
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")
    return text or "unknown"


def _format_timestamp(value: datetime | None) -> str:
    return "unknown" if value is None else value.isoformat()


def _issues_with_codes(
    issues: Iterable[DeviceIssue],
    *codes: DeviceIssueCode,
) -> list[DeviceIssue]:
    allowed = set(codes)
    return [issue for issue in issues if issue.code in allowed]


def _render_issue_table(title: str, issues: list[DeviceIssue]) -> list[str]:
    if not issues:
        return []
    lines = [
        f"### {title}",
        "",
        "| Device | Target | Detail | Observed at |",
        "| --- | --- | --- | --- |",
    ]
    for issue in issues:
        lines.append(
            f"| {_escape(issue.device.name)} | {_escape(issue.device.target)} | "
            f"{_escape(issue.detail)} | {_escape(_format_timestamp(issue.observed_at))} |"
        )
    lines.append("")
    return lines


def _render_action_errors(errors: list[ActionError], *, redact_details: bool) -> list[str]:
    if not errors:
        return []
    lines = ["### Tailscale API Action Errors", ""]
    if redact_details:
        lines.extend(
            [
                "| Action | HTTP | Message |",
                "| --- | --- | --- |",
            ]
        )
    else:
        lines.extend(
            [
                "| Device | Action | HTTP | Message |",
                "| --- | --- | --- | --- |",
            ]
        )
    for error in errors:
        if redact_details:
            lines.append(
                f"| {_escape(error.action)} | {_escape(error.status_code)} | "
                "details redacted from public output |"
            )
        else:
            lines.append(
                f"| {_escape(error.device.name if error.device else None)} | "
                f"{_escape(error.action)} | {_escape(error.status_code)} | "
                f"{_escape(error.message)} |"
            )
    lines.append("")
    return lines


def _public_machine_name(value: str | None) -> str:
    if value is None or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", value):
        return "(name unavailable)"
    return f"`{value}`"


def _render_device_table(report: KeepaliveReport) -> list[str]:
    lines = [
        "### Devices",
        "",
        "Status reflects Tailscale API online/lastSeen; Tailwarden does not ping devices.",
        "",
    ]
    if report.total_devices == 0:
        return lines + ["No devices matched the configured selector (0 matches).", ""]

    connectivity_labels = {
        DeviceConnectivity.SEEN_RECENTLY: f"seen within {report.stale_after_minutes} min",
        DeviceConnectivity.OFFLINE: "offline (online=false)",
        DeviceConnectivity.STALE: f"stale (lastSeen older than {report.stale_after_minutes} min)",
        DeviceConnectivity.UNKNOWN_LAST_SEEN: "unknown (lastSeen unavailable)",
    }
    key_expiry_labels = {
        KeyExpiryState.ALREADY_DISABLED: "already disabled",
        KeyExpiryState.DISABLED_THIS_RUN: "disabled this run",
        KeyExpiryState.PLANNED: "planned (dry-run)",
        KeyExpiryState.FAILED: "disable failed",
    }
    lines.extend(["| Machine | Control-plane status | Key expiry |", "| --- | --- | --- |"])
    for device in report.device_statuses:
        lines.append(
            f"| {_public_machine_name(device.machine_name)} | "
            f"{connectivity_labels[device.connectivity]} | {key_expiry_labels[device.key_expiry]} |"
        )
    return lines + [""]


def render_markdown_summary(
    report: KeepaliveReport,
    *,
    redact_details: bool = False,
    include_device_names: bool = False,
) -> str:
    lines = [
        "## Tailwarden",
        "",
        f"- outcome: <code>{_escape(report.outcome.value)}</code>",
        f"- stale threshold: `{report.stale_after_minutes} minutes`",
        f"- key-expiry warning window: `{report.key_expiry_warning_hours} hours`",
        f"- run mode: `{'dry-run' if report.dry_run else 'apply'}`",
        f"- servers checked: `{report.total_devices}`",
        f"- key expiry already disabled: `{report.key_expiry_actions.already}`",
        f"- key expiry planned this run: `{report.key_expiry_actions.planned}`",
        f"- key expiry disabled this run: `{report.key_expiry_actions.succeeded}`",
        f"- key expiry action failures: `{report.key_expiry_actions.failed}`",
        f"- devices expiring inside warning window: `{report.expiring_devices}`",
        f"- stale or offline devices: `{report.stale_devices}`",
        f"- devices with unknown lastSeen: `{report.unknown_last_seen_devices}`",
        f"- started at: `{_escape(_format_timestamp(report.started_at))}`",
        f"- finished at: `{_escape(_format_timestamp(report.finished_at))}`",
        "",
    ]
    if redact_details:
        lines.extend(
            [
                (
                    "- machine names: shown (REPORT_DEVICE_NAMES); device IDs, addresses, "
                    "tailnet, tags, per-device timestamps and upstream error details: redacted"
                    if include_device_names
                    else "- identifiers and per-device details: `redacted for GitHub/public logs`"
                ),
                "",
            ]
        )
    else:
        lines[3:3] = [
            f"- tailnet: <code>{_escape(report.tailnet)}</code>",
            f"- server selector: <code>{_escape(report.server_tag)}</code>",
            f"- rejoin auth key tag: <code>{_escape(report.rejoin_auth_key_tag)}</code>",
        ]
        lines.extend(
            _render_issue_table(
                "Expiring Soon Before Fix",
                _issues_with_codes(report.issues, DeviceIssueCode.KEY_EXPIRING),
            )
        )
        lines.extend(
            _render_issue_table(
                "Stale Or Offline",
                _issues_with_codes(
                    report.issues,
                    DeviceIssueCode.OFFLINE,
                    DeviceIssueCode.STALE,
                ),
            )
        )
        lines.extend(
            _render_issue_table(
                "Unknown Last Seen",
                _issues_with_codes(report.issues, DeviceIssueCode.UNKNOWN_LAST_SEEN),
            )
        )
    if include_device_names:
        lines.extend(_render_device_table(report))
    lines.extend(_render_action_errors(report.action_errors, redact_details=redact_details))

    if report.stale_devices:
        lines.extend(
            [
                "Recovery requires an explicit operator action.",
                "Use `uv run --frozen tailwarden recovery-key` in a controlled local terminal.",
                (
                    "Run the same command locally for per-device detail; "
                    + (
                        "the public device table shows only machine short names and fixed states."
                        if include_device_names
                        else "GitHub/public logs stay redacted."
                    )
                    if redact_details
                    else None
                ),
                "",
            ]
        )
    return "\n".join(line for line in lines if line is not None).rstrip() + "\n"


def write_step_summary(
    report: KeepaliveReport,
    destination: str | None,
    *,
    redact_details: bool = False,
    include_device_names: bool = False,
) -> None:
    if not destination:
        return
    path = Path(destination)
    needs_separator = path.exists() and path.stat().st_size > 0
    with path.open("a", encoding="utf-8") as summary_file:
        if needs_separator:
            summary_file.write("\n")
        summary_file.write(
            render_markdown_summary(
                report,
                redact_details=redact_details,
                include_device_names=include_device_names,
            )
        )


__all__ = ["render_markdown_summary", "write_step_summary"]
