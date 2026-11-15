# Secure Knowledge Base MCP Server

> 一个用 **OAuth 2.1 + PKCE** 保护的企业知识库 MCP Server：身份可信、权限最小、数据隔离、行为可审计。

[![CI](https://github.com/DragonYi1722/mcp-oauth-kb/actions/workflows/ci.yml/badge.svg)](https://github.com/DragonYi1722/mcp-oauth-kb/actions)
![Python](https://img.shields.io/badge/Python-3.13-blue)
![MCP](https://img.shields.io/badge/MCP-2026--07--28-purple)
![License](https://img.shields.io/badge/license-MIT-green)

> 状态：第 1–9 周完成。Keycloak realm 已导入并验证 PKCE、JWT claims（`sub`/`aud`/`scope`/角色）；Server 的 TokenVerifier、PRM、scope/ACL、审计、健康检查与按用户限流通过真实 token 端到端验证；Python 与 TypeScript 客户端均能独立完成 OAuth 登录、调用工具并自动刷新 token；31 项安全负向测试覆盖 10 类威胁；容器镜像、Caddy HTTPS 代理与 4 个 job 的 GitHub Actions 流水线已就绪。完整功能按 `docs/01-计划书.md` 的 10 周计划推进。

---

## 这个项目解决什么问题

企业知识库接入 AI 客户端后，常见问题是：静态 API Key、所有人权限相同、工具参数里直接传 `user_id`、没有审计。

本项目把 MCP Server 实现为标准的 **OAuth Resource Server**：

- 客户端通过 **授权码 + PKCE** 登录，token 由 Keycloak 签发；
- Server 本地用 JWKS 验签，校验 `iss` / `aud` / `exp` / scope；
- scope 映射到工具（`kb.read` / `kb.write` / `kb.admin`）；
- 身份只取 token 的 `sub`，入参中的身份字段一律忽略；
- 文档按 个人 / 部门 / 全公司 三级隔离；
- 每次调用写入审计日志。

## 架构

```
MCP Client ──① 401 + PRM──▶ MCP Server (Resource Server)
     │                          │
     │ ② 授权码 + PKCE          │ ④ JWKS 验签 / scope / ACL
     ▼                          ▼
 Keycloak (AS) ──③ 签发 JWT──▶ PostgreSQL
```

详细时序见 [`docs/02-工程方案.md`](docs/02-工程方案.md)。

## 技术栈

| 层 | 选型 |
|---|---|
| 协议 | MCP 2026-07-28（Streamable HTTP，无状态） |
| Server | Python 3.13 + 官方 `mcp==2.3.0` |
| AS | Keycloak 26.7.x |
| DB | PostgreSQL 16 |
| 客户端 | MCP Inspector / Python SDK / TypeScript SDK |
| 部署 | Docker Compose + Caddy |

## 快速开始

### 前置要求

- Docker Desktop
- Python 3.12+
- Node.js 20+（仅 Inspector 与 TS 客户端）

### 1. 启动基础设施（第 3 周起需要）

默认 `KB_STORAGE_BACKEND=memory`，自带演示数据（alice / bob / admin 与 9 篇文档），**不依赖 Docker**，可以直接跳到第 2 步。

要跑 Keycloak 与 PostgreSQL，先安装 [Docker Desktop](https://www.docker.com/products/docker-desktop/)（Windows 需启用 WSL2，安装后重启），确认 `docker --version` 可用，然后：

```bash
docker compose -f infra/docker-compose.yml up -d postgres keycloak
python scripts/check_keycloak.py --wait 120
```

Keycloak 首次启动约 30–60 秒，realm `kb` 与 3 个用户由 `realm-kb.json` 自动导入。控制台：`http://localhost:8080`（`admin` / `admin`）。

> 国内网络下 `docker run hello-world` 可能报 `registry-1.docker.io` 连接超时；Compose 已改用 AWS ECR Public 镜像。验证引擎可用：
>
> ```bash
> docker run --rm public.ecr.aws/docker/library/hello-world:latest
> ```

验证脚本会检查 discovery、issuer、PKCE S256 与 JWKS 的 RSA 签名密钥；通过后用浏览器走真实授权码流程取 token：

```bash
python scripts/get_token.py
```

用 `alice` / `password` 登录，终端会打印 `iss` / `sub` / `aud` / `scope` / `department` / `roles`。`aud` 必须包含 `http://localhost:8000/mcp`，否则 MCP Server 会拒绝该 token。

自动化或 CI 环境可以用无浏览器模式（仅限本地演示账号）：

```bash
python scripts/get_token.py --headless --username alice --scope "openid kb.read"
```

灌入真实 `sub` 的种子数据（realm 重导入后 `sub` 会变化，加 `--reset`）：

```bash
python scripts/bootstrap_seed.py --reset
```

认证链路端到端验证（401 → whoami → 部门隔离 → scope 拒绝）：

```bash
KB_AUTH_ENABLED=true KB_STORAGE_BACKEND=postgres python -m kb_mcp.server
python scripts/smoke_auth.py
```

#### 备用：不用 Docker 运行 Keycloak

如果 `quay.io` 拉取过慢，可直接运行 Keycloak 官方发行版（需要 JRE 17+）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_keycloak_native.ps1
```

脚本会把 `infra/keycloak/realm-kb.json` 复制到 `data/import` 并以 `--import-realm` 启动。
默认路径可通过 `-KeycloakHome` / `-JavaHome` 覆盖；数据库仍可用 Docker 里的 PostgreSQL。

### 2. 启动 MCP Server（本地开发）

```bash
cd apps/mcp-server
python -m venv .venv
source .venv/Scripts/activate   # Windows Git Bash
pip install -e ".[dev]"
cp ../../.env.example ../../.env
python -m kb_mcp.server
```

Server 监听 `http://localhost:8000`。

### 3. 用 Inspector 连接

认证模式启动 Server：

```bash
KB_AUTH_ENABLED=true KB_STORAGE_BACKEND=postgres python -m kb_mcp.server
npx @modelcontextprotocol/inspector
```

- 在 Inspector 的 Servers 页添加或编辑一个 **HTTP / Streamable HTTP** server，URL：`http://localhost:8000/mcp`
- 连接前打开 **Settings → OAuth Settings**，填 **Client ID = `mcp-inspector`**、Secret 留空，并点 **Clear stored OAuth**
- 登录 `alice` / `password`；若浏览器已有 Keycloak SSO 会话，会静默回调，不弹登录页
- 调 `whoami`：`username=alice`、`department=engineering`、`roles` 含 `employee`
- 调 `search_documents`（搜「复盘」）：只返回「Q2 复盘」
- 调 `archive_document`：`FORBIDDEN_SCOPE`（Inspector 只申请了 `kb.read`）

> 两个常见报错：`Dynamic Client Registration rejected` 是没填 Client ID（Inspector 默认走 DCR）；`Offline tokens not allowed` 是用户缺少 `offline_access` 角色。

### 4. 用 Python / TypeScript 客户端

两个客户端各自实现 OAuth 2.1 授权码 + PKCE（不复用 Inspector 或脚本的 token）：discovery → S256 → 回调 `http://localhost:8765/callback` → 换 token → 缓存到用户目录，过期后用 refresh token 静默续期。

Python（包 `apps/py-client`，客户端 ID `mcp-cli`）：

```bash
pip install -e apps/py-client
kb-client login                 # 浏览器登录 alice / password
kb-client whoami
kb-client search 复盘
kb-client create "周会记录" --content "..." --visibility department
kb-client tools                 # 列出工具与 JSON Schema
```

TypeScript（`apps/ts-client`，官方 SDK + 自写 PKCE）：

```bash
cd apps/ts-client
npm install
npm run build
node dist/index.js login
node dist/index.js whoami
node dist/index.js search 复盘
```

两端都支持 `--token` / `KB_ACCESS_TOKEN` 直接注入 token，以及 `--token-file` / `KB_TOKEN_FILE`、`KB_TS_TOKEN_FILE` 指定缓存路径。

无浏览器验收（脚本替浏览器提交登录表单，驱动两个客户端各跑一遍真实授权流程）：

```bash
python scripts/verify_clients.py
```

期望 6 行 `PASS`：每个客户端的 PKCE 登录、`whoami` 与过期 token 自动刷新。

### 5. 验证

| 检查 | 命令 / 操作 | 期望 |
|---|---|---|
| 健康检查 | `curl http://localhost:8000/healthz` | `{"status":"ok"}` |
| 资源元数据 | `curl http://localhost:8000/.well-known/oauth-protected-resource/mcp` | RFC 9728 JSON |
| 未登录（认证开启后） | 不带 token 调工具 | 401 + `WWW-Authenticate` |
| 登录 | Inspector OAuth | 成功回调 |
| 身份 | 调用 `whoami` | 返回 alice 的 `sub`/部门/scope |
| 检索 | 调用 `search_documents`，query=`复盘` | 只返回工程部可见文档 |
| 越权 | `get_document` 传入销售部文档 id | `NOT_FOUND`（不泄露存在性） |
| 管理权限 | 调用 `archive_document` | `FORBIDDEN_SCOPE`（alice 无 `kb.admin`） |
| 认证冒烟 | `python scripts/smoke_auth.py` | 6 项全部 `PASS` |
| 三用户隔离 | `python scripts/demo_week5.py` | alice 6 / bob 5 / admin 4 篇可见，越权均 `NOT_FOUND` |
| 就绪检查 | `curl http://localhost:8000/readyz` | `storage` 与 `jwks` 均 `ok`，故障返回 503 |
| 审计追溯 | `python scripts/audit_report.py --limit 5` | 每行含 `request_id`、结果与错误码 |
| Python 客户端 | `kb-client whoami` | 返回 alice 身份，退出码 0 |
| TS 客户端 | `node dist/index.js whoami` | 同上 |
| 客户端 OAuth 验收 | `python scripts/verify_clients.py` | 6 项 `PASS`（含 refresh） |
| 安全单测 | `pytest -m "security and not integration"` | 20 项通过，无需外部服务 |
| 安全负向（live） | `pytest -m "security and integration"` | 11 项通过（需 :8080 / :8000） |
| 限流 | 同一用户 60 秒内第 61 次工具调用 | `RATE_LIMITED`，写 `denied` 审计 |
| 镜像构建 | `docker build -t kb-mcp apps/mcp-server` | 构建成功，容器 `/healthz` 返回 200 |
| HTTPS 代理 | `curl -k https://localhost/readyz`（见下方 Docker 一节） | `{"status":"ready"}`，未登录 `/mcp` 返回 401 + PRM |
| CI | GitHub Actions | `python` / `clients` / `docker` / `integration` 四个 job |

### 6. Docker 与 HTTPS

```bash
cp .env.example .env
docker compose -f infra/docker-compose.yml up -d postgres keycloak
python scripts/bootstrap_seed.py --reset

# 方式一（二选一）：本机跑 Server，Caddy 代理到宿主机
KB_AUTH_ENABLED=true KB_STORAGE_BACKEND=postgres python -m kb_mcp.server
DOMAIN=localhost MCP_UPSTREAM=host.docker.internal:8000 \
  docker compose -f infra/docker-compose.yml --profile public up -d --no-deps caddy

# 方式二（二选一）：Server 也放进 Compose
docker compose -f infra/docker-compose.yml up -d --build mcp-server
DOMAIN=localhost \
  docker compose -f infra/docker-compose.yml --profile public up -d caddy

curl -k https://localhost/readyz
```

Caddy 使用内部 CA，浏览器首次访问需信任证书；公网部署（真实域名、Let's Encrypt、
Keycloak `KC_HOSTNAME`、realm audience 调整）见 [docs/02-工程方案.md](docs/02-工程方案.md) 第 10 节。

## 项目结构

```
mcp-oauth-kb/
├── apps/
│   ├── mcp-server/      # Python MCP Server（核心）
│   ├── py-client/       # Python OAuth 客户端
│   └── ts-client/       # TypeScript 客户端
├── infra/               # Docker Compose / Keycloak realm / Caddy
├── docs/                # 计划书、工程方案、安全设计、工具契约
├── scripts/             # API 核对、本地取 token
├── tests/               # 单元 / 集成 / 安全
└── README.md
```

## 文档

- [01-计划书](docs/01-计划书.md)：背景、目标、用户故事、10 周计划、风险
- [02-工程方案](docs/02-工程方案.md)：架构、时序、数据模型、鉴权实现、部署
- [03-安全设计](docs/03-安全设计.md)：STRIDE、10 类威胁、检查清单、测试矩阵
- [04-接口与工具](docs/04-接口与工具.md)：6 个工具的 JSON Schema 与错误码
- [05-演示脚本](docs/05-演示脚本.md)：3 分钟 Demo 分镜

## 安全特性

- OAuth 2.1 授权码 + PKCE S256
- JWT 本地验签（RS256、JWKS 缓存）
- `iss` / `aud` / `exp` / `sub` 强校验
- 工具级 scope 授权 + 数据级 ACL
- 身份只取 `sub`，不信任入参
- 越权统一返回 404
- 审计日志脱敏（参数只存 SHA-256 摘要）
- 参数化 SQL、输入长度限制、分页上限
- 按 token `sub` 的滑动窗口限流（默认 60 次/分钟，可配置）

## 已知限制

- Keycloak 26 的 `--import-realm` 不会创建内置 client scope，因此 `basic`（提供 `sub`）必须在本仓库的 realm JSON 中显式定义；
- `fullScopeAllowed=false` 时 Keycloak 会丢弃未显式授权给 client 的 realm 角色，`realm_access.roles` 为空，所以本项目设为 `true`（工具权限仍由 token 的 scope 控制）；
- Keycloak 26 的 `start-dev` 不再内置 admin 账号，需设置 `KC_BOOTSTRAP_ADMIN_USERNAME` / `KC_BOOTSTRAP_ADMIN_PASSWORD`；
- Inspector 会自动申请 `offline_access`：client 的 optional scopes 与用户的 realm 角色都必须包含它，realm JSON 已为 3 个演示用户配置；
- Keycloak 26.7 对 RFC 8707 `resource` 参数仅实验性支持，audience 通过 per-scope mapper 绑定；
- CIMD 在 Keycloak 中仍是实验特性，MVP 使用预注册 client；
- PostgreSQL 内置分词器不切分中文，MVP 使用 `pg_trgm`；
- npm 上 `@modelcontextprotocol/sdk` 最新为 `1.32.2`（尚无计划中的 2.x），仍以 `initialize` 协商；对无状态 Server 实测可用，客户端 OAuth 因此手写 PKCE，而非使用 SDK 的 OAuth provider；
- 限流计数保存在进程内，多副本部署时每个副本各有一套预算；需要严格全局配额时应改用 Redis，反向代理也应限制请求体大小；
- 开发用户密码为演示用途，生产必须替换。

## 路线图

- [x] 第 1 周：echo / whoami 骨架，Inspector 可连接
- [x] 第 2 周：数据层（内存 + PostgreSQL）与 5 个业务工具，23 个单元测试通过
- [x] 第 3 周：Keycloak realm + 授权码 + PKCE，token 含正确 `sub` / `aud` / `scope` / 角色
- [x] 第 4 周：TokenVerifier + PRM，Inspector 完成浏览器 OAuth 登录并调用 `whoami`
- [x] 第 5 周：三用户隔离演示（alice 6 / bob 5 / admin 4 篇可见，越权返回 `NOT_FOUND`）
- [x] 第 6 周：审计与健康检查（`request_id` 贯穿、`/readyz`、`audit_report.py`）
- [x] 第 7 周：Python / TypeScript 客户端（各自完成 PKCE 登录、工具调用、token 自动刷新）
- [x] 第 8 周：安全负向测试（31 项，覆盖 10 类威胁；新增按用户限流）
- [x] 第 9 周：Docker 镜像 + Caddy HTTPS + GitHub Actions（4 个 job；公网域名待接入）
- [ ] 第 10 周：文档、视频、博客

## License

MIT
