/**
 * Auth middleware: a static bearer gate, checked before any MCP traffic.
 *
 * This is the shipped default — simple, robust, and enough for a single-user
 * server whose token lives only in your MCP client config. The MCP spec's
 * fuller authorization story for remote servers is OAuth 2.1; to upgrade,
 * wrap the handler with Cloudflare's `workers-oauth-provider` (see README
 * "Harden it") — this middleware is the one seam to swap. Cloudflare Access
 * in front of the Worker is the zero-code belt-and-suspenders alternative.
 */
import type { Env } from "./types";

const encoder = new TextEncoder();

/** Constant-time equality via SHA-256 digests (uniform length and timing). */
async function tokensMatch(presented: string, expected: string): Promise<boolean> {
  const [a, b] = await Promise.all([
    crypto.subtle.digest("SHA-256", encoder.encode(presented)),
    crypto.subtle.digest("SHA-256", encoder.encode(expected)),
  ]);
  const av = new Uint8Array(a);
  const bv = new Uint8Array(b);
  let diff = 0;
  for (let i = 0; i < av.length; i++) {
    diff |= (av[i] ?? 0) ^ (bv[i] ?? 0);
  }
  return diff === 0;
}

function deny(status: number, error: string, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify({ error }), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

/**
 * Returns a Response to short-circuit with, or undefined to let the request
 * through. CORS preflights pass (they carry no credentials by design).
 */
export async function requireBearer(request: Request, env: Env): Promise<Response | undefined> {
  if (request.method === "OPTIONS") {
    return undefined;
  }
  const expected = env.SARA_MCP_TOKEN;
  if (!expected) {
    console.log(JSON.stringify({ event: "auth_unconfigured" }));
    return deny(503, "Server not configured: set the SARA_MCP_TOKEN secret.");
  }
  const header = request.headers.get("authorization") ?? "";
  const presented = header.startsWith("Bearer ") ? header.slice(7).trim() : "";
  if (!presented || !(await tokensMatch(presented, expected))) {
    console.log(JSON.stringify({ event: "auth_reject" }));
    return deny(401, "Unauthorized: present the bearer token.", {
      "www-authenticate": 'Bearer realm="personal-mcp"',
    });
  }
  return undefined;
}
