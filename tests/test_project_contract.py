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


def test_dependabot_tracks_uv_dependencies() -> None:
    dependabot = read(".github/dependabot.yml")
    assert "package-ecosystem: uv" in dependabot
    assert "interval: weekly" in dependabot


def test_repository_has_private_vulnerability_reporting_policy() -> None:
    policy = read(".github/SECURITY.md")
    assert "privately" in policy
    assert "Do not open a public issue" in policy


def test_old_bash_implementation_is_removed() -> None:
    assert not (ROOT / "scripts/tailscale/ensure-permanent-connectivity.sh").exists()
    assert not (ROOT / "scripts/tailscale/exchange-oidc-token.sh").exists()


def test_docs_match_local_runtime_and_recovery_contracts() -> None:
    english = read("README.md")
    chinese = read("README.zh-CN.md")
    operations = read("docs/tailwarden.md")
    env_example = read(".env.example")
    assert "Settings > Keys" in english
    assert "Settings > Keys" in chinese
    assert re.search(r"never falls back to\s+`TS_TOKEN`", english)
    assert "不会回退到 `TS_TOKEN`" in chinese
    assert "requires complete OIDC settings" in env_example
    assert "controlled local terminal" in english
    assert "受控本地终端" in chinese
    for document in (english, chinese, operations):
        assert "uv sync --frozen --group dev" in document
        assert "uv run --frozen ruff check ." in document
        assert "uv run --frozen ruff format --check ." in document
        assert "uv run --frozen pytest" in document
        assert "REPORT_DEVICE_NAMES" in document
        assert "REPORT_REDACT_DETAILS" in document
        assert "FAIL_ON_STALE" in document
