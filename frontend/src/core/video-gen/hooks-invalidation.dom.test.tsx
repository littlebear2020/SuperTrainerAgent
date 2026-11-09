/**
 * T13（F7，2026-09-28 edits 修复批）：useRecomposeTask / useReRenderTask 成功后
 * 均失效 裸列表键 [VIDEO_KEY] + [VIDEO_KEY, taskId] + [STORYBOARD_KEY, taskId]。
 * 修前 useRecomposeTask 只失效 [VIDEO_KEY, taskId]（列表徽标陈旧）；useReRenderTask
 * 缺裸列表键。模式沿用 hooks-storyboard.test.tsx。
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, type ReactElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetch } from "@/core/api/fetcher";
import {
  STORYBOARD_KEY,
  useRecomposeTask,
  useReRenderTask,
  VIDEO_KEY,
} from "@/core/video-gen/hooks";

vi.mock("@/core/api/fetcher", () => ({ fetch: vi.fn() }));
vi.mock("@/core/config", () => ({ getBackendBaseURL: () => "" }));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const mockFetch = vi.mocked(fetch);

function okRes(): Response {
  return {
    ok: true,
    status: 200,
    json: () => Promise.resolve({ task_id: "vg_1", status: "composing" }),
  } as unknown as Response;
}

type Invalidations = Array<{ queryKey: readonly unknown[] }>;

async function runHookCollectInvalidations(
  useHook: (taskId: string) => {
    mutate: (v: void, opts?: { onSuccess?: () => void }) => void;
  },
): Promise<Invalidations> {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const spy = vi.fn();
  const orig = qc.invalidateQueries.bind(qc);
  qc.invalidateQueries = (async (opts: { queryKey: readonly unknown[] }) => {
    spy(opts);
    return orig(opts);
  }) as typeof qc.invalidateQueries;

  const seen: { mutate: (v: void, opts?: { onSuccess?: () => void }) => void } =
    { mutate: () => undefined };
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  function Probe() {
    const m = useHook("vg_1");
    seen.mutate = m.mutate;
    return null;
  }
  await act(async () => {
    root.render(
      (
        <QueryClientProvider client={qc}>
          <Probe />
        </QueryClientProvider>
      ) as ReactElement,
    );
  });
  await act(async () => {
    seen.mutate(undefined, {});
  });
  await act(async () => undefined);
  act(() => root.unmount());
  container.remove();
  return spy.mock.calls.map((c) => c[0] as { queryKey: readonly unknown[] });
}

beforeEach(() => {
  mockFetch.mockReset();
  mockFetch.mockResolvedValue(okRes());
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("T13 recompose/re-render 缓存失效（F7）", () => {
  it("useRecomposeTask → 失效 列表键 + 任务键 + storyboard 键", async () => {
    const invs = await runHookCollectInvalidations(useRecomposeTask as never);
    const keys = invs.map((i) => JSON.stringify(i.queryKey));
    expect(keys).toContain(JSON.stringify([VIDEO_KEY]));
    expect(keys).toContain(JSON.stringify([VIDEO_KEY, "vg_1"]));
    expect(keys).toContain(JSON.stringify([STORYBOARD_KEY, "vg_1"]));
  });

  it("useReRenderTask → 同样三键（含裸列表键）", async () => {
    const invs = await runHookCollectInvalidations(useReRenderTask as never);
    const keys = invs.map((i) => JSON.stringify(i.queryKey));
    expect(keys).toContain(JSON.stringify([VIDEO_KEY]));
    expect(keys).toContain(JSON.stringify([VIDEO_KEY, "vg_1"]));
    expect(keys).toContain(JSON.stringify([STORYBOARD_KEY, "vg_1"]));
  });
});
