/**
 * Auth: one owner secret (SARA_MCP_TOKEN), honored two ways.
 *
 * 1. OAuth 2.1 (primary — what claude.ai and the phone apps speak): the
 *    OAuthProvider in index.ts owns discovery, registration, and tokens;
 *    the consent screen (consent.ts) approves a client when the owner
 *    pastes this secret once. After that, real audience-bound tokens flow.
 * 2. Static bearer (kept for CLI/agents/curl): `Authorization: Bearer
 *    <SARA_MCP_TOKEN>` straight at /mcp. Wired through the provider's
 *    resolveExternalToken hook, which runs only after its own token lookup
 *    fails. The secret is locally minted, grants access only to this
 *    Worker, and is never forwarded anywhere — the GitHub PAT stays a
 *    separate Worker secret.
 *
 * Fail-closed: with SARA_MCP_TOKEN unset, both paths refuse.
 */
import type { Env } from "./types";

const encoder = new TextEncoder();

/** Constant-time equality via SHA-256 digests (uniform length and timing). */
async function timingSafeEqual(a: string, b: string): Promise<boolean> {
  const [av, bv] = (
    await Promise.all([
      crypto.subtle.digest("SHA-256", encoder.encode(a)),
      crypto.subtle.digest("SHA-256", encoder.encode(b)),
    ])
  ).map((buf) => new Uint8Array(buf)) as [Uint8Array, Uint8Array];
  let diff = 0;
  for (let i = 0; i < av.length; i++) {
    diff |= (av[i] ?? 0) ^ (bv[i] ?? 0);
  }
  return diff === 0;
}

/** Does `presented` match the configured owner token? Unset token = never. */
export async function ownerTokenMatches(
  presented: string | null | undefined,
  env: Env
): Promise<boolean> {
  if (!env.SARA_MCP_TOKEN || !presented) {
    return false;
  }
  return timingSafeEqual(presented, env.SARA_MCP_TOKEN);
}

/**
 * resolveExternalToken hook: accept the owner's static bearer at /mcp.
 * Returning null yields the provider's generic 401 challenge (which also
 * carries the resource-metadata pointer OAuth discovery needs).
 */
export async function resolveOwnerBearer(args: {
  token: string;
  env: Env;
}): Promise<{ props: Record<string, unknown> } | null> {
  if (!(await ownerTokenMatches(args.token, args.env))) {
    return null;
  }
  console.log(JSON.stringify({ event: "auth_static_bearer" }));
  return { props: { userId: "owner", authorizedVia: "static-bearer" } };
}
