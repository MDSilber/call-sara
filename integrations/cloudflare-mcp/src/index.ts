/**
 * Personal MCP — a read-only remote MCP server on Cloudflare Workers.
 *
 * Streamable HTTP at /mcp (the current MCP remote transport), wrapped in
 * OAuth 2.1 via Cloudflare's workers-oauth-provider. The provider owns
 * discovery (/.well-known/*), open dynamic client registration (so
 * claude.ai's connector dialog works with its OAuth fields left EMPTY),
 * the token endpoint, and bearer validation on /mcp. Consent is the
 * single-owner paste-to-approve screen in src/consent.ts; the same owner
 * secret also works as a plain static bearer for CLI/agents/curl
 * (src/auth.ts, via resolveExternalToken). Vault data comes straight from
 * its private GitHub repo (src/github.ts); nothing is ever written
 * anywhere.
 */
import { OAuthProvider } from "@cloudflare/workers-oauth-provider";
import { createMcpHandler } from "agents/mcp/server";
import { resolveOwnerBearer } from "./auth";
import { handleAuthorize } from "./consent";
import { buildServer, SCOPES } from "./server";
import type { Env } from "./types";

// One MCP handler per isolate — bindings are stable for the isolate's lifetime.
let handler: ReturnType<typeof createMcpHandler> | undefined;

/** /mcp, invoked by the provider only after the bearer token verified.
 *  OAuth props reach tools through ctx (agents' getMcpAuthContext). */
const apiHandler = {
  fetch(request, env, ctx): Response | Promise<Response> {
    handler ??= createMcpHandler(() => buildServer(env));
    return handler(request, env, ctx);
  },
} satisfies ExportedHandler<Env>;

/** Everything unprotected: the consent screen and the banner. */
const defaultHandler = {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname === "/plaid-oauth") {
      // Plaid OAuth requires an HTTPS redirect URI; this route only bounces the
      // browser back to the local link server, carrying Plaid's query params.
      // No auth: the params are useless outside the initiating localhost session.
      return Response.redirect("http://localhost:8484/?" + url.searchParams.toString(), 302);
    }
    if (url.pathname === "/authorize") {
      return handleAuthorize(request, env);
    }
    if (url.pathname === "/" && request.method === "GET") {
      return new Response(
        `${env.SERVER_NAME ?? "personal-mcp"}: MCP endpoint at POST /mcp — ` +
          `OAuth (connect from claude.ai; approve once with the owner token) ` +
          `or a static Authorization: Bearer <owner token>.\n`,
        { headers: { "content-type": "text/plain" } }
      );
    }
    return new Response("Not found", { status: 404 });
  },
} satisfies ExportedHandler<Env>;

export default new OAuthProvider<Env>({
  apiRoute: "/mcp",
  apiHandler,
  defaultHandler,

  authorizeEndpoint: "/authorize",
  tokenEndpoint: "/oauth/token",
  // Open registration is what lets claude.ai register itself; a registered
  // client still gets nothing until the owner approves it on /authorize.
  clientRegistrationEndpoint: "/oauth/register",
  // The MCP-2026-preferred registration path (client_id = HTTPS metadata URL);
  // needs the global_fetch_strictly_public compatibility flag in wrangler.toml.
  clientIdMetadataDocumentEnabled: true,

  scopesSupported: SCOPES,

  // The owner's static bearer, accepted only after the provider's own token
  // lookup fails — keeps curl/CLI/agent configs working unchanged.
  resolveExternalToken: resolveOwnerBearer,
});
