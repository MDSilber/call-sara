/**
 * The MCP server, assembled from domain modules.
 *
 * Adding a domain is three steps (see README "Adding a domain"):
 *   1. write src/domains/<name>.ts exporting register<Name>Domain(server, env)
 *   2. import it here
 *   3. add it to DOMAINS
 * Tool names carry their domain as a prefix (finance_networth, ...).
 */
import { McpServer } from "@modelcontextprotocol/server";
import { registerFinanceDomain } from "./domains/finance";
import type { Env } from "./types";

const DOMAINS: ((server: McpServer, env: Env) => void)[] = [registerFinanceDomain];

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
