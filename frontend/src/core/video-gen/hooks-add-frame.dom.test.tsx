/**
 * 「在 N 镜后插入」Task 3：useAddFrame 的 body.index 直通 api.addFrame
 * （POST body JSON 序列化不丢 index）。组件层测试 mock 了 hooks，
 * fetch body 这一层须独立锁。模式沿用 hooks-regen-prompt.test.tsx。
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetch } from "@/core/api/fetcher";
import { VideoApiError } from "@/core/video-gen/api";
import { useAddFrame } from "@/core/video-gen/hooks";

vi.mock("@/core/api/fetcher", () => ({ fetch: vi.fn() }));
vi.mock("@/core/config", () => ({ getBackendBaseURL: () => "" }));

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const mockFetch = vi.mocked(fetch);

let latest: ReturnType<typeof useAddFrame> | null = null;
function Probe({ taskId }: { taskId: string }) {
  latest = useAddFrame(taskId);
  return null;
}

function asRes(obj: Record<string, unknown>): Response {
  return obj as unknown as Response;
}

describe("useAddFrame：body.index 透传（在 N 镜后插入）", () => {
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

  it("mutate 携带 index → POST /storyboard/frames body 原样含 index", async () => {
    mockFetch.mockResolvedValueOnce(
      asRes({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ status: "added" }),
      }),
    );
    const qc = new QueryClient({
      defaultOptions: { mutations: { retry: false } },
    });
    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <Probe taskId="vg_test" />
        </QueryClientProvider>,
      );
    });

    await act(async () => {
      await latest!.mutateAsync({ narration: "x", index: 2 });
    });

    expect(mockFetch).toHaveBeenCalledWith(
      "/api/video/tasks/vg_test/storyboard/frames",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ narration: "x", index: 2 }),
      }),
    );
  });

  it("失败时置 error（502，VideoApiError 透传后端 detail）", async () => {
    mockFetch.mockResolvedValueOnce(
      asRes({
        ok: false,
        status: 502,
        json: () => Promise.resolve({ detail: "bad gateway" }),
      }),
    );
    const qc = new QueryClient({
      defaultOptions: { mutations: { retry: false } },
    });
    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <Probe taskId="vg_test" />
        </QueryClientProvider>,
      );
    });

    let rejected: unknown = null;
    await act(async () => {
      rejected = await latest!
        .mutateAsync({ narration: "x" })
        .catch((e: unknown) => e);
      // 等 onError → state 更新的微任务链排空（hooks-regen-prompt.test.tsx 同款）
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(rejected).toBeDefined();
    // 标题承诺的「VideoApiError 透传后端 detail」必须兑现为断言——否则
    // api.ts 错误路径退化为吞 detail 只留通用文案时此用例照绿。
    expect(rejected).toBeInstanceOf(VideoApiError);
    expect((rejected as Error).message).toContain("bad gateway");
    expect((rejected as VideoApiError).status).toBe(502);
    expect(latest!.isError).toBe(true);
  });
});
