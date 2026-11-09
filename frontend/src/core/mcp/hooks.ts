import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { loadMCPConfig, updateMCPConfig } from "./api";

// SEC-M16: GET /api/mcp/config is admin-only on the backend — non-admin
// callers pass enabled=false so the query never fires (no 403 noise).
export function useMCPConfig(enabled = true) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["mcpConfig"],
    queryFn: () => loadMCPConfig(),
    enabled,
  });
  return { config: data, isLoading, error };
}

export function useEnableMCPServer() {
  const queryClient = useQueryClient();
  const { config } = useMCPConfig();
  return useMutation({
    mutationFn: async ({
      serverName,
      enabled,
    }: {
      serverName: string;
      enabled: boolean;
    }) => {
      if (!config) {
        throw new Error("MCP config not found");
      }
      if (!config.mcp_servers[serverName]) {
        throw new Error(`MCP server ${serverName} not found`);
      }
      await updateMCPConfig({
        mcp_servers: {
          ...config.mcp_servers,
          [serverName]: {
            ...config.mcp_servers[serverName],
            enabled,
          },
        },
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["mcpConfig"] });
    },
    onError: (error) => {
      // DEF-2: surface failed saves (e.g. 500 from a failed config write) —
      // previously the switch UI gave no signal at all. updateMCPConfig
      // already produces a user-facing message; non-Error rejections fall back.
      toast.error(
        error instanceof Error ? error.message : "MCP 服务器开关失败，请重试",
      );
    },
  });
}
