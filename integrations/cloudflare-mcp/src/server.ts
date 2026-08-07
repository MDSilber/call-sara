/**
 * The MCP server, assembled from domain modules.
 *
 * Adding a domain is three steps (see README "Adding a domain"):
 *   1. write src/domains/<name>.ts exporting register<Name>Domain(server, env)
 *   2. import it here
 *   3. add it to DOMAINS
 * A domain contributes tools + resources under its prefix: tool names carry
 * it directly (finance_overview, ...), resource URIs namespace it as a
 * scheme (finance://thesis, finance://facts/{+path}). Computed answers are
 * tools, owner documents are resources, method rides in the domain's ask
 * tool.
 */
import { McpServer } from "@modelcontextprotocol/server";
import { registerFinanceDomain } from "./domains/finance";
import type { Env } from "./types";

const DOMAINS: ((server: McpServer, env: Env) => void)[] = [registerFinanceDomain];

/** OAuth scopes this server publishes — one read scope per domain. */
export const SCOPES: string[] = ["finance:read"];

export function buildServer(env: Env): McpServer {
  const server = new McpServer({
    name: env.SERVER_NAME ?? "personal-mcp",
    version: "1.0.0",
  });
  for (const register of DOMAINS) {
    register(server, env);
  }
  return server;
}
