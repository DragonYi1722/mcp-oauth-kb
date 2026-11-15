# MCP 2026-07-28：没有 initialize 和 session 之后

> 这篇写于 2026 年 11 月，基于 MCP 2026-07-28 规范与官方 Python SDK `mcp==2.3.0`。
> 网上大量中文教程仍停留在 2025 版本，两者的请求形态差别很大，照抄会跑不通。

## 旧模型：一次握手，一条会话

2025 版的 MCP Streamable HTTP 大致是：

1. 客户端 `POST /mcp` 发 `initialize`，服务端返回 `Mcp-Session-Id`；
2. 之后每个请求都要带这个 session 头；
3. 服务端把会话状态（协商出的版本、客户端能力、订阅关系）存在内存里。

这套模型在本地没问题，一上多副本就暴露：负载均衡必须粘性会话，滚动发布时连接会掉，Serverless 环境根本没有稳定的内存可放会话。

## 新模型：每个请求自带上下文

2026-07-28 把无状态变成了默认形态：

- 不再需要 `initialize` 握手（进入 18 个月废弃期，SDK 仍会自动兼容老客户端）；
- 每个请求用 `Mcp-Method`、`Mcp-Name` 头表达路由意图；
- 协议版本和客户端能力放在 `params._meta` 里；
- 新增 `server/discover`，让客户端在调用工具前先了解服务端能力。

一个真实的 `tools/call` 请求长这样（本项目 `scripts/smoke_auth.py` 里的构造）：

```http
POST /mcp HTTP/1.1
Authorization: Bearer eyJhbGciOiJSUzI1NiIs...
Content-Type: application/json
Mcp-Method: tools/call
Mcp-Name: search_documents

{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "search_documents",
    "arguments": {"query": "复盘"},
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientCapabilities": {}
    }
  }
}
```

关键点：**请求自身携带全部上下文**，服务端不依赖任何会话表。任意一个副本收到请求都能直接处理，扩容不再需要粘性会话。

## 服务端怎么写

用官方 SDK，注册工具的部分和以前几乎一样，变化主要在启动参数：

```python
# apps/mcp-server/src/kb_mcp/server.py（节选）
from mcp.server import MCPServer

mcp = MCPServer("kb", token_verifier=verifier, auth=auth_settings)
# 工具通过 @mcp.tool() 注册，scope 校验写在工具内部
```

身份不是从请求参数里取的，而是从鉴权中间件写入的上下文里取：

```python
# apps/mcp-server/src/kb_mcp/tools/common.py
actor = current_actor()      # 由 TokenVerifier 验证后的 token 派生
require_scope(actor, "kb.read")
```

这样即使客户端在 `arguments` 里塞 `user_id: "admin"`，也不会影响任何查询——`whoami` 也一样，它读的是 token 的 claims。

## 客户端怎么写

Python SDK 2.x 的入口是 `streamable_http_client`，它返回读写两端：

```python
# apps/py-client/src/kb_client/client.py（节选）
read, write = await stack.enter_async_context(
    streamable_http_client(self.server_url, http_client=http)
)
session = await stack.enter_async_context(ClientSession(read, write))
await session.initialize()
```

有趣的是，`ClientSession.initialize()` 仍然存在。这不是矛盾：SDK 在新旧两代协议之间做自动协商——服务端支持 `server/discover` 就走新路径，否则回退到 `initialize`。

TypeScript 侧同样如此。截至本文写作时 npm 上最新的 `@modelcontextprotocol/sdk` 是 1.32.2，仍以 `initialize` 为主；本项目实测它可以正常连接 2026-07-28 的服务端。这一点务必写进文档，否则读者会以为必须升级到某个"2.x"。

## 三个容易踩的坑

**1. 只发 JSON 不带头**。不少旧代码把方法名只放在 body 里。新协议下路由依赖 `Mcp-Method` / `Mcp-Name`，缺头可能收到 400 或 405。

**2. 以为 `_meta` 可选**。协议版本在 `_meta` 里，缺了之后服务端只能按默认版本处理；跨版本调试时会出现"客户端说支持、服务端没识别"的怪现象。

**3. 忽略 DNS rebinding 防护**。SDK 默认只信任 localhost。部署到公网域名时必须在启动参数里显式 allowlist，否则浏览器侧请求会收到 421。本地演示一切正常、上线就 421，通常就是这里。

## 无状态带来的额外收益

去掉会话之后，安全模型反而更清晰：

- **鉴权与状态解耦**：每个请求都独立验签，不存在"登录后这个连接就一直是该用户"的隐式状态；
- **审计更完整**：每次调用都带 `request_id`，日志可以按请求聚合，不依赖会话生命周期；
- **压测与回放简单**：任意请求都能单独重放，安全测试可以直接对 HTTP 层做负向用例。

本项目的 31 项安全测试里，有 11 项是直接对 `/mcp` 发原始 HTTP 请求的，不需要建立任何会话——这在旧模型下反而更难写。

## 小结

2026-07-28 的核心不是加功能，而是**去掉隐式状态**。服务端只依赖三样东西：请求头里的路由信息、`_meta` 里的协商结果、`Authorization` 里的身份。把这三样想清楚，迁移并不难；难的是忘掉旧教程里的 session 直觉。
