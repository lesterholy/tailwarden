# Tailwarden

目标：在不保存长期 Tailscale API 密钥的前提下，持续维护需要长期在线的 Tailscale 节点。

## 总体设计

当前实现分成两层：

- GitHub Actions：唯一的生产定时调度源。
- Python 应用：提供共享业务逻辑和 CLI；FastAPI 代码保留用于本地集成测试。

生产环境保留固定调度：

- Cron：`0 0,12 * * *`
- 频率：每 12 小时一次
- 时间：约 `00:00 UTC` 与 `12:00 UTC`
- 注意：GitHub Actions 调度可能延迟几分钟，不保证绝对准点

生产巡检只需要 GitHub Actions 调用 CLI，不需要单独部署 FastAPI 服务。本文不包含
FastAPI 部署步骤。

## 技术栈

- Python `3.12`
- FastAPI
- `aiohttp`
- `uv`

依赖管理、锁文件和执行入口都由 `uv` 负责。环境变量契约统一维护在 `.env.example`。

## 运行入口

CLI：

- `uv run --frozen tailwarden run`：执行一次巡检
- `uv run --frozen tailwarden recovery-key`：显式创建 recovery key

## 认证与凭据

生产路径：

1. GitHub Actions 使用 GitHub OIDC。
2. Tailscale Workload Identity Federation 颁发短期访问能力。
3. Python 应用再调用 Tailscale API。

本地调试路径：

- 可在 Tailscale 管理后台 **Settings > Keys** 创建短期 API access token，并临时写入
  `TS_TOKEN`。
- `TS_TOKEN` 只适合本地短期调试，不应作为生产默认方案。

不要提交或长期保存以下内容：

- `tskey-*`
- recovery key
- GitHub OIDC token
- Tailscale access token

## 环境变量来源

生产配置涉及两个后台：

1. 在 Tailscale 管理后台 **Access controls** 定义并分配 `SERVER_TAG` 使用的 tag。
    https://console.tailscale.com/admin/acls/visual/tags
    ![](https://image.ioo.one/general/bf2aa2ca-d640-4f80-9929-a4327f662423.png)
2. 在 Tailscale 管理后台 **Settings > Trust credentials** 创建 GitHub Actions Trust
   Credential。

   ![](https://image.ioo.one/general/6fcd92b2-cc6b-4fd8-97e5-8ec400df132a.png)

   ![](https://image.ioo.one/general/95065755-505f-4f2e-b750-166869995353.png)


3. 在 GitHub 仓库 **Settings > Environments > production > Variables** 保存非敏感配置。

公开仓库的普通访客不能直接浏览 Variables 值，但有相应权限的仓库协作者可以通过 GitHub
API 读取。GitHub Runner 会在 step env 中明文打印配置 vars，包括 Client ID、Audience、
tailnet、tag 和阈值。这些是配置标识符，不是 secret；程序脱敏不覆盖 Runner 的这部分
输出。因此这里只存放可公开的配置元数据。

Trust Credential 的 OIDC subject 必须与 GitHub workflow 实际拿到的 OIDC claim 精确匹配。
不要继续照抄旧示例里的仓库路径格式；请先读取当前仓库的 subject prefix，再按 job 绑定的
environment 追加后缀：

```bash
gh api repos/lesterholy/tailwarden/actions/oidc/customization/sub --jq '.sub_claim_prefix'
```

2026 年 7 月 15 日后创建的 GitHub 仓库默认使用包含不可变 owner/repository ID 的 Subject
前缀。当前仓库返回 `repo:lesterholy@33650692/tailwarden@1356719362`。由于生产 job 绑定了
`production` environment，所以完整 Subject 是：

```text
repo:lesterholy@33650692/tailwarden@1356719362:environment:production
```

应始终以 API 返回的 `sub_claim_prefix` 为准。如果将来 workflow 不再绑定 `production`
environment，就不要追加 `:environment:production`。仓库迁移、重建，或 GitHub OIDC
Subject 自定义规则变化后，也应重新核对。

Tailscale Trust Credential 推荐值：

| Field   | Value                                                                            |
|---------|----------------------------------------------------------------------------------|
| Issuer  | `GitHub Actions`                                                                 |
| Subject | `repo:lesterholy@33650692/tailwarden@1356719362:environment:production`          |
| Scopes  | `devices:core`                                                                   |
| Tags    | `tag:tailwarden-managed`（推荐专用 tag）                                           |

GitHub Actions 强制程序输出脱敏，workflow 保持 `REPORT_REDACT_DETAILS=true`。console JSON
保留结果、计数、运行开始/结束时间与带 HTTP 状态码的通用错误，不新增机器名或逐台字段。

`REPORT_DEVICE_NAMES` 应用默认 `false`，workflow 显式设为 `true`，因此 Step Summary 会
**公开每台匹配机器的短名**、控制面健康状态和密钥维护结果。短名取 API `name` 的第一个
DNS 标签，不回退到 hostname、ID 或地址；缺失或非法名称显示 `(name unavailable)`。
状态只来自 Tailscale API `online`/`lastSeen`，不主动 ping，不证明实际网络可达。

设备表覆盖最近出现、offline、stale、未知 lastSeen，以及密钥已禁用、本次禁用成功、
禁用失败、dry-run 计划；零匹配明确显示 `0 matches`。程序脱敏输出仍隐藏设备 ID、DNS
后缀、地址、tailnet、tag、逐台时间和上游错误详情，保留原有运行时间戳和 HTTP 状态码。
若需关闭表格，将 workflow 的 `REPORT_DEVICE_NAMES` 改为 `"false"`。Runner 的 step env
仍会明文打印配置 vars；需要进一步逐台定位时，请在受控本地终端重跑。

各环境变量的来源如下：

| 应用变量                                                          | GitHub Environment 变量              | 来源或设置方式                                                                               |
|-------------------------------------------------------------------|--------------------------------------|----------------------------------------------------------------------------------------------|
| `TS_CLIENT_ID`                                                    | `TS_MANAGER_CLIENT_ID`               | 创建 Trust Credential 后，从详情页复制 Client ID                                              |
| `TS_AUDIENCE`                                                     | `TS_MANAGER_AUDIENCE`                | 从同一详情页复制 Audience，通常为 `api.tailscale.com/<client-id>`                             |
| `TAILNET`                                                         | `TAILNET`                            | 默认使用 `-`；否则从 Tailscale 管理后台复制 tailnet DNS 名称                                  |
| `SERVER_TAG`                                                      | `SERVER_TAG`                         | 从 Tailscale Access controls 策略选择具体 tag；GitHub Actions 不接受 `all`、`*` 或 `tag:*`     |
| `REJOIN_AUTH_KEY_TAG`                                             | `REJOIN_AUTH_KEY_TAG`                | 从 Access controls 策略选择具体 tag，不接受通配符                                             |
| `STALE_AFTER_MINUTES`                                             | `STALE_AFTER_MINUTES`                | 运维人员定义；默认 `30`                                                                       |
| `KEY_EXPIRY_WARNING_HOURS`                                        | `KEY_EXPIRY_WARNING_HOURS`           | 运维人员定义；默认 `168`                                                                      |
| `AUTH_KEY_EXPIRY_SECONDS`                                         | `AUTH_KEY_EXPIRY_SECONDS`            | 运维人员定义；默认 `3600`                                                                     |
| `DRY_RUN`                                                         | workflow dispatch 的 `dry_run`       | 手动触发时选择；定时任务固定为 `false`                                                        |
| `FAIL_ON_STALE`                                                   | workflow 固定 `false`               | 定时和手动运行均只报告设备异常；本地 CLI 默认仍为 `true`                                     |
| `REPORT_DEVICE_NAMES`                                             | workflow 固定 `true`                | 应用默认 `false`；仅控制 Step Summary 设备表，会公开机器短名和固定状态                       |
| `REPORT_REDACT_DETAILS`                                           | workflow 固定 `true`                | 本地默认 `false`；GitHub Actions 强制程序脱敏，不覆盖 Runner step env                         |
| `TS_TOKEN`                                                        | 不设置                               | 仅本地调试；从 Tailscale **Settings > Keys** 创建短期 API access token                        |
| `AUTH_KEY_DESCRIPTION`                                            | 不设置                               | 仅影响本地显式 recovery key；由操作者填写或使用 `.env.example` 默认值                         |
| `HTTP_TOTAL_TIMEOUT_SECONDS` / `HTTP_CONNECT_TIMEOUT_SECONDS`     | 不设置                               | 本地可调参数；由操作者填写或使用 `.env.example` 默认值                                        |
| `APP_HOST` / `APP_PORT` / `APP_LOG_LEVEL` / `APP_API_TOKEN`       | 不设置                               | 仅用于可选本地 API 测试；workflow 不需要。`APP_API_TOKEN` 可用 `openssl rand -hex 32` 自行生成 |
| `ACTIONS_ID_TOKEN_REQUEST_TOKEN` / `ACTIONS_ID_TOKEN_REQUEST_URL` | 不设置                               | `id-token: write` 生效后由 GitHub Actions 自动注入                                           |
| `GITHUB_STEP_SUMMARY` / `GITHUB_ACTIONS`                          | 不设置                               | 由 GitHub Actions 自动注入                                                                   |

使用 GitHub CLI 写入 `production` environment 配置时，请使用环境作用域参数：

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

workflow 会把 GitHub environment 里的 `TS_MANAGER_CLIENT_ID` 和 `TS_MANAGER_AUDIENCE` 映射到应用内部使用的 `TS_CLIENT_ID` 和 `TS_AUDIENCE`。

说明：

- `SERVER_TAG` 用于选择被巡检和修复的设备。
- `REJOIN_AUTH_KEY_TAG` 用于显式恢复时新 auth key 自动附带的 tag。
- `.env.example` 是契约样例，真实 `.env` 不应提交。
- `TS_MANAGER_CLIENT_ID`、`TS_MANAGER_AUDIENCE`、Tailnet、tag 和阈值是配置元数据，放在
  Environment Variables。
- `TS_TOKEN`、`APP_API_TOKEN`、OIDC JWT、Tailscale access token 和 recovery/auth key 是
  密钥，绝不能放在 Variables。生产 workflow 不需要这些密钥。
- `ACTIONS_ID_TOKEN_REQUEST_TOKEN`、`ACTIONS_ID_TOKEN_REQUEST_URL`、
  `GITHUB_STEP_SUMMARY` 和 `GITHUB_ACTIONS` 由 GitHub 自动注入。

## 公开仓库安全边界

- GitHub Actions 必须使用完整 OIDC 配置，代码会拒绝回退到 `TS_TOKEN`。
- Trust Credential Subject 必须精确绑定当前 GitHub OIDC claim；本仓库当前值为
  `repo:lesterholy@33650692/tailwarden@1356719362:environment:production`，Scope 只授予
  `devices:core`。
- 优先给受管设备使用专用 tag（例如 `tag:tailwarden-managed`），并让 Trust Credential 的
  Tags 与 `SERVER_TAG` 完全一致；生产环境避免使用 `all`、`*` 或 `tag:*`。
- workflow 不为 `SERVER_TAG` 和 `REJOIN_AUTH_KEY_TAG` 提供隐式默认值；两者必须显式配置，
  且应用会在 GitHub Actions 中拒绝通配 `SERVER_TAG`。
- 将 `production` environment 的 deployment branches 限制为默认分支。
- 无人值守的 12 小时定时任务不要设置 required reviewers，否则每次运行都会等待审批。
- pull request CI 没有 `id-token: write` 权限，也不会绑定 `production` environment。
- 第三方 GitHub Actions 固定到完整 commit SHA；Dependabot 负责提出后续更新。

首次部署后，先手动执行一次 `dry_run=true`，确认 OIDC、设备 tag 和公开报表符合预期，再让
定时任务执行实际修改。定时与手动触发均固定 `FAIL_ON_STALE=false`，没有手动失败策略输入。

## 设备选择语义

推荐生产值：

- `SERVER_TAG=tag:tailwarden-managed`
- `REJOIN_AUTH_KEY_TAG=tag:tailwarden-managed`

代码支持将 `SERVER_TAG` 设为 `all` 或其他通配选择器，但生产环境不推荐这样做。若确有
广泛巡检需求，只能在受控本地环境使用，并确认覆盖范围符合预期。GitHub Actions 会拒绝
通配选择器。

`REJOIN_AUTH_KEY_TAG` 必须是具体 tag（例如 `tag:tailwarden-managed`），不接受 `all`、`*` 或
`tag:*` 通配符，因为它描述的是新节点入网后要自动携带的 tag，而不是设备过滤器。

已有环境若显式使用 `tag:server`，仍然兼容；只有确认该 tag 的所有设备都应由本项目管理时
才建议继续使用。

## GitHub 仓库安全设置

仓库文件已经提供 CodeQL、Dependency Review 和 Dependabot，但以下保护必须在 GitHub
后台手工启用：

1. **Settings > Environments > production**：deployment branches 只允许默认分支；为保持
   12 小时无人值守调度，不设置 required reviewers。
2. **Settings > Rules > Rulesets**：保护默认分支，要求 PR 与 CI/安全检查通过，禁止强推和
   删除默认分支。
3. **Settings > Code security and analysis**：启用 Dependency graph、Dependabot alerts、
   Dependabot security updates、Secret scanning、Push protection 与 Private vulnerability
   reporting。
4. **Settings > Actions > General**：默认 `GITHUB_TOKEN` 权限设为只读，并保持“允许 Actions
   创建和批准 PR”关闭。

漏洞请通过仓库 **Security > Advisories > Report a vulnerability** 私密报告，不要把 token、
JWT 或设备信息粘贴到公开 Issue。

## 正常巡检行为

每次 `run` 会执行这些动作：

1. 获取短期 Tailscale 访问令牌。
2. 拉取目标设备列表。
3. 检查 `lastSeen`、`online`、key expiry 状态。
4. 对需要修复的节点关闭 key expiry。
5. 生成运行摘要和状态结果。

关键约束：

- 正常巡检不会自动创建 recovery key。
- `no matches` 代表没有匹配到设备，应作为可见结果返回，而不是伪装成成功修复。
- workflow 中 stale、offline、未知 lastSeen 只记录表格、正常退出（`0`）。
- API 操作错误（含单台禁用 key expiry 失败）、认证错误或 payload 异常仍返回 `4`；
  配置错误返回 `64`。单台维护失败后仍处理其余设备，Summary 保留所有匹配行。
- 本地 CLI 的 `FAIL_ON_STALE` 默认 `true`，`--fail-on-stale` / `--no-fail-on-stale` 可覆盖；
  启用时 stale/offline 返回 `2`。未知 lastSeen 保持单独统计，不计为最近出现。

## Recovery key 策略

Recovery key 现在是显式操作，不再由自动巡检顺手创建后丢弃。

设计原则：

- 仅在确实需要恢复机器时，由授权操作者主动创建。
- 创建后立即返回给当前操作者。
- 绝不写入普通日志。
- 绝不写入 GitHub Step Summary。
- 绝不写入未加密 artifact。

推荐恢复流程：

1. 在 Tailscale 管理后台 **Settings > Keys** 创建短期 API access token，临时设置本地
   `TS_TOKEN`。
2. 在受控本地终端执行 `uv run --frozen tailwarden recovery-key` 创建凭据。
3. 在目标机器上重新执行 `tailscale up`。
4. 除 `--auth-key` 外，保留该机器原有的 `tailscale up` 参数；用完后撤销临时 API token。

示例：

```bash
sudo tailscale up \
  --auth-key="$AUTH_KEY" \
  --advertise-tags="$REJOIN_AUTH_KEY_TAG" \
  --ssh \
  --accept-dns=true
```

如果该节点原本还带有 `--advertise-routes`、`--accept-routes`、`--reset` 以外的特定参数，恢复时也要一并保留。

## 本地开发建议

```bash
cp .env.example .env
uv sync --frozen
uv run --frozen tailwarden run
```

只有在有意更新依赖时才使用非 frozen 的 `uv lock` 或 `uv sync`，并提交对应的
`uv.lock` 变更。

如果只做本地验证，可在 `.env` 中临时提供 `TS_TOKEN`。生产巡检应使用 GitHub OIDC +
Tailscale workload identity。

## 参考资料

- Tailscale key expiry: https://tailscale.com/docs/features/access-control/key-expiry
- Tailscale auth keys: https://tailscale.com/docs/features/access-control/auth-keys
- Tailscale API: https://tailscale.com/docs/reference/tailscale-api
- Tailscale workload identity federation: https://tailscale.com/docs/features/workload-identity-federation
- GitHub Actions OIDC: https://docs.github.com/en/actions/reference/security/oidc
