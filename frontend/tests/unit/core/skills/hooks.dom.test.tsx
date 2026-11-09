/**
 * DEF-2: useEnableSkill 失败必须 toast 发声。
 * 修前 api 层把非 2xx 当成功解析、hooks 层无 onError——生产 500（单文件
 * bind mount EBUSY）被双重静默，开关弹回且无任何提示。
 *
 * renderHook 手法沿用 tests/unit/core/video-gen/useSubmitVideo-error.test.tsx
 * 的既定约定（仓库未装 @testing-library/react）。
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { toast } from "sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { enableSkill } from "@/core/skills/api";
import { useEnableSkill } from "@/core/skills/hooks";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));
vi.mock("@/core/skills/api", () => ({
  enableSkill: vi.fn(),
}));
vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://localhost:8001",
}));
vi.mock("@/core/api/fetcher", () => ({ fetch: vi.fn() }));

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const mockEnableSkill = vi.mocked(enableSkill);

type EnableResult = ReturnType<typeof useEnableSkill>;

let latest: EnableResult | null = null;

function Probe() {
  // 测试探针沿仓内既定 renderHook 模式（见文件头注）；disable 见
  // AGENTS.md「新代码零新增命中」约定。
  // eslint-disable-next-line react-hooks/globals
  latest = useEnableSkill();
  return null;
}

function current(): EnableResult {
  if (!latest) throw new Error("probe not rendered");
  return latest;
}

describe("useEnableSkill（DEF-2：失败发声）", () => {
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
      defaultOptions: { mutations: { retry: false } },
    });
    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <Probe />
        </QueryClientProvider>,
      );
    });
  }

  it("api 抛错（500 detail）时 toast 展示服务端文案", async () => {
    mockEnableSkill.mockRejectedValueOnce(
      new Error("Failed to update skill (HTTP 500)"),
    );
    await renderProbe();

    await act(async () => {
      current().mutate({ skillName: "writer", enabled: false });
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(current().isError).toBe(true);
    expect(toast.error).toHaveBeenCalledWith(
      "Failed to update skill (HTTP 500)",
    );
  });

  it("非 Error 拒绝（网络层裸字符串）弹兜底文案", async () => {
    mockEnableSkill.mockRejectedValueOnce("network down");
    await renderProbe();

    await act(async () => {
      current().mutate({ skillName: "writer", enabled: false });
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(current().isError).toBe(true);
    expect(toast.error).toHaveBeenCalledWith("技能开关失败，请重试");
  });

  it("成功时不弹错误 toast", async () => {
    mockEnableSkill.mockResolvedValueOnce({} as never);
    await renderProbe();

    await act(async () => {
      current().mutate({ skillName: "writer", enabled: true });
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(toast.error).not.toHaveBeenCalled();
  });
});
