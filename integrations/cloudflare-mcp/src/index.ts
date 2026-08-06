/**
 * Personal MCP — a read-only remote MCP server on Cloudflare Workers.
 *
 * Streamable HTTP at /mcp (the current MCP remote transport), gated by a
 * static bearer token (src/auth.ts — the seam to swap for OAuth 2.1 via
 * workers-oauth-provider when this ever serves more than you). Vault data
 * comes straight from its private GitHub repo (src/github.ts); nothing is
 * ever written anywhere.
 */
import { createMcpHandler } from "agents/mcp/server";
import { requireBearer } from "./auth";
import { buildServer } from "./server";
import type { Env } from "./types";

// One handler per isolate — bindings are stable for the isolate's lifetime.
let handler: ReturnType<typeof createMcpHandler> | undefined;

export default {
  async fetch(request, env, ctx): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === "/" && request.method === "GET") {
      return new Response(
        `${env.SERVER_NAME ?? "personal-mcp"}: MCP endpoint at POST /mcp (bearer required)\n`,
        { headers: { "content-type": "text/plain" } }
      );
    }
    const denied = await requireBearer(request, env);
    if (denied) {
      return denied;
    }
    handler ??= createMcpHandler(() => buildServer(env));
    return handler(request, env, ctx);
  },
} satisfies ExportedHandler<Env>;
