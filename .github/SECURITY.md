# Security Policy

## Reporting a vulnerability

Report suspected vulnerabilities privately with GitHub Private Vulnerability Reporting:

1. Open the repository **Security** tab.
2. Select **Advisories** and **Report a vulnerability**.
3. Describe the affected version, impact, and a minimal reproduction without including live
   credentials.

Do not open a public issue for a suspected vulnerability. Never paste a Tailscale access token,
GitHub OIDC JWT, recovery key, device identifier, or private Tailnet details into an issue, pull
request, Actions log, or artifact.

If private vulnerability reporting is unavailable, contact the repository owner privately and
share only the minimum information needed to establish a secure reporting channel.

## Supported versions

Security fixes are applied to the current default branch. Older commits and unmaintained forks are
not supported.

## Credential exposure

If a credential is exposed, revoke it at its issuer before opening any report. Delete the affected
Actions log or artifact where possible, rotate related credentials, and review recent Tailscale and
GitHub audit activity.
