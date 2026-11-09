/**
 * D3（2026-08-19）：FrameCard「生成画面描述」接通后端 regenerate_prompt。
 * useRegenerateFramePrompt 成功后失效 storyboard 缓存、失败置 error。
 *
 * 注：仓库未装 @testing-library/react（tests/unit/core/voice-twin/hooks.test.ts
 * 头注既定约定），沿用 useSubmitVideo-error.test.tsx 的手写 renderHook 模式；
 * api 层 fetch 走 @/core/api/fetcher（非全局 fetch），按 api.test.ts 约定 mock。
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { toast } from "sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetch } from "@/core/api/fetcher";
import { useRegenerateFramePrompt } from "@/core/video-gen/hooks";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));
vi.mock("@/core/api/fetcher", () => ({
  fetch: vi.fn(),
}));
vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "",
}));

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const mockFetch = vi.mocked(fetch);

type RegenResult = ReturnType<typeof useRegenerateFramePrompt>;

let latest: RegenResult | null = null;

function Probe({
  taskId,
  onInvalidate,
}: {
  taskId: string;
  onInvalidate?: () => void;
}) {
  latest = useRegenerateFramePrompt(taskId, onInvalidate);
  return null;
}

function current(): RegenResult {
  if (!latest) throw new Error("probe not rendered");
  return latest;
}

function asRes(obj: Record<string, unknown>): Response {
  return obj as unknown as Response;
}

describe("useRegenerateFramePrompt（D3：生成画面描述）", () => {
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

  async function renderProbe(taskId: string, onInvalidate?: () => void) {
    const qc = new QueryClient({
      defaultOptions: { mutations: { retry: false } },
    });
    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <Probe taskId={taskId} onInvalidate={onInvalidate} />
        </QueryClientProvider>,
      );
    });
  }

  it("POST regenerate_prompt 并在成功后失效 storyboard", async () => {
    mockFetch.mockResolvedValueOnce(asRes({ ok: true, status: 200 }));
    const invalidate = vi.fn();
    await renderProbe("vg_test", invalidate);

    await act(async () => {
      await current().mutateAsync(3);
    });

    expect(mockFetch).toHaveBeenCalledWith(
      "/api/video/tasks/vg_test/storyboard/frames/3/regenerate_prompt",
      expect.objectContaining({ method: "POST" }),
    );
    expect(invalidate).toHaveBeenCalled();
  });

  it("失败时置 error（502）", async () => {
    mockFetch.mockResolvedValueOnce(
      asRes({
        ok: false,
        status: 502,
        json: () => Promise.resolve({ detail: "bad gateway" }),
      }),
    );
    await renderProbe("vg_test", vi.fn());

    let rejected: unknown = null;
    await act(async () => {
      rejected = await current()
        .mutateAsync(3)
        .catch((e: unknown) => e);
      // 等 onError → state 更新的微任务链排空（useSubmitVideo-error.test.tsx 同款）
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(rejected).toBeDefined();
    expect(current().isError).toBe(true);
  });
});
