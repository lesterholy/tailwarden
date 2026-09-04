# Tailwarden

[English](README.md) | 中文

`tailwarden` 使用 Python 3.12、FastAPI、`aiohttp`、GitHub Actions OIDC 和 Tailscale
Workload Identity Federation 来维护长期在线的 Tailscale 节点。生产巡检只通过 GitHub
Actions 和 CLI 运行，不需要单独部署 FastAPI 服务。

## 功能概览

- 按 `SERVER_TAG` 选择目标设备。
- 为匹配到的服务器节点关闭 device key expiry。
- 在巡检结果里标记 stale 或 offline 节点。
- 没有匹配设备时返回 `no matches`，不算执行错误。
- 只有显式授权时才创建 recovery auth key。

## 运行模型

- 生产调度源：仅 GitHub Actions。
- Cron：`0 0,12 * * *`。
- 目标频率：每 12 小时一次，大致在 `00:00 UTC` 和 `12:00 UTC`。
- GitHub 托管调度可能略有延迟，实际启动时间不保证精确到分钟。
- 生产巡检不需要单独部署 FastAPI 服务。

## 技术栈

- Python `3.12`
- FastAPI
- `aiohttp`
- 使用 `uv` 管理依赖与执行命令
- 使用 `.env.example` 作为环境变量契约

## 快速开始

1. 安装 `uv` 和 Python `3.12`。
2. 本地开发时将 `.env.example` 复制为 `.env`。
3. 填写必需变量。
4. 执行 `uv sync --frozen`，严格按锁文件安装依赖。
5. 使用 `uv run --frozen tailwarden run` 本地执行一次巡检。

只有在有意更新依赖时才使用非 frozen 的 `uv lock` 或 `uv sync`，并提交对应的
`uv.lock` 变更。

## 配置说明

以 `.env.example` 作为全部受支持环境变量的权威说明。

生产环境把非敏感配置放在 GitHub 的 `production` environment variables 中，不提交真实
`.env` 文件。GitHub Variables 不是密钥保险箱：公开仓库的访客不能直接浏览设置页里的值，
但有相应权限的仓库协作者可以通过 GitHub API 读取，而且这些值不会自动脱敏。因此只应
保存即使意外进入日志也可以接受的配置元数据。

关键变量及其来源：

| 应用变量 | 在 GitHub 中设置为 | 作用 | 去哪里获取或如何确定 |
| --- | --- | --- | --- |
| `TS_CLIENT_ID` | `TS_MANAGER_CLIENT_ID` | 应用读取的 Tailscale workload identity client ID | 在 Tailscale 管理后台 **Settings > Trust credentials** 创建 GitHub Actions Trust Credential 后复制 Client ID |
| `TS_AUDIENCE` | `TS_MANAGER_AUDIENCE` | Tailscale OIDC audience | 从同一个 Trust Credential 复制 Audience，通常为 `api.tailscale.com/<client-id>` |
| `TAILNET` | `TAILNET` | Tailnet 名称，或默认 tailnet 的 `-` | 使用 Tailscale admin console 中显示的 tailnet 名称，或直接保留 `-` |
| `SERVER_TAG` | `SERVER_TAG` | 设备选择器；GitHub Actions 要求使用具体 tag | 在 Tailscale **Access controls** 中定义 `tag:tailwarden-managed` 之类的专用 tag，并只分配给目标设备 |
| `REJOIN_AUTH_KEY_TAG` | `REJOIN_AUTH_KEY_TAG` | 显式 recovery key 使用的具体 tag | 从同一份 Access controls 策略选择，通常与 `SERVER_TAG` 相同 |
| `STALE_AFTER_MINUTES` | `STALE_AFTER_MINUTES` | 多久未见设备就判定为 stale | 由操作者按巡检策略设定 |
| `KEY_EXPIRY_WARNING_HOURS` | `KEY_EXPIRY_WARNING_HOURS` | 距离 key expiry 多久开始预警 | 由操作者按巡检策略设定 |
| `AUTH_KEY_EXPIRY_SECONDS` | `AUTH_KEY_EXPIRY_SECONDS` | 显式 recovery auth key 的有效期 | 由操作者按恢复策略设定 |

不同仓库的 GitHub OIDC Subject 格式可能不同。不要照抄旧示例中的仓库路径格式，应先读取
当前仓库实际使用的 Subject 前缀：

```bash
gh api repos/lesterholy/tailwarden/actions/oidc/customization/sub --jq '.sub_claim_prefix'
```

2026 年 7 月 15 日后创建的 GitHub 仓库默认使用包含不可变 owner/repository ID 的 Subject
前缀。当前仓库返回 `repo:lesterholy@33650692/tailwarden@1356719362`，因此绑定
`production` environment 的完整 Subject 是：

```text
repo:lesterholy@33650692/tailwarden@1356719362:environment:production
```

应始终以 API 返回的 `sub_claim_prefix` 为准，再追加 workflow job 使用的 environment 后缀。
创建 Trust Credential 时，Issuer 选择 `GitHub Actions`，Subject 使用上述完整值，Scope 只授予
`devices:core`，Tags 填写 `SERVER_TAG` 对应的 tag。

按最小权限原则，如果 `tag:server` 还覆盖不应由本项目管理的设备，建议改用
`tag:tailwarden-managed` 这样的专用 tag，并让 Trust Credential 的 Tags 与 `SERVER_TAG`
保持一致。生产环境除非明确需要管理所有匹配设备，否则不要使用 `all`、`*` 或 `tag:*`。

`TS_MANAGER_CLIENT_ID`、`TS_MANAGER_AUDIENCE`、`TAILNET`、`SERVER_TAG`、
`REJOIN_AUTH_KEY_TAG` 和阈值都属于配置元数据，放在 Environment Variables。`TS_TOKEN`、
`APP_API_TOKEN`、OIDC JWT、Tailscale access token 以及任何 recovery/auth key 都属于密钥，
不得放进 Variables；当前生产 workflow 也不需要配置这些密钥。

为 GitHub `production` environment 设置配置时，使用带环境作用域的命令：

```bash
gh variable set --env production TS_MANAGER_CLIENT_ID --body "k1xxx..."
gh variable set --env production TS_MANAGER_AUDIENCE --body "api.tailscale.com/k1xxx..."
gh variable set --env production TAILNET --body "-"
gh variable set --env production SERVER_TAG --body "tag:tailwarden-managed"
gh variable set --env production REJOIN_AUTH_KEY_TAG --body "tag:tailwarden-managed"
gh variable set --env production STALE_AFTER_MINUTES --body "30"
gh variable set --env production KEY_EXPIRY_WARNING_HOURS --body "168"
gh variable set --env production AUTH_KEY_EXPIRY_SECONDS --body "3600"
```

workflow 在运行前会把 `TS_MANAGER_CLIENT_ID -> TS_CLIENT_ID`、`TS_MANAGER_AUDIENCE -> TS_AUDIENCE` 映射给应用。

这些值保存在仓库 **Settings > Environments > production > Environment variables**。
`ACTIONS_ID_TOKEN_REQUEST_TOKEN`、`ACTIONS_ID_TOKEN_REQUEST_URL`、
`GITHUB_STEP_SUMMARY` 和 `GITHUB_ACTIONS` 由 GitHub Actions 自动注入，不需要手工创建。

定时 workflow 不使用 `TS_TOKEN`。本地调试时，可在 Tailscale 管理后台 **Settings > Keys**
创建短期 API access token。`APP_HOST`、`APP_PORT`、`APP_LOG_LEVEL` 和 `APP_API_TOKEN` 也只用于
可选的本地 API 测试；需要时可执行 `openssl rand -hex 32` 自行生成 `APP_API_TOKEN`。

## 鉴权模型

- 生产 GitHub Actions 通过 GitHub OIDC 换取短期 Tailscale token。
- GitHub Actions 中强制使用完整 OIDC 配置，即使误传 `TS_TOKEN` 也不会回退使用。
- `TS_TOKEN` 只适合本地短时调试，从 Tailscale 管理后台 **Settings > Keys** 获取。
- 不建议把长期 Tailscale API key 作为生产常规路径。

### OIDC 配置与令牌生命周期

- `TS_CLIENT_ID` 和 `TS_AUDIENCE` 是 Tailscale Trust Credential 的标识信息，不是访问
  密钥。它们通常不会自行过期，可以长期保存在 GitHub Environment variables 中。
- `ACTIONS_ID_TOKEN_REQUEST_TOKEN` 和 `ACTIONS_ID_TOKEN_REQUEST_URL` 由 GitHub Actions
  为每次 job 自动注入。不要手工创建、复制到仓库变量或在 job 之外复用这些值。
- GitHub OIDC JWT 是运行时临时签发的短期凭证；使用它换取的 Tailscale access token
  同样是短期凭证。应用会在每次业务操作时重新获取和交换令牌，而不是持久化它们。
- 因此，生产环境长期保存的是 Client ID 和 Audience，而不是长期 Tailscale API token。

Client ID 和 Audience 虽然通常不会按时间自动过期，但以下变化会使现有配置失效：删除、
禁用或重新创建 Tailscale Trust Credential；GitHub job 改为绑定其他 environment 或不再绑定
environment；仓库 OIDC Subject 前缀变化；修改 Audience、Issuer、`devices:core` scope 或允许
使用的 tags。发生这些变化后，应重新查询 `sub_claim_prefix`，并从 Tailscale
**Settings > Trust credentials** 核对和更新 GitHub environment 配置。

## 公开仓库的日志策略

- GitHub Actions 始终强制脱敏，因此 logs 和 Step Summary 只保留结果、计数和通用错误，
  不展示 tailnet、tag、设备名称、地址、逐台时间或上游错误正文。
- 本地 CLI 默认输出完整 JSON 和详细 stderr 错误；只有在本地显式设置 `REPORT_REDACT_DETAILS=true` 时才会改成脱敏输出。
- 如果定时任务结束后需要看逐台设备细节，建议在受控本地终端重跑，而不是放宽 GitHub 上的脱敏默认值。

## CLI

CLI 命令：

- `uv run --frozen tailwarden run`
- `uv run --frozen tailwarden recovery-key`（仅限受控本地终端）

## Recovery key 规则

- 正常的 12 小时巡检不会自动创建 recovery key。
- recovery key 只能在受控本地终端通过显式 CLI 操作创建。
- 返回给操作者后的 recovery key 必须按敏感信息处理。
- 不要把 recovery key 写入日志、GitHub Step Summary 或 artifact。
- 恢复节点时，不要只补 `--auth-key`，还要保留该节点原本需要的 `tailscale up` 参数。

## GitHub Actions 行为

GitHub Actions 仍然是生产环境的唯一调度与告警入口：

- 支持手动触发。
- 定时频率保持每 12 小时一次。
- 发现 stale 节点时可以让 workflow 失败，以继续复用 GitHub 通知能力。
- API 或 payload 错误应直接让运行失败，而不是静默忽略。
- workflow 只允许从仓库默认分支运行，并拒绝通配设备选择器。
- 将 `production` environment 限制为默认分支；无人值守定时任务不要配置 required reviewers。
- CI 的 pull request job 没有 OIDC 权限，也不会读取 `production` environment。
- CodeQL 会扫描 Python 和 GitHub Actions；Dependency Review 会阻止新引入的高危漏洞依赖；
  Dependabot 同时维护 Actions 和 `uv` 依赖。

启用定时任务前，还要在 GitHub 后台手工确认：

- **Settings > Environments > production**：只允许默认分支部署。
- **Settings > Rules > Rulesets**：保护默认分支，要求通过 PR 和 CI/安全检查，并禁止强推与
  删除分支。
- **Settings > Code security and analysis**：启用 Dependency graph、Dependabot alerts、
  security updates、Secret scanning、Push protection 和 Private vulnerability reporting。
- **Settings > Actions > General**：默认 workflow 权限设为只读；除非以后确有需要，不允许
  Actions 创建或批准 PR。

安全问题应按[安全策略](.github/SECURITY.md)通过仓库 Security 页面私密报告，不要在公开
Issue 中粘贴任何凭据。

## 更多说明

更完整的配置、恢复和运维说明见 [docs/tailwarden.md](docs/tailwarden.md)。

## 开源协议

本项目基于 [MIT License](LICENSE) 开源。
