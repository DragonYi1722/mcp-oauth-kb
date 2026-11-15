#!/usr/bin/env node
/** CLI: `npm run dev -- whoami`, `npm run dev -- search 复盘`, ... */
import { parseArgs } from "node:util";

import { callTool, connect, DEFAULT_SERVER_URL, ToolFailure } from "./client.js";
import {
  DEFAULT_CLIENT_ID,
  DEFAULT_ISSUER,
  DEFAULT_REDIRECT_URI,
  DEFAULT_SCOPE,
  defaultTokenFile,
  isExpired,
  loadToken,
  login,
  refresh,
  saveToken,
  type OAuthOptions,
  type Token,
} from "./oauth.js";

const { values, positionals } = parseArgs({
  allowPositionals: true,
  options: {
    "server-url": { type: "string", default: process.env.KB_MCP_URL ?? DEFAULT_SERVER_URL },
    issuer: { type: "string", default: process.env.KB_ISSUER ?? DEFAULT_ISSUER },
    "client-id": { type: "string", default: DEFAULT_CLIENT_ID },
    "redirect-uri": { type: "string", default: DEFAULT_REDIRECT_URI },
    scope: { type: "string", default: DEFAULT_SCOPE },
    token: { type: "string" },
    "token-file": { type: "string" },
    "no-browser": { type: "boolean", default: false },
    limit: { type: "string", default: "20" },
    version: { type: "string" },
    content: { type: "string" },
    title: { type: "string" },
    visibility: { type: "string" },
    reason: { type: "string" },
    args: { type: "string", default: "{}" },
  },
});

const [command, ...rest] = positionals;
const tokenFile = values["token-file"] ?? defaultTokenFile();
const oauth: OAuthOptions = {
  issuer: values.issuer!,
  clientId: values["client-id"]!,
  redirectUri: values["redirect-uri"]!,
  scope: values.scope!,
};

async function resolveToken(): Promise<Token | null> {
  if (values.token) {
    return { accessToken: values.token, expiresAt: Date.now() + 3_600_000, scope: "" };
  }
  if (process.env.KB_ACCESS_TOKEN) {
    return {
      accessToken: process.env.KB_ACCESS_TOKEN,
      expiresAt: Date.now() + 3_600_000,
      scope: "",
    };
  }

  const cached = await loadToken(tokenFile);
  if (cached && !isExpired(cached)) {
    return cached;
  }
  if (cached?.refreshToken) {
    try {
      const renewed = await refresh(oauth, cached);
      await saveToken(tokenFile, renewed);
      return renewed;
    } catch (error) {
      console.error(`token refresh failed: ${(error as Error).message}`);
    }
  }
  if (values["no-browser"]) {
    throw new Error(`no usable token in ${tokenFile}; run the login command or pass --token`);
  }

  const fresh = await login(oauth, true);
  await saveToken(tokenFile, fresh);
  return fresh;
}

function requireValue(name: string): string {
  const value = rest[0];
  if (!value) {
    throw new Error(`missing argument: ${name}`);
  }
  return value;
}

async function dispatch(client: Awaited<ReturnType<typeof connect>>["client"]) {
  switch (command) {
    case "tools": {
      const tools = await client.listTools();
      return {
        tools: tools.tools.map((tool) => ({
          name: tool.name,
          description: tool.description,
          input_schema: tool.inputSchema,
        })),
      };
    }
    case "whoami":
      return await callTool(client, "whoami");
    case "search":
      return await callTool(client, "search_documents", {
        query: requireValue("query"),
        limit: Number(values.limit),
      });
    case "get":
      return await callTool(client, "get_document", {
        document_id: requireValue("document_id"),
      });
    case "create":
      if (!values.content) {
        throw new Error("create requires --content");
      }
      return await callTool(client, "create_document", {
        title: requireValue("title"),
        content: values.content,
        visibility: values.visibility ?? "department",
      });
    case "update": {
      if (!values.version) {
        throw new Error("update requires --version");
      }
      const args: Record<string, unknown> = {
        document_id: requireValue("document_id"),
        version: Number(values.version),
      };
      for (const key of ["title", "content", "visibility"] as const) {
        if (values[key] !== undefined) {
          args[key] = values[key];
        }
      }
      return await callTool(client, "update_document", args);
    }
    case "archive":
      return await callTool(client, "archive_document", {
        document_id: requireValue("document_id"),
        reason: values.reason ?? "archived from kb-ts-client",
      });
    case "call":
      return await callTool(client, requireValue("tool"), JSON.parse(values.args!));
  }
  throw new Error(`unknown command: ${command ?? "(none)"}`);
}

async function main(): Promise<number> {
  if (!command) {
    console.error(
      "usage: kb-ts-client [--token TOKEN] <login|tools|whoami|search|get|create|update|archive|call>",
    );
    return 1;
  }

  if (command === "login") {
    const token = await login(oauth, !values["no-browser"]);
    await saveToken(tokenFile, token);
    console.log(JSON.stringify({ saved: tokenFile, scope: token.scope }, null, 2));
    return 0;
  }

  const token = await resolveToken();
  const connection = await connect(values["server-url"]!, token);
  try {
    console.log(JSON.stringify(await dispatch(connection.client), null, 2));
    return 0;
  } finally {
    await connection.close();
  }
}

main()
  .then((code) => {
    process.exitCode = code;
  })
  .catch((error: unknown) => {
    if (error instanceof ToolFailure) {
      console.error(JSON.stringify(error.payload, null, 2));
      process.exitCode = 2;
      return;
    }
    console.error(`error: ${(error as Error).message}`);
    process.exitCode = 1;
  });
