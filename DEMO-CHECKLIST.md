# 演示清单 · DEMO CHECKLIST

> 打印一页放手边。照顺序打勾，出现红字就翻到最后一节。
> 详细分镜与旁白见 [docs/05-演示脚本.md](docs/05-演示脚本.md)。

---

## 0. 三个必须过的闸门

| 闸门 | 命令 | 只认这个结果 |
|---|---|---|
| ① Realm 就绪 | `python scripts/check_keycloak.py --wait 120` | 最后一行 `Realm is ready.` |
| ② 认证链路 | `python scripts/smoke_auth.py` | 6 个 `[PASS]` + `All checks passed.` |
| ③ 演示流程 | `python scripts/demo_portfolio.py` | 最后一行 `演示流程完成。` |

**过了 ①② 再开口讲话；③ 不过不要开始演示。**

---

## 1. 时间表

| 时间 | 动作 |
|---|---|
| T-60 | 起 Postgres + Keycloak |
| T-55 | 等 realm 就绪（闸门 ①） |
| T-50 | 灌种子数据 |
| T-48 | 起 MCP Server |
| T-45 | 冒烟（闸门 ②） |
| T-40 | **彩排一遍**（闸门 ③），把输出拍照存档 |
| T-30 | 调字号、摆窗口、清屏 |
| T-5 | `bootstrap_seed.py --reset` + `clear`，回到干净状态 |
| T-0 | 开始 |

---

## 2. 命令卡

### 终端 A · 基础设施（本机网络路径）

```bash
docker compose -f infra/docker-compose.yml up -d --wait --wait-timeout 300 postgres
```

```bash
powershell -ExecutionPolicy Bypass -File scripts\start_keycloak_native.ps1
```

> 能正常拉 `quay.io` 时，改用一条命令起两个：
> `docker compose -f infra/docker-compose.yml up -d --wait --wait-timeout 300 postgres keycloak`
> **两种方式二选一**，否则 8080 端口冲突。

### 终端 B · 数据与服务端

```bash
export PYTHONIOENCODING=utf-8
```

```bash
./.venv/Scripts/python.exe scripts/check_keycloak.py --wait 120
```

```bash
./.venv/Scripts/python.exe scripts/bootstrap_seed.py --reset
```

```bash
KB_AUTH_ENABLED=true KB_STORAGE_BACKEND=postgres ./.venv/Scripts/python.exe -m kb_mcp.server
```

> 这两个环境变量是硬要求：少了 `KB_AUTH_ENABLED=true` 就没有 401，
> 少了 `KB_STORAGE_BACKEND=postgres` 种子数据白灌（`.env` 里的默认值两种都不是）。

### 终端 C · 演示

```bash
export PYTHONIOENCODING=utf-8
```

```bash
./.venv/Scripts/python.exe scripts/smoke_auth.py
```

```bash
./.venv/Scripts/python.exe scripts/demo_portfolio.py
```

```bash
./.venv/Scripts/python.exe scripts/audit_report.py --limit 6
```

```bash
./.venv/Scripts/python.exe scripts/audit_report.py --user alice --result denied
```

> 想放慢节奏：`DEMO_PAUSE=2 ./.venv/Scripts/python.exe scripts/demo_portfolio.py`
> 时间紧：`DEMO_PAUSE=0 ...`

---

## 3. 每一段只说一句话

| 输出 | 一句话 |
|---|---|
| `[1] HTTP 401` | 没带令牌不是简单拒绝，而是按 RFC 9728 告诉客户端去哪办证 |
| `[2] 拿到 access token` | 走的是同一套授权码 + PKCE，不是从数据库偷令牌 |
| `[3] username: alice` | 参数里写了 `user_id: admin`，系统无视——身份只认令牌的 `sub` |
| `[4] N 条命中` | 只返回她有权限看的，过滤发生在 SQL 层 |
| `[5] owner: alice` | 归属自动来自 `sub`，工具参数里根本没有 owner 字段 |
| `[6] NOT_FOUND` | 文档其实存在；说"不存在"是为了不泄露存在性 |
| `[7] FORBIDDEN_SCOPE` | 登录了不够，还要有 `kb.admin` 这个权限 |
| `[8]` + 审计表 | 谁、何时、调什么、成败、耗时全在；且不含正文与令牌 |

**三个必讲重点**：① 没票进不来 ② 有票也越不过数据边界 ③ 留痕但不泄密。

---

## 4. 可选加演：真浏览器登录

```bash
npx -y @modelcontextprotocol/inspector
```

浏览器 `http://localhost:6274` → Transport 选 **Streamable HTTP** → URL
`http://localhost:8000/mcp` → **Client ID 手填 `mcp-inspector`** → Connect →
登录 `alice` / `password` → Tools → `whoami` → Run。

> Client ID 留空会走动态注册，Keycloak 默认拒绝，会看到 400。

---

## 5. 故障速查

| 现象 | 原因 | 处理 |
|---|---|---|
| `Connection refused` :8000 | Server 没起 / 端口不对 | 看终端 B 报错；`netstat -ano \| grep :8000` |
| `check_keycloak.py` 一直等 | Keycloak 还在导入 realm | 再等 30–60 秒，看终端 A 有无 `Imported realm kb` |
| `readyz` 的 `jwks` 不是 ok | Keycloak 未就绪 / `KB_ISSUER` 错 | 开 `http://localhost:8080/realms/kb` 看是否有 JSON |
| demo 第 2 步失败 | 登录配置不对 | 浏览器手动登录 alice/password 验证 |
| demo 第 6 步"没搜到销售部文档" | 种子没灌 / realm 重导入后 `sub` 变了 | `bootstrap_seed.py --reset` |
| 全是 401 | Server 没开 `KB_AUTH_ENABLED=true` | 停掉重起，带上变量 |
| 全是 503 | JWKS 拉不到 | 先修 Keycloak；这是设计好的依赖故障返回 |
| `RATE_LIMITED` / 429 | 60 次/分钟用完（彩排跑太多） | 等 1 分钟，或 `KB_RATE_LIMIT_PER_MINUTE=0` 重启 |
| 访问 `localhost:8080` 返回 `502 Bad Gateway` | 本机开了系统代理（Clash / v2ray / 公司代理），httpx 把 localhost 也走了代理 | 脚本已用 `trust_env=False` 修好；旧环境可临时 `export NO_PROXY=localhost,127.0.0.1` |
| 中文乱码 | 终端 GBK | `export PYTHONIOENCODING=utf-8` |
| 8080 被占 | Docker 版与原生版同时起了 | `docker compose -f infra/docker-compose.yml stop keycloak` |

---

## 6. 收尾

终端 A / B / C 各按一次 `Ctrl + C`。

```bash
docker compose -f infra/docker-compose.yml down
```

> 加 `-v` 会连数据库一起删，下次必须重灌种子。

---

## 7. 三条最容易搞错的点

1. **`.env` 默认是"不认证 + 内存存储"**，演示必须用命令行变量覆盖。
2. **`--reset` 不是可选项**：realm 重新导入后用户 `sub` 会变，不重置就对不上。
3. **闸门 ①② 是硬门槛**：过了再讲，没过先修。
