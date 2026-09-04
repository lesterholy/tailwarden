from __future__ import annotations

import html
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from .models import ActionError, DeviceIssue, DeviceIssueCode, KeepaliveReport


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


def render_markdown_summary(
    report: KeepaliveReport,
    *,
    redact_details: bool = False,
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
                "- identifiers and per-device details: `redacted for GitHub/public logs`",
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
    lines.extend(_render_action_errors(report.action_errors, redact_details=redact_details))

    if report.stale_devices:
        lines.extend(
            [
                "Recovery requires an explicit operator action.",
                "Use `uv run --frozen tailwarden recovery-key` in a controlled local terminal.",
                (
                    "Run the same command locally for per-device detail; "
                    "GitHub/public logs stay redacted."
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
) -> None:
    if not destination:
        return
    path = Path(destination)
    needs_separator = path.exists() and path.stat().st_size > 0
    with path.open("a", encoding="utf-8") as summary_file:
        if needs_separator:
            summary_file.write("\n")
        summary_file.write(render_markdown_summary(report, redact_details=redact_details))


__all__ = ["render_markdown_summary", "write_step_summary"]
