# Keycloak 26 给 MCP 签 token 的三个坑

> 版本：Keycloak 26.7.4，realm 通过 `--import-realm` 导入。
> 这三个问题都在本项目里真实踩过，解决方式已写进 `infra/keycloak/realm-kb.json`。

## 背景

目标很简单：MCP Server 的地址是 `http://localhost:8000/mcp`，Keycloak 签发的 access token 必须满足：

- 有 `sub`（服务端用它识别用户）；
- 有 `aud = http://localhost:8000/mcp`（服务端用它拒绝串服务）；
- 有 `scope = kb.read` 等（服务端用它做工具级授权）；
- 有 `department` 与 `realm_access.roles`（审计与二次校验用）。

看起来都是标准动作，但 Keycloak 26 的领域模型和导入行为有几个反直觉的地方。

## 坑一：导入的 realm 没有内置 client scope

Keycloak 正常初始化时会创建 `basic`、`profile`、`email` 等内置 client scope，`basic` 里带 `oidc-sub-mapper`，负责把 `sub` 放进 token。

但用 `--import-realm` 导入一个只写了 client 和用户的 realm JSON 时，这些**内置 scope 不会自动生成**。结果就是：登录成功、token 也有，但 payload 里没有 `sub`——服务端只能拒绝。

解决方式是在 realm JSON 里显式定义：

```json
{
  "name": "basic",
  "protocol": "openid-connect",
  "attributes": {"include.in.token.scope": "true"},
  "protocolMappers": [
    {
      "name": "sub",
      "protocol": "openid-connect",
      "protocolMapper": "oidc-sub-mapper",
      "consentRequired": false,
      "config": {
        "access.token.claim": "true",
        "id.token.claim": "true"
      }
    }
  ]
}
```

验证方法：启动后直接解析 token 的 payload，看有没有 `sub`。本项目用 `scripts/get_token.py` 打印 claims，比在浏览器里翻 JWT 直观。

## 坑二：`fullScopeAllowed=false` 会丢掉 realm 角色

出于"最小权限"的直觉，很多人会把 client 的 `fullScopeAllowed` 设成 `false`，希望 token 只包含显式授权的角色。

实际行为是：realm 级角色（`realm_access.roles`）**不会**被自动包含进 token，除非在 client scope 里逐个映射。结果就是 token 里 `realm_access.roles` 为空，服务端基于角色的二次校验（例如"归档必须同时有 `kb.admin` scope 和 `admin` 角色"）全部失败。

本项目的选择是把 `fullScopeAllowed` 设为 `true`，但**工具权限仍然只由 scope 决定**。原因是角色在本项目里只用于审计展示与归档的二次校验，真正的授权边界是 scope + ACL。这个取舍写进了 README 的"已知限制"，避免读者误以为它是安全最佳实践。

如果你的系统确实依赖"角色必须最小化"，那就需要为每个角色建 mapper，工作量不小，但语义更严格。

## 坑三：audience 只能靠 per-scope mapper

OAuth 2.1 推荐客户端在授权请求里带 `resource`（RFC 8707），授权服务器据此把 `aud` 设成目标服务。Keycloak 26.7 对它的支持仍是实验特性，默认不启用，所以**不能依赖 `resource` 参数**。

最稳的方式是给每个 scope 配一个 audience mapper：

```json
{
  "name": "kb.read-audience",
  "protocol": "openid-connect",
  "protocolMapper": "oidc-audience-mapper",
  "config": {
    "included.custom.audience": "http://localhost:8000/mcp",
    "access.token.claim": "true",
    "id.token.claim": "false"
  }
}
```

三个 scopes（`kb.read` / `kb.write` / `kb.admin`）各自配一份，客户端申请哪个，token 里就带哪个 scope 对应的 `aud`。

这条设计还有一个隐含要求：**每个 MCP Server 要用自己的 scope 名**。如果两个服务共用 `kb.read`，同一个 token 就会同时带上两个 audience，串服务的风险又回来了。

## 其他值得记一笔的行为

**`start-dev` 不再内置 admin**。Keycloak 26 需要用环境变量创建：

```bash
KC_BOOTSTRAP_ADMIN_USERNAME=admin KC_BOOTSTRAP_ADMIN_PASSWORD=admin \
  kc.sh start-dev --import-realm
```

**Inspector 会自动申请 `offline_access`**。如果 client 的 optional scopes 里没有它，或者用户缺少对应的 realm 角色，就会看到：

```text
Offline tokens not allowed for the user or client
```

解决办法是把 `offline_access` 同时加到 client 的 optional scopes 与演示用户的 realm 角色里。项目里 3 个演示用户都配了。

**不填 Client ID 会先走 DCR**。Inspector 默认尝试动态注册，Keycloak 默认拒绝，报错信息是 `Dynamic Client Registration rejected`。在 Settings → OAuth Settings 里填上预注册的 `mcp-inspector` 即可。本项目有意不开 DCR：预注册 client 的 redirect URI 是白名单，攻击面更小。

## 一份可执行的验收清单

```bash
# 1. realm 与 discovery
python scripts/check_keycloak.py --wait 120

# 2. 真实授权码 + PKCE，打印 claims
python scripts/get_token.py

# 3. 断言 aud / sub / scope
python scripts/smoke_auth.py

# 4. 双客户端各自登录
python scripts/verify_clients.py
```

`check_keycloak.py` 会检查四件事：discovery 可用、issuer 与预期一致、`/authorize` 声明支持 `S256`、JWKS 里有 RSA 签名密钥。这四项任何一项不对，后面的步骤都不用看了。

## 小结

Keycloak 本身没问题，问题在于它的默认行为与"从零导入一个 realm"的期待之间有落差：内置 scope 不生成、角色被裁掉、`resource` 参数不可用。把这三点写进 realm JSON 和文档，配置就从"玄学"变成了可复现的工程。
