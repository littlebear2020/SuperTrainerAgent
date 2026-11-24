/**
 * T13（F7，2026-09-28 edits 修复批）：useRecomposeTask / useReRenderTask 成功后
 * 均失效 裸列表键 [VIDEO_KEY] + [VIDEO_KEY, taskId] + [STORYBOARD_KEY, taskId]。
 * 修前 useRecomposeTask 只失效 [VIDEO_KEY, taskId]（列表徽标陈旧）；useReRenderTask
 * 缺裸列表键。模式沿用 hooks-storyboard.test.tsx。
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, type ReactElement } from "react";
import { createRoot } from "react-dom/client";
import { toast } from "sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetch } from "@/core/api/fetcher";
import {
  STORYBOARD_KEY,
  useBulkDisableGraphic,
  useRecomposeTask,
  useReRenderTask,
  useUpdateFrameLayout,
  useUpdateFrameVisualSource,
  VIDEO_KEY,
} from "@/core/video-gen/hooks";
import type { StoryboardData } from "@/core/video-gen/types";

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

// ── E7（2026-11-24 前端可靠性批，PR1）：乐观编辑失败回滚并提示 ──────────────

const OPTIMISTIC_PREV: StoryboardData = {
  title: "t",
  config: { video_type: "ai_image", width: 1080, height: 1920 },
  frames: [
    { index: 0, narration: "n0", visual_source: "ai_image", layout: "L0" },
    { index: 1, narration: "n1", visual_source: "graphic", layout: "L1" },
  ],
  total_duration: 6,
};

/**
 * 驱动一个带乐观写入的 mutation 走到失败：请求发出时记下缓存（证明乐观值确实
 * 写入过），settle 后再读缓存（应为回滚后的 prev）。
 */
async function runFailingOptimisticMutation<V>(
  useHook: (taskId: string) => { mutate: (v: V, opts?: object) => void },
  variables: V,
  failWith: () => Promise<Response>,
): Promise<{
  duringFetch: StoryboardData | undefined;
  after: StoryboardData | undefined;
}> {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  qc.setQueryData([STORYBOARD_KEY, "vg_1"], structuredClone(OPTIMISTIC_PREV));
  let duringFetch: StoryboardData | undefined;
  mockFetch.mockImplementationOnce(async () => {
    duringFetch = qc.getQueryData<StoryboardData>([STORYBOARD_KEY, "vg_1"]);
    return failWith();
  });

  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  let mutate: ((v: V, opts?: object) => void) | undefined;
  function Probe() {
    const m = useHook("vg_1");
    mutate = m.mutate as (v: V, opts?: object) => void;
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
    await new Promise<void>((resolve) =>
      mutate!(variables, { onSettled: () => resolve() }),
    );
  });
  const after = qc.getQueryData<StoryboardData>([STORYBOARD_KEY, "vg_1"]);
  act(() => root.unmount());
  container.remove();
  return { duringFetch, after };
}

function errorResponse(status: number, detail: string): Response {
  return {
    ok: false,
    status,
    json: async () => ({ detail }),
  } as unknown as Response;
}

const toastError = vi.mocked(toast.error);

describe("E7 乐观编辑失败回滚并提示（帧视觉来源 / 版式 / 批量停用图形）", () => {
  it("useUpdateFrameVisualSource 失败：回滚 ctx.prev，toast 恰好一次且带真实错误", async () => {
    const { duringFetch, after } = await runFailingOptimisticMutation(
      useUpdateFrameVisualSource,
      { frameIndex: 0, visualSource: "ai_video" },
      async () => errorResponse(500, "视觉来源写盘失败"),
    );
    expect(duringFetch?.frames[0]?.visual_source).toBe("ai_video");
    expect(after).toEqual(OPTIMISTIC_PREV);
    expect(toastError).toHaveBeenCalledTimes(1);
    expect(toastError.mock.calls[0]![0]).toContain("视觉来源写盘失败");
  });

  it("useUpdateFrameLayout 失败（网络层异常）：回滚 ctx.prev，toast 恰好一次", async () => {
    const { duringFetch, after } = await runFailingOptimisticMutation(
      useUpdateFrameLayout,
      { frameIndex: 1, layout: "ImageTextLeft" },
      async () => {
        throw new Error("Network down");
      },
    );
    expect(duringFetch?.frames[1]?.layout).toBe("ImageTextLeft");
    expect(after).toEqual(OPTIMISTIC_PREV);
    expect(toastError).toHaveBeenCalledTimes(1);
    expect(toastError.mock.calls[0]![0]).toContain("Network down");
  });

  it("useBulkDisableGraphic 失败：回滚 ctx.prev，toast 恰好一次", async () => {
    const { duringFetch, after } = await runFailingOptimisticMutation(
      useBulkDisableGraphic,
      undefined,
      async () => errorResponse(409, "任务正在合成"),
    );
    expect(duringFetch?.frames[1]?.visual_source).toBeNull();
    expect(after).toEqual(OPTIMISTIC_PREV);
    expect(toastError).toHaveBeenCalledTimes(1);
    expect(toastError.mock.calls[0]![0]).toContain("任务正在合成");
  });
});

describe("E7 调用组件不重复提示（toast 出口只在 hook 层）", () => {
  const componentSrc = (file: string) =>
    readFileSync(
      join(process.cwd(), "src/components/workspace/video-gen", file),
      "utf-8",
    );

  /** 取 `prefix(` 之后括号配平的实参区（含嵌套对象字面量）。 */
  function callArgs(src: string, prefix: string): string[] {
    const out: string[] = [];
    let from = 0;
    for (;;) {
      const at = src.indexOf(prefix, from);
      if (at === -1) return out;
      const open = at + prefix.length - 1;
      let depth = 0;
      for (let i = open; i < src.length; i++) {
        if (src[i] === "(") depth++;
        else if (src[i] === ")" && --depth === 0) {
          out.push(src.slice(open, i + 1));
          from = i + 1;
          break;
        }
      }
      if (depth !== 0) return out;
    }
  }

  it.each([
    [
      "video-gen-storyboard.tsx",
      ["sourceMut.mutate(", "layoutMut.mutate(", "bulkGraphicMut.mutate("],
    ],
    ["remotion-course-storyboard.tsx", ["updateLayout.mutate("]],
    ["remotion-course-frame-ops.tsx", ["updateLayout.mutate("]],
  ])(
    "%s 的 mutate 调用不带 onError（否则 hook 层 toast 之外再提示一次）",
    (file, calls) => {
      const src = componentSrc(file);
      for (const prefix of calls) {
        const args = callArgs(src, prefix);
        expect(args.length, `${file} 里找不到 ${prefix}(`).toBeGreaterThan(0);
        for (const a of args) {
          expect(a, `${file} 的 ${prefix} 实参里出现 onError`).not.toContain(
            "onError",
          );
        }
      }
    },
  );
});
