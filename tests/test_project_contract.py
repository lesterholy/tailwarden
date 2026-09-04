from __future__ import annotations

import re
from pathlib import Path

from tailwarden.config import Settings

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_project_targets_python_312_fastapi_aiohttp_and_uv() -> None:
    pyproject = read("pyproject.toml")
    assert 'requires-python = ">=3.12,<3.13"' in pyproject
    assert '"fastapi>=' in pyproject
    assert '"aiohttp>=' in pyproject
    assert 'tailwarden = "tailwarden.cli:main"' in pyproject
    assert "aioresponses" not in pyproject
    assert '"/scripts"' not in pyproject
    assert read(".python-version").strip() == "3.12"
    assert (ROOT / "uv.lock").is_file()


def test_tailwarden_rename_is_complete() -> None:
    old_distribution = "auto" + "-tail"
    old_module = "auto" + "_tail"
    old_class_prefix = "Auto" + "Tail"
    old_workflow = ".github/workflows/" + "tailscale" + "-keepalive.yml"
    old_operations = "docs/" + "tailscale" + "-keepalive.md"

    assert (ROOT / "src/tailwarden").is_dir()
    assert not (ROOT / "src" / old_module).exists()
    assert (ROOT / ".github/workflows/tailwarden.yml").is_file()
    assert not (ROOT / old_workflow).exists()
    assert (ROOT / "docs/tailwarden.md").is_file()
    assert not (ROOT / old_operations).exists()

    text_paths = [
        ROOT / ".env.example",
        ROOT / "LICENSE",
        ROOT / "README.md",
        ROOT / "README.zh-CN.md",
        ROOT / "pyproject.toml",
        ROOT / "uv.lock",
        *(ROOT / ".github").rglob("*.yml"),
        *(ROOT / ".github").rglob("*.md"),
        *(ROOT / "docs").rglob("*.md"),
        *(ROOT / "src").rglob("*.py"),
        *(ROOT / "tests").rglob("*.py"),
    ]
    for path in text_paths:
        content = path.read_text(encoding="utf-8")
        assert old_distribution not in content, path
        assert old_module not in content, path
        assert old_class_prefix not in content, path


def test_env_example_exactly_tracks_supported_settings() -> None:
    env_example = read(".env.example")
    configured_names = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", env_example, re.MULTILINE))
    supported_names = {str(field.alias) for field in Settings.model_fields.values()}

    assert configured_names == supported_names
    assert "CREATE_REJOIN_AUTH_KEY" not in configured_names
    assert "APP_ENV" not in configured_names
    assert "secret" in env_example.lower()


def test_keepalive_workflow_keeps_exact_12_hour_schedule_and_uv_commands() -> None:
    workflow = read(".github/workflows/tailwarden.yml")
    assert workflow.startswith("name: Tailwarden\n")
    assert "group: tailwarden-keepalive" in workflow
    assert re.findall(r'cron:\s*"([^"]+)"', workflow) == ["0 0,12 * * *"]
    assert "environment: production" in workflow
    assert "github.ref_name == github.event.repository.default_branch" in workflow
    assert "id-token: write" in workflow
    assert "persist-credentials: false" in workflow
    assert 'REPORT_REDACT_DETAILS: "true"' in workflow
    assert "secrets.TS_" not in workflow
    assert "actions/checkout@v" not in workflow
    assert "actions/setup-python@v" not in workflow
    assert "astral-sh/setup-uv@v" not in workflow
    assert "uv sync --frozen --no-dev" in workflow
    assert "uv run --frozen --no-dev tailwarden run" in workflow
    assert 'version: "0.12.9"' in workflow
    assert "vars.SERVER_TAG ||" not in workflow
    assert "vars.REJOIN_AUTH_KEY_TAG ||" not in workflow
    assert "CREATE_REJOIN_AUTH_KEY" not in workflow
    assert "recovery-key" not in workflow
    assert "ensure-permanent-connectivity.sh" not in workflow
    assert "exchange-oidc-token.sh" not in workflow


def test_ci_uses_uv_and_does_not_hide_pytest_failures() -> None:
    workflow = read(".github/workflows/ci.yml")
    assert "uv sync --frozen --group dev" in workflow
    assert "uv run --frozen ruff check ." in workflow
    assert "uv run --frozen ruff format --check ." in workflow
    assert "uv run --frozen pytest" in workflow
    assert "persist-credentials: false" in workflow
    assert "actions/checkout@v" not in workflow
    assert "|| true" not in workflow


def test_dependabot_tracks_pinned_github_actions() -> None:
    dependabot = read(".github/dependabot.yml")
    assert "package-ecosystem: github-actions" in dependabot
    assert "package-ecosystem: uv" in dependabot
    assert "interval: weekly" in dependabot


def test_security_workflows_use_pinned_actions_and_minimum_permissions() -> None:
    dependency_review = read(".github/workflows/dependency-review.yml")
    codeql = read(".github/workflows/codeql.yml")

    assert "fail-on-severity: high" in dependency_review
    assert "comment-summary-in-pr: never" in dependency_review
    assert "security-events: write" in codeql
    assert "actions: read" in codeql
    assert "- actions" in codeql
    assert "- python" in codeql
    assert "id-token: write" not in dependency_review
    assert "id-token: write" not in codeql


def test_every_external_github_action_is_pinned_to_a_full_commit_sha() -> None:
    action_refs: list[tuple[str, str]] = []
    for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
        for ref in re.findall(r"uses:\s*[^@\s]+@([^\s]+)", path.read_text(encoding="utf-8")):
            action_refs.append((path.name, ref))

    assert action_refs
    assert all(re.fullmatch(r"[0-9a-f]{40}", ref) for _, ref in action_refs), action_refs


def test_repository_has_private_vulnerability_reporting_policy() -> None:
    policy = read(".github/SECURITY.md")
    assert "privately" in policy
    assert "Do not open a public issue" in policy


def test_old_bash_implementation_is_removed() -> None:
    assert not (ROOT / "scripts/tailscale/ensure-permanent-connectivity.sh").exists()
    assert not (ROOT / "scripts/tailscale/exchange-oidc-token.sh").exists()


def test_docs_match_schedule_recovery_and_secret_contracts() -> None:
    english = read("README.md")
    chinese = read("README.zh-CN.md")
    operations = read("docs/tailwarden.md")
    env_example = read(".env.example")
    config = read("src/tailwarden/config.py")
    combined = "\n".join([english, chinese, operations])
    oidc_subject = "repo:lesterholy/tailwarden:environment:production"

    assert oidc_subject in english
    assert oidc_subject in chinese
    assert oidc_subject in operations
    assert "repo:" + "OWNER/REPO" not in combined
    assert "0 0,12 * * *" in english
    assert "0 0,12 * * *" in chinese
    assert "0 0,12 * * *" in operations
    assert "15 minutes" not in combined
    assert "15分钟" not in combined
    assert "CREATE_REJOIN_AUTH_KEY" not in combined
    assert "no FastAPI deployment is required" in english
    assert "不需要单独部署 FastAPI" in chinese
    assert "不需要单独部署 FastAPI" in operations
    assert "tailwarden serve" not in combined
    assert "/api/v1/" not in combined
    assert "Settings > Trust credentials" in combined
    assert "Settings > Keys" in combined
    assert "Settings > Environments > production" in combined
    assert "REPORT_REDACT_DETAILS" in combined
    assert "never falls back to `TS_TOKEN`" in english
    assert "TS_TOKEN` 也不会回退" in chinese
    assert "authorized repository collaborators" in english
    assert "有相应权限的仓库协作者" in chinese
    assert "tag:tailwarden-managed" in combined
    assert config.count('default="tag:tailwarden-managed"') == 2
    assert "Secret scanning" in combined
    assert "Push protection" in combined
    assert "injects these automatically" in env_example
    assert "openssl rand -hex 32" in combined
    assert "controlled local terminal" in english
    assert "受控本地终端" in chinese
