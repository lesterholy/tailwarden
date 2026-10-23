# Tailwarden

[English](README.md) | 中文

`tailwarden` 使用 Python 3.12、FastAPI 和 `aiohttp` 巡检 Tailscale 设备，并为选中的
服务器节点关闭 device key expiry。调度与自动化在本仓库外维护；通过 CLI 执行一次巡检
不需要单独部署 FastAPI 服务。

## 功能

- 按 `SERVER_TAG` 选择设备，并报告 stale 或 offline 节点。
- 为匹配的服务器节点关闭 device key expiry。
- 没有匹配设备时返回 `no matches`。
- 仅通过显式的 `recovery-key` 命令创建 recovery auth key。

## 本地使用

安装 `uv` 和 Python 3.12，将 `.env.example` 复制为 `.env` 并填写本次运行所需的
配置。本地短时使用时，可将 Tailscale **Settings > Keys** 创建的短期 API access token
填入 `TS_TOKEN`。不要将真实凭据提交到仓库。

```bash
uv sync --frozen
uv run --frozen tailwarden run
```

只在受控本地终端运行 `uv run --frozen tailwarden recovery-key`，并将返回的密钥按敏感
信息处理。普通 `run` 不会创建 recovery key。完整的环境变量契约见 `.env.example`。

## 运行语义

`FAIL_ON_STALE` 默认 `true`；`run --fail-on-stale` 与 `run --no-fail-on-stale`
可覆盖单次运行的设置。启用时 stale 或 offline 设备返回退出码 `2`；操作错误返回 `4`，
配置错误返回 `64`，没有失败条件的报告返回 `0`。

应用保留可选的 GitHub Actions 运行时兼容能力。在该运行时必须配置完整的 GitHub OIDC，
鉴权不会回退到 `TS_TOKEN`。存在 `GITHUB_STEP_SUMMARY` 时，应用可写入 Step Summary。
`REPORT_REDACT_DETAILS` 控制本地输出脱敏；GitHub Actions 运行时强制脱敏。
`REPORT_DEVICE_NAMES` 控制 Step Summary 是否显示设备短名。本仓库不再提供配置或调度
运行的 workflow。

## 本地开发检查

```bash
uv sync --frozen --group dev
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen pytest
```

只在有意更新依赖时使用非 frozen 的 `uv lock` 或 `uv sync`，并提交对应的 `uv.lock`
变更。Dependabot 继续维护 `uv` 依赖。

安全问题请按[安全策略](.github/SECURITY.md)私密报告。运维和恢复说明见
[docs/tailwarden.md](docs/tailwarden.md)。

本项目基于 [MIT License](LICENSE) 开源。
