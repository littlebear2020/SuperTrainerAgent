import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://backend",
}));

import { updateMCPConfig } from "@/core/mcp/api";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("updateMCPConfig（DEF-2：非 2xx 必须 reject）", () => {
  it("rejects on 500 with the server detail", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({ detail: "Failed to update MCP configuration" }),
        { status: 500, headers: { "Content-Type": "application/json" } },
      ),
    );

    await expect(updateMCPConfig({ mcp_servers: {} } as never)).rejects.toThrow(
      "Failed to update MCP configuration",
    );
  });

  it("rejects on unparseable body with the fallback + status", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("bad gateway", { status: 502 }),
    );

    await expect(updateMCPConfig({ mcp_servers: {} } as never)).rejects.toThrow(
      "MCP 配置保存失败 (HTTP 502)",
    );
  });

  it("returns the parsed body on success", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ mcp_servers: {} }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const result = await updateMCPConfig({ mcp_servers: {} } as never);

    expect(result).toEqual({ mcp_servers: {} });
  });
});
