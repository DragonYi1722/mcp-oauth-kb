/**
 * Authorization code + PKCE (S256) for the `mcp-cli` public client.
 *
 * Node has no browser, so the flow uses a loopback redirect: the CLI starts a
 * one-shot HTTP server on 127.0.0.1, opens the system browser, and waits for
 * Keycloak to send the browser back with `?code=...&state=...`.
 */
import { createHash, randomBytes } from "node:crypto";
import { spawn } from "node:child_process";
import { chmod, mkdir, readFile, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

export interface Token {
  accessToken: string;
  refreshToken?: string;
  expiresAt: number;
  scope: string;
}

interface Discovery {
  issuer: string;
  authorization_endpoint: string;
  token_endpoint: string;
}

export interface OAuthOptions {
  issuer: string;
  clientId: string;
  redirectUri: string;
  scope: string;
}

export const DEFAULT_ISSUER = "http://localhost:8080/realms/kb";
export const DEFAULT_CLIENT_ID = "mcp-cli";
export const DEFAULT_REDIRECT_URI = "http://localhost:8765/callback";
export const DEFAULT_SCOPE = "openid kb.read kb.write kb.admin";

export function defaultTokenFile(): string {
  return process.env.KB_TS_TOKEN_FILE ?? join(homedir(), ".kb-client", "ts-token.json");
}

export function isExpired(token: Token, leewaySeconds = 30): boolean {
  return token.expiresAt <= Date.now() + leewaySeconds * 1000;
}

export async function loadToken(path: string): Promise<Token | null> {
  try {
    const parsed = JSON.parse(await readFile(path, "utf8")) as Partial<Token>;
    if (typeof parsed.accessToken !== "string") {
      return null;
    }
    return {
      accessToken: parsed.accessToken,
      refreshToken: parsed.refreshToken,
      expiresAt: typeof parsed.expiresAt === "number" ? parsed.expiresAt : 0,
      scope: typeof parsed.scope === "string" ? parsed.scope : "",
    };
  } catch {
    return null;
  }
}

export async function saveToken(path: string, token: Token): Promise<void> {
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, `${JSON.stringify(token, null, 2)}\n`, "utf8");
  try {
    await chmod(path, 0o600);
  } catch {
    // Windows ignores POSIX modes; the file still lives in the user's home.
  }
}

async function discovery(issuer: string): Promise<Discovery> {
  const base = issuer.replace(/\/$/, "");
  const response = await fetch(`${base}/.well-known/openid-configuration`);
  if (!response.ok) {
    throw new Error(`discovery failed with HTTP ${response.status}`);
  }
  const metadata = (await response.json()) as Discovery;
  if (metadata.issuer !== base) {
    throw new Error(`issuer mismatch: ${metadata.issuer}`);
  }
  return metadata;
}

function base64url(buffer: Buffer): string {
  return buffer.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function pkcePair(): { verifier: string; challenge: string } {
  const verifier = base64url(randomBytes(48));
  const challenge = base64url(createHash("sha256").update(verifier).digest());
  return { verifier, challenge };
}

async function waitForCode(redirectUri: string, expectedState: string): Promise<string> {
  const redirect = new URL(redirectUri);
  const port = Number(redirect.port || "80");

  return await new Promise<string>((resolve, reject) => {
    const server = createServer((request, response) => {
      const incoming = new URL(request.url ?? "/", `http://127.0.0.1:${port}`);
      if (incoming.pathname !== redirect.pathname) {
        response.writeHead(404).end("not found");
        return;
      }
      const error = incoming.searchParams.get("error");
      const state = incoming.searchParams.get("state");
      const code = incoming.searchParams.get("code");
      response.writeHead(200, { "Content-Type": "text/plain; charset=utf-8" });
      response.end("Login complete. You can close this tab.");
      server.close();

      if (error) {
        reject(new Error(`${error}: ${incoming.searchParams.get("error_description") ?? ""}`));
        return;
      }
      if (state !== expectedState) {
        reject(new Error("state mismatch; the callback may be forged"));
        return;
      }
      if (!code) {
        reject(new Error("callback did not include a code"));
        return;
      }
      resolve(code);
    });
    server.on("error", reject);
    server.listen(port, "127.0.0.1");
    server.setTimeout(300_000, () => {
      server.close();
      reject(new Error("timed out waiting for the browser callback"));
    });
  });
}

function openBrowser(url: string): void {
  const [command, args] =
    process.platform === "win32"
      ? ["cmd", ["/c", "start", "", url]]
      : process.platform === "darwin"
        ? ["open", [url]]
        : ["xdg-open", [url]];
  const child = spawn(command, args, { detached: true, stdio: "ignore" });
  child.on("error", () => undefined);
  child.unref();
}

async function tokenRequest(
  tokenEndpoint: string,
  form: Record<string, string>,
): Promise<Token> {
  const response = await fetch(tokenEndpoint, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      Accept: "application/json",
    },
    body: new URLSearchParams(form),
  });
  if (!response.ok) {
    throw new Error(`token endpoint returned ${response.status}: ${await response.text()}`);
  }
  const payload = (await response.json()) as {
    access_token: string;
    refresh_token?: string;
    expires_in?: number;
    scope?: string;
  };
  return {
    accessToken: payload.access_token,
    refreshToken: payload.refresh_token,
    expiresAt: Date.now() + (payload.expires_in ?? 0) * 1000,
    scope: payload.scope ?? "",
  };
}

export async function login(options: OAuthOptions, open = true): Promise<Token> {
  const metadata = await discovery(options.issuer);
  const { verifier, challenge } = pkcePair();
  const state = base64url(randomBytes(16));

  const authorize = new URL(metadata.authorization_endpoint);
  authorize.search = new URLSearchParams({
    client_id: options.clientId,
    redirect_uri: options.redirectUri,
    response_type: "code",
    scope: options.scope,
    state,
    code_challenge: challenge,
    code_challenge_method: "S256",
  }).toString();

  console.log(`Open this URL if the browser does not open:\n${authorize.toString()}\n`);
  const callback = waitForCode(options.redirectUri, state);
  if (open) {
    openBrowser(authorize.toString());
  }
  const code = await callback;

  return await tokenRequest(metadata.token_endpoint, {
    grant_type: "authorization_code",
    code,
    redirect_uri: options.redirectUri,
    client_id: options.clientId,
    code_verifier: verifier,
  });
}

export async function refresh(options: OAuthOptions, token: Token): Promise<Token> {
  if (!token.refreshToken) {
    throw new Error("no refresh token available; run `npm run dev -- login` again");
  }
  const metadata = await discovery(options.issuer);
  return await tokenRequest(metadata.token_endpoint, {
    grant_type: "refresh_token",
    client_id: options.clientId,
    refresh_token: token.refreshToken,
  });
}
