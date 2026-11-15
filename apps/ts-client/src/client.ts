/** MCP client wrapper: bearer token on every Streamable HTTP request. */
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

import type { Token } from "./oauth.js";

export const DEFAULT_SERVER_URL = "http://localhost:8000/mcp";

export interface ConnectedClient {
  client: Client;
  close: () => Promise<void>;
}

export async function connect(serverUrl: string, token: Token | null): Promise<ConnectedClient> {
  const client = new Client({ name: "kb-ts-client", version: "0.1.0" });
  const headers: Record<string, string> = {};
  if (token) {
    headers.Authorization = `Bearer ${token.accessToken}`;
  }
  const transport = new StreamableHTTPClientTransport(new URL(serverUrl), {
    requestInit: { headers },
  });
  await client.connect(transport);
  return { client, close: () => client.close() };
}

export type ToolPayload = Record<string, unknown>;

export class ToolFailure extends Error {
  constructor(
    public readonly tool: string,
    public readonly payload: ToolPayload,
  ) {
    super(`${tool} failed: ${JSON.stringify(payload)}`);
    this.name = "ToolFailure";
  }
}

export function normalizeResult(result: unknown): ToolPayload {
  const typed = result as {
    structuredContent?: ToolPayload;
    content?: Array<{ type: string; text?: string }>;
  };
  if (typed.structuredContent !== undefined) {
    return typed.structuredContent;
  }
  const texts = (typed.content ?? [])
    .filter((block) => block.type === "text" && typeof block.text === "string")
    .map((block) => block.text as string);
  return texts.length > 0 ? { content: texts } : {};
}

export async function callTool(
  client: Client,
  name: string,
  args: Record<string, unknown> = {},
): Promise<ToolPayload> {
  const result = await client.callTool({ name, arguments: args });
  const payload = normalizeResult(result);
  if ((result as { isError?: boolean }).isError) {
    throw new ToolFailure(name, payload);
  }
  return payload;
}
