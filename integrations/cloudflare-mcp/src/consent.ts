/**
 * The /authorize consent screen — the single-owner approval scheme.
 *
 * There is exactly one user: you. So consent is "prove you're the owner":
 * the screen asks for SARA_MCP_TOKEN once, and a correct paste approves the
 * OAuth client (claude.ai, the iOS app, ...) for real audience-bound tokens
 * with refresh. No accounts, no passwords, no identity provider.
 *
 * Anyone can *register* a client (open dynamic registration is what lets
 * claude.ai's dialog work with its OAuth fields left empty) — registration
 * grants nothing. Only a consent POST carrying the owner token mints a
 * grant, so client-supplied strings rendered here (name, URIs) are treated
 * as hostile and escaped. CSRF needs no extra nonce: approval requires the
 * secret itself in the POST body, and there are no cookies to ride.
 */
import { AuthorizationError, type AuthRequest } from "@cloudflare/workers-oauth-provider";
import { ownerTokenMatches } from "./auth";
import { SCOPES } from "./server";
import type { Env } from "./types";

const NAME_MAX = 80;

function esc(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function page(title: string, body: string): Response {
  const html = `<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>${esc(title)}</title>
<style>
  :root { color-scheme: light dark; }
  body { font: 16px/1.5 system-ui, sans-serif; max-width: 26rem; margin: 4rem auto; padding: 0 1rem; }
  h1 { font-size: 1.2rem; }
  .card { border: 1px solid color-mix(in srgb, currentColor 25%, transparent); border-radius: 12px; padding: 1.25rem; }
  .muted { opacity: .7; font-size: .9rem; }
  .err { color: #c0392b; font-weight: 600; }
  input[type=password] { width: 100%; box-sizing: border-box; font-size: 1rem; padding: .5rem; margin: .75rem 0; }
  button { font-size: 1rem; padding: .5rem 1.25rem; cursor: pointer; }
  code { word-break: break-all; }
</style></head><body>${body}</body></html>`;
  return new Response(html, {
    status: 200,
    headers: { "content-type": "text/html; charset=utf-8" },
  });
}

function errorRedirect(req: AuthRequest, code: string): Response {
  const redirect = new URL(req.redirectUri);
  redirect.searchParams.set("error", code);
  if (req.state) redirect.searchParams.set("state", req.state);
  if (req.issuer) redirect.searchParams.set("iss", req.issuer);
  return Response.redirect(redirect.toString(), 302);
}

/** parseAuthRequest with the README's error contract (render locally unless
 * the client + exact redirect URI were validated). */
async function parseOrRespond(env: Env, request: Request): Promise<AuthRequest | Response> {
  try {
    // OAuth params ride the query string on both GET and POST (the form's
    // action="" preserves them) — parse from the URL deterministically.
    return await env.OAUTH_PROVIDER.parseAuthRequest(
      request.method === "GET" ? request : new Request(request.url, { method: "GET" })
    );
  } catch (error) {
    if (!(error instanceof AuthorizationError)) throw error;
    if (!error.redirectUri) {
      return page("Authorization error", `<h1>Authorization error</h1><p>${esc(error.description)}</p>`);
    }
    const redirect = new URL(error.redirectUri);
    redirect.searchParams.set("error", error.code);
    redirect.searchParams.set("error_description", error.description);
    if (error.state) redirect.searchParams.set("state", error.state);
    if (error.issuer) redirect.searchParams.set("iss", error.issuer);
    return Response.redirect(redirect.toString(), 302);
  }
}

async function renderForm(
  env: Env,
  oauthRequest: AuthRequest,
  serverName: string,
  wrongToken: boolean
): Promise<Response> {
  const client = await env.OAUTH_PROVIDER.lookupClient(oauthRequest.clientId);
  if (!client) {
    return page("Unknown client", "<h1>Unknown OAuth client</h1>");
  }
  const name = esc((client.clientName ?? "Unnamed client").slice(0, NAME_MAX));
  const origin = esc(new URL(oauthRequest.redirectUri).origin);
  const scopes = esc((oauthRequest.scope.length ? oauthRequest.scope : SCOPES).join(", "));
  const denyRaw = errorRedirect(oauthRequest, "access_denied").headers.get("location") ?? "/";
  // open registration means redirect URIs are hostile input: only http(s)
  // schemes may render as a link; anything else falls back to a dead end
  let denySafe = "/";
  try {
    const u = new URL(denyRaw, oauthRequest.redirectUri);
    if (u.protocol === "https:" || u.protocol === "http:") denySafe = u.toString();
  } catch { /* keep the safe fallback */ }
  const deny = esc(denySafe);
  return page(
    `Approve ${name}?`,
    `<div class="card">
<h1>${esc(serverName)}</h1>
<p><b>${name}</b> <span class="muted">(${origin})</span> wants read-only access
(<code>${scopes}</code>).</p>
<p class="muted">Paste the owner token (SARA_MCP_TOKEN) to approve. One paste
per client — tokens refresh on their own afterwards.</p>
${wrongToken ? '<p class="err">That token didn&#39;t match.</p>' : ""}
<form method="post" action="">
  <input type="password" name="owner_token" autocomplete="off" autofocus
         aria-label="Owner token" placeholder="owner token">
  <button type="submit">Approve</button>
  <a href="${deny}" style="margin-left:1rem">Deny</a>
</form>
</div>`
  );
}

/** GET (show the form) and POST (paste-to-approve) for the authorize endpoint. */
export async function handleAuthorize(request: Request, env: Env): Promise<Response> {
  const serverName = env.SERVER_NAME ?? "personal-mcp";
  const parsed = await parseOrRespond(env, request);
  if (parsed instanceof Response) {
    return parsed;
  }

  if (request.method === "GET") {
    return renderForm(env, parsed, serverName, false);
  }
  if (request.method !== "POST") {
    return new Response("Method not allowed", { status: 405 });
  }

  if (!env.SARA_MCP_TOKEN) {
    return page(
      "Not configured",
      "<h1>Server not configured</h1><p>Set the <code>SARA_MCP_TOKEN</code> secret first.</p>"
    );
  }
  const form = await request.formData();
  const presented = form.get("owner_token");
  if (typeof presented !== "string" || !(await ownerTokenMatches(presented, env))) {
    console.log(JSON.stringify({ event: "consent_reject", client: parsed.clientId }));
    return renderForm(env, parsed, serverName, true);
  }

  const client = await env.OAUTH_PROVIDER.lookupClient(parsed.clientId);
  const granted = parsed.scope.length
    ? parsed.scope.filter((s) => SCOPES.includes(s))
    : [...SCOPES];
  const { redirectTo } = await env.OAUTH_PROVIDER.completeAuthorization({
    request: parsed,
    userId: "owner",
    metadata: { clientName: client?.clientName ?? "unknown" },
    scope: granted,
    props: { userId: "owner", authorizedVia: "consent" },
  });
  console.log(JSON.stringify({ event: "consent_approve", client: parsed.clientId }));
  return Response.redirect(redirectTo, 302);
}
