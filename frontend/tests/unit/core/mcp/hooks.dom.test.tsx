/**
 * DEF-2: useEnableMCPServer 失败必须 toast 发声（修前无 onError，500 静默）。
 * renderHook 手法沿用 tests/unit/core/video-gen/useSubmitVideo-error.test.tsx
 * 的既定约定（仓库未装 @testing-library/react）。
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { toast } from "sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { updateMCPConfig } from "@/core/mcp/api";
import { useEnableMCPServer } from "@/core/mcp/hooks";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));
vi.mock("@/core/mcp/api", () => ({
  loadMCPConfig: vi.fn().mockResolvedValue({
    mcp_servers: {
      metaso: { enabled: true },
    },
  }),
  updateMCPConfig: vi.fn(),
}));
vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://localhost:8001",
}));
vi.mock("@/core/api/fetcher", () => ({ fetch: vi.fn() }));

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const mockUpdate = vi.mocked(updateMCPConfig);

type EnableResult = ReturnType<typeof useEnableMCPServer>;

let latest: EnableResult | null = null;

function Probe() {
  // 测试探针沿仓内既定 renderHook 模式（见文件头注）；disable 见
  // AGENTS.md「新代码零新增命中」约定。
  // eslint-disable-next-line react-hooks/globals
  latest = useEnableMCPServer();
  return null;
}

function current(): EnableResult {
  if (!latest) throw new Error("probe not rendered");
  return latest;
}

describe("useEnableMCPServer（DEF-2：失败发声）", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.clearAllMocks();
    latest = null;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  async function renderProbe() {
    const qc = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <Probe />
        </QueryClientProvider>,
      );
    });
    // 等 useMCPConfig 查询解析出 config（mutationFn 依赖它）
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }

  it("api 抛错（500 detail）时 toast 展示服务端文案", async () => {
    mockUpdate.mockRejectedValueOnce(
      new Error("Failed to update MCP configuration"),
    );
    await renderProbe();

    await act(async () => {
      current().mutate({ serverName: "metaso", enabled: false });
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(current().isError).toBe(true);
    expect(toast.error).toHaveBeenCalledWith(
      "Failed to update MCP configuration",
    );
  });

  it("非 Error 拒绝（网络层裸字符串）弹兜底文案", async () => {
    mockUpdate.mockRejectedValueOnce("network down");
    await renderProbe();

    await act(async () => {
      current().mutate({ serverName: "metaso", enabled: false });
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(current().isError).toBe(true);
    expect(toast.error).toHaveBeenCalledWith("MCP 服务器开关失败，请重试");
  });
});
