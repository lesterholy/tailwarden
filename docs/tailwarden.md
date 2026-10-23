# Tailwarden 运维说明

Tailwarden 的 CLI 执行单次 Tailscale 设备巡检。调度与自动化在本仓库外维护；本仓库
不提供运行 workflow，也不规定生产频率。单次巡检无需单独部署 FastAPI 服务。
配置字段及默认值以 `.env.example` 和 `src/tailwarden/config.py` 为准。

## 运行与鉴权

```bash
uv sync --frozen
uv run --frozen tailwarden run
```

本地短时调试可从 Tailscale **Settings > Keys** 创建短期 API access token，并临时
设置 `TS_TOKEN`。不要提交真实 `.env`、token、auth key 或 recovery key。

应用保留可选的 GitHub Actions 运行时兼容：该运行时使用 GitHub OIDC 向 Tailscale
Workload Identity Federation 换取短期访问令牌，要求完整的 `TS_CLIENT_ID`、
`TS_AUDIENCE`、`ACTIONS_ID_TOKEN_REQUEST_TOKEN` 和
`ACTIONS_ID_TOKEN_REQUEST_URL`。GitHub Actions 运行时不会回退使用 `TS_TOKEN`。
这些 `ACTIONS_ID_TOKEN_REQUEST_*` 值由 GitHub Actions 运行时提供，不能在普通
本地环境中假定存在。外部自动化如何调度、注入配置及保护凭据，由其维护方负责。

## 设备选择与结果

`SERVER_TAG` 选择目标设备；建议使用只分配给受管设备的具体 tag，例如
`tag:tailwarden-managed`。`REJOIN_AUTH_KEY_TAG` 是显式创建 recovery key 时使用的
具体 tag，不接受通配符。GitHub Actions 运行时会拒绝通配的 `SERVER_TAG`；普通
CLI 支持 `all` 等选择器，使用前应确认覆盖范围。

每次 `run` 获取设备列表，检查 `online`、`lastSeen` 和 key expiry，对需要维护的
节点关闭 key expiry，并输出报告。`no matches` 表示没有匹配设备。正常巡检不会
创建 recovery key。

`FAIL_ON_STALE` 默认 `true`；`--fail-on-stale` 和 `--no-fail-on-stale` 覆盖
单次运行的设置。启用时 stale 或 offline 设备返回 `2`；设备维护等操作错误返回
`4`，配置错误返回 `64`，没有失败条件时返回 `0`。未知 `lastSeen` 单独统计。
`DRY_RUN=true` 或 `run --dry-run` 可检查计划动作；实际更新需要关闭 dry run。

## 输出与 GitHub 兼容

本地 CLI 默认输出详细 JSON；设置 `REPORT_REDACT_DETAILS=true` 可隐藏设备细节。
GitHub Actions 运行时强制脱敏。若运行环境提供 `GITHUB_STEP_SUMMARY`，应用可
写入 Step Summary；`REPORT_DEVICE_NAMES=true` 会在其中显示匹配设备的短名和
固定状态，默认 `false`。设备状态来自 Tailscale API，不代表实时网络探测。
外部自动化应自行决定日志可见范围和这些输出设置。

## Recovery key

只在确实需要恢复机器时，由授权操作者在受控本地终端执行：

```bash
uv run --frozen tailwarden recovery-key
```

返回的密钥只交给当前操作者，不要写入日志、Step Summary 或 artifact。恢复节点时，
除 `--auth-key` 外，还要保留该节点原有的 `tailscale up` 参数；用完后撤销临时
API token。GitHub Actions 运行时禁止执行 `recovery-key`。

## 本地开发检查

```bash
uv sync --frozen --group dev
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen pytest
```

只有在有意更新依赖时才使用非 frozen 的 `uv lock` 或 `uv sync`，并提交对应的
`uv.lock` 变更。Dependabot 继续维护 `uv` 依赖。漏洞请按仓库
[安全策略](../.github/SECURITY.md)私密报告。
