# 把 MCP Server 变成 OAuth 2.1 资源服务器

> 面向已经跑通 MCP hello world、但还在用静态 API Key 的读者。
> 代码与结论来自一个真实项目：Keycloak 26.7 + PostgreSQL 16 + Python SDK 2.3.0。

## 为什么要 OAuth，而不是 API Key

静态 Key 的问题不在于"不安全"三个字，而在于它无法回答四个问题：**谁**在调用、能调用**哪些工具**、能看到**哪些数据**、出事后**怎么追**。

OAuth 2.1 把这些问题拆开：

- 身份由授权服务器签发（谁）；
- scope 表达权限（哪些工具）；
- token 的 `sub` 做数据过滤（哪些数据）；
- 每次调用写审计（怎么追）。

MCP 的价值在于"让 AI 客户端接入企业内部系统"，而企业内部系统的第一要求就是权限边界。所以 MCP Server 天然应该是 **Resource Server**。

## 第一步：告诉客户端去哪里登录

客户端第一次不带 token 访问 `/mcp`，服务端要返回 401，并用 `WWW-Authenticate` 指向资源元数据（RFC 9728）：

```http
HTTP/1.1 401 Unauthorized
WWW-Authenticate: Bearer error="invalid_token",
  error_description="Authentication required",
  resource_metadata="http://localhost:8000/.well-known/oauth-protected-resource/mcp"
```

元数据本身很短：

```json
{
  "resource": "http://localhost:8000/mcp",
  "authorization_servers": ["http://localhost:8080/realms/kb"],
  "bearer_methods_supported": ["header"],
  "scopes_supported": ["kb.read", "kb.write", "kb.admin"]
}
```

客户端拿到 `authorization_servers` 后，再走 OIDC discovery 找 `/authorize`、`/token`、`/certs`。这三步是标准化的，所以 Inspector、Python 客户端、TypeScript 客户端可以各自实现，却走同一套流程。

## 第二步：授权码 + PKCE，不要 secret

MCP 客户端大多是本地 CLI 或浏览器应用，**保管不了 client secret**。正确做法是 public client + PKCE：

```
code_verifier  = 43~128 位随机串
code_challenge = BASE64URL(SHA256(code_verifier))
```

授权请求带 `code_challenge` 与 `code_challenge_method=S256`，换取 token 时带 `code_verifier`。服务端只接受 `S256`，拒绝 `plain` 和缺失 verifier 的请求——这正是本项目安全测试 `S-11` 覆盖的内容。

回调地址用 loopback：`http://localhost:8765/callback`。RFC 8252 允许任意端口，但本项目在 Keycloak 里预注册了固定端口，换取更小的攻击面。

## 第三步：验签，别只看有没有 token

服务端拿到 JWT 后至少要做五件事：

```python
# 示意，实际实现见 apps/mcp-server/src/kb_mcp/auth.py
key = await jwks.get_signing_key(token)          # 带缓存
claims = jwt.decode(
    token,
    key,
    algorithms=["RS256"],                        # 只允许白名单算法
    issuer=settings.issuer,                      # 精确比较，含 realm 路径
    audience=settings.resource_url,              # aud 必须绑定本服务
    leeway=30,
    options={"require": ["exp", "iss", "aud", "sub"]},
)
```

几个容易忽略的细节：

1. **算法白名单**。只写 `algorithms=[...]`，不要相信 token 头部的 `alg`；`alg=none` 与 RS512 混淆都必须拒绝。
2. **`jku` 不支持**。如果按 token 里的 `jku` 去取密钥，攻击者可以把自己的 JWKS 塞进来。本项目只使用配置好的 JWKS URI，安全测试里甚至断言攻击者服务器**零请求**。
3. **区分"密钥服务挂了"和"token 非法"**。JWKS 拉取失败应该是 `503`，不是 `401`；否则合法用户会被误导去重新登录。
4. **issuer 精确匹配**。`http://localhost:8080/realms/kb` 与带尾斜杠的版本不是同一个字符串，Keycloak 不会替你修正。
5. **audience 必须绑定本服务**。给 A 服务签发的 token 不能拿来调 B 服务，这是 CVE-2026-116684 那一类资源混淆的根因。

## 第四步：scope 管工具，sub 管数据

这两件事必须分开做：

| 层 | 依据 | 失败结果 |
|---|---|---|
| 工具级授权 | token 的 `scope` | `403 FORBIDDEN_SCOPE` |
| 数据级授权 | token 的 `sub` + 文档 ACL | `404 NOT_FOUND` |

工具内部先校验 scope，再按 `sub` 过滤查询：

```python
require_scope(actor, "kb.admin")          # 没有 kb.admin 直接拒绝
document = await service.get(actor, repository, document_id=document_id)
```

越权读取统一返回 404 而不是 403，是为了不泄露"文档存在但你看不到"。安全测试 `S-09` 验证的正是这一点：bob 读 alice 的私人笔记，返回 `NOT_FOUND`，且响应体里没有任何标题或作者信息。

身份永远只从 token 取。`create_document` 的实现里根本没有 `owner` 参数：

```python
document = await repository.create(actor, title, content, visibility)
```

客户端传什么 `owner` 都没用——参数校验阶段就会被忽略或拒绝。这一条建议写进代码注释，避免后来的人"贴心"地加回一个可选参数。

## 第五步：审计与限流

审计行只存必要信息：

```text
actor_sub | tool_name | result(ok/denied/error) | error_code
args_digest(SHA-256) | duration_ms | request_id | created_at
```

不存 token、不存文档正文、不存密码。参数摘要用于事后比对"是不是同一次调用"，而不是还原内容。文档正文可能包含敏感信息，存了就是第二个泄露面。

限流按 `sub` 做 60 秒滑动窗口，超限返回 `429 RATE_LIMITED` 并写一条 `denied` 审计。进程内实现够用，多副本时要换 Redis——这条限制在 README 里写清楚，比假装它能扛住横向扩展更诚实。

## 错误码约定

| 场景 | HTTP | code |
|---|---|---|
| 未带 token / 验签失败 | 401 | `UNAUTHENTICATED` |
| scope 不足 | 403 | `FORBIDDEN_SCOPE` |
| 文档不存在或无权访问 | 404 | `NOT_FOUND` |
| 参数不合法 | 400 | `VALIDATION_ERROR` |
| 超出限流 | 429 | `RATE_LIMITED` |
| JWKS / 数据库不可用 | 503 | `SERVICE_UNAVAILABLE` |

统一错误码的好处是客户端可以按 code 分支处理，而不是解析中文错误信息。

## 负向测试才是验收标准

正向流程跑通只说明"能用"，负向测试才说明"安全"。本项目覆盖的 10 类威胁里，最有价值的几条：

- 把 `alg` 改成 `none`、把 RS256 换成 RS512 → 全部 401；
- 拿 alice 的 token 读 sales 部文档 → `NOT_FOUND`；
- 只读 token 调 `archive_document` → `FORBIDDEN_SCOPE`；
- 去掉 `code_verifier` 换 token → 400；
- 换一个未注册的 `redirect_uri` → 400；
- 同一用户 1 分钟内第 61 次调用 → `RATE_LIMITED`。

这些用例都放在 `apps/mcp-server/tests/security/`，其中 11 条打真实 HTTP 端点，能直接暴露中间件顺序、错误码映射这类"单测看不出来"的问题。

## 小结

把 MCP Server 做成资源服务器，核心只有一句话：**每个请求都独立回答"你是谁、你能做什么、你能看什么、你做了什么"**。OAuth 2.1 负责前两个问题，`sub` + ACL 负责第三个，审计负责第四个。剩下的都是工程细节。
