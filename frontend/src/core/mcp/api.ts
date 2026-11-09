import { fetch } from "@/core/api/fetcher";
import { readFastApiErrorDetail } from "@/core/api/read-fastapi-error";
import { getBackendBaseURL } from "@/core/config";

import type { MCPConfig } from "./types";

export async function loadMCPConfig() {
  const response = await fetch(`${getBackendBaseURL()}/api/mcp/config`);
  return response.json() as Promise<MCPConfig>;
}

export async function updateMCPConfig(config: MCPConfig) {
  const response = await fetch(`${getBackendBaseURL()}/api/mcp/config`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(config),
  });
  // DEF-2: a non-2xx (e.g. 500 from a failed config write) must reject —
  // parsing the error body as success left failed toggles silently unapplied.
  if (!response.ok) {
    throw new Error(await readFastApiErrorDetail(response, "MCP 配置保存失败"));
  }
  return response.json();
}
