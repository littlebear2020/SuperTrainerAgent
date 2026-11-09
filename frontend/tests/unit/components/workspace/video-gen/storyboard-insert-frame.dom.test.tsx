/**
 * 「在 N 镜后插入」Task 3：FrameCard 头部 + 按钮。
 * 锁三条不变量：仅 draft 渲染；点击第 N 镜的 + → mutate 携带 index=N+1 与占位旁白；
 * addFrame pending 时全部禁用（防连点 index 漂移）。
 * harness 沿用 storyboard-no-auto-preview.test.tsx（仓库无 @testing-library/react）。
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DialogProvider } from "@/components/workspace/dialog-provider";

const h = vi.hoisted(() => ({
  addFrameMutate: vi.fn(),
  addFramePending: false,
  storyboardData: undefined as Record<string, unknown> | undefined,
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ taskId: "vg_1" }),
  useRouter: () => ({ back: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
}));
vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
}));
const stubMut = () => ({ mutate: vi.fn(), isPending: false });
vi.mock("@/core/video-gen/hooks", () => ({
  useStoryboardWithAudioPolling: () => ({
    data: h.storyboardData,
    isLoading: false,
  }),
  useVideoTask: () => ({ data: undefined, isLoading: false, isError: false }),
  useCostEstimate: () => ({
    data: undefined,
    isLoading: false,
    isError: false,
  }),
  useCourseProject: () => ({ data: undefined }),
  usePreviewAll: stubMut,
  usePreviewFrame: stubMut,
  useAddFrame: () => ({
    mutate: (vars: unknown, opts?: { onSuccess?: () => void }) => {
      h.addFrameMutate(vars, opts);
      opts?.onSuccess?.();
    },
    isPending: h.addFramePending,
  }),
  useDeleteFrame: stubMut,
  useRenderFromDraft: stubMut,
  useReRenderTask: stubMut,
  useRecomposeTask: stubMut,
  useBulkDisableGraphic: stubMut,
  useUpdateFrameVisualSource: stubMut,
  useUpdateFrameLayout: stubMut,
  useUpdateStoryboardFrame: stubMut,
  useRegenerateFramePrompt: stubMut,
  useUploadFrameVisual: stubMut,
  getStallWarning: () => null,
  STORYBOARD_KEY: "video-storyboard",
  VIDEO_KEY: "video-task",
}));
vi.mock("@/core/video-gen/usePreviewTracker", () => ({
  usePreviewTracker: () => ({
    pending: new Set<number>(),
    track: vi.fn(),
    release: vi.fn(),
  }),
}));
vi.mock("@/components/workspace/video-gen/audio-preview-bar", () => ({
  AudioPreviewBar: () => null,
}));
vi.mock("@/components/workspace/video-gen/scene-group-card", () => ({
  SceneGroupCard: () => null,
}));
vi.mock("@/components/workspace/video-gen/scene-group-progress", () => ({
  SceneGroupProgress: () => null,
}));
vi.mock("@/components/workspace/video-gen/visual-source-switcher", () => ({
  VisualSourceSwitcher: () => null,
}));

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const draftStoryboard = {
  status: "draft",
  config: {},
  frames: [
    { index: 0, narration: "第一帧", duration: 5, visual_url: null },
    { index: 1, narration: "第二帧", duration: 5, visual_url: null },
    { index: 2, narration: "第三帧", duration: 5, visual_url: null },
  ],
};

describe("FrameCard「+ 在此镜后插入」", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    h.addFrameMutate.mockClear();
    h.addFramePending = false;
    h.storyboardData = undefined;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    document.body.innerHTML = "";
  });

  async function renderStoryboard() {
    const { VideoGenStoryboard } =
      await import("@/components/workspace/video-gen/video-gen-storyboard");
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <DialogProvider>
            <VideoGenStoryboard taskId="vg_1" />
          </DialogProvider>
        </QueryClientProvider>,
      );
    });
  }

  it("draft 态每张帧卡渲染 + 按钮；点第 2 镜的 + → mutate 携带 index=2（N+1）与占位旁白", async () => {
    h.storyboardData = draftStoryboard;
    await renderStoryboard();

    const btns = container.querySelectorAll<HTMLButtonElement>(
      'button[title="在此镜后插入分镜"]',
    );
    expect(btns.length).toBe(3);

    await act(async () => {
      btns[1]!.click(); // 第 2 镜（index 1）→ 插到 index 2（计数断言保证存在）
    });
    expect(h.addFrameMutate).toHaveBeenCalledTimes(1);
    // 只断言第 1 参：Task 4 会给 mutate 加第 2 参 per-call opts，此处不锁形状
    expect(h.addFrameMutate.mock.calls[0]![0]).toEqual({
      narration: "（新分镜，请编辑旁白）",
      index: 2,
    });
  });

  it("ready 态不渲染插入按钮（canEditFrames=false）", async () => {
    h.storyboardData = { ...draftStoryboard, status: "ready" };
    await renderStoryboard();

    expect(
      container.querySelectorAll('button[title="在此镜后插入分镜"]').length,
    ).toBe(0);
  });

  it("addFrame pending 时 + 按钮禁用（防连点 index 漂移）", async () => {
    h.storyboardData = draftStoryboard;
    h.addFramePending = true;
    await renderStoryboard();

    const btns = container.querySelectorAll<HTMLButtonElement>(
      'button[title="在此镜后插入分镜"]',
    );
    expect(btns.length).toBe(3);
    for (const b of btns) expect(b.disabled).toBe(true);
  });
});

describe("插入后定位（Task 4：滚动+高亮）", () => {
  let container: HTMLDivElement;
  let root: Root;
  // rAF/scrollIntoView 在 describe 级持引用：beforeEach stub、afterEach 还原
  let rafOriginal: typeof window.requestAnimationFrame;
  let scrollIntoView: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.useFakeTimers();
    // rAF 同步执行（fake timers 不代管 rAF，手动 stub 保证 effect 内滚动立即可断言）
    rafOriginal = window.requestAnimationFrame.bind(window);
    (window as unknown as Record<string, unknown>).requestAnimationFrame = (
      cb: FrameRequestCallback,
    ) => {
      cb(0);
      return 0;
    };
    scrollIntoView = vi.fn();
    (Element.prototype as unknown as Record<string, unknown>).scrollIntoView =
      scrollIntoView;
    h.addFrameMutate.mockClear();
    h.addFramePending = false;
    h.storyboardData = undefined;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    document.body.innerHTML = "";
    (window as unknown as Record<string, unknown>).requestAnimationFrame =
      rafOriginal;
    vi.useRealTimers();
  });

  async function renderStoryboard() {
    const { VideoGenStoryboard } =
      await import("@/components/workspace/video-gen/video-gen-storyboard");
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <DialogProvider>
            <VideoGenStoryboard taskId="vg_1" />
          </DialogProvider>
        </QueryClientProvider>,
      );
    });
  }

  it("插入成功且重拉落地后：scrollIntoView 定位新帧卡 + ring 高亮，2s 后消退", async () => {
    h.storyboardData = draftStoryboard;
    await renderStoryboard();

    const btn = container.querySelector<HTMLButtonElement>(
      'button[title="在此镜后插入分镜"]',
    )!;
    await act(async () => {
      btn.click(); // 第 1 镜后插入 → 新帧落 index 1
    });

    // 模拟 invalidate → 重拉：frames 多出一帧（index 1 为新帧）
    h.storyboardData = {
      ...draftStoryboard,
      frames: [
        { index: 0, narration: "第一帧", duration: 5, visual_url: null },
        {
          index: 1,
          narration: "（新分镜，请编辑旁白）",
          duration: 3,
          visual_url: null,
        },
        { index: 2, narration: "第二帧", duration: 5, visual_url: null },
        { index: 3, narration: "第三帧", duration: 5, visual_url: null },
      ],
    };
    await renderStoryboard(); // 重新 render 触发 effect

    expect(scrollIntoView).toHaveBeenCalled();
    const card = container.querySelector("#frame-card-1");
    expect(card, "新帧卡应带 frame-card-1 锚点 id").toBeTruthy();
    expect(card?.className).toContain("ring");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(
      container.querySelector("#frame-card-1")?.className,
      "2s 后高亮消退",
    ).not.toContain("ring");
  });

  it("连击竞态：第二次插入落地后，第一次的高亮 timer 不得提前清掉新定位", async () => {
    // 在既有 frames 的 at 位插入占位帧，>=at 的旧帧 index 顺移（模拟后端重拉结果）
    const withNewFrame = (
      frames: typeof draftStoryboard.frames,
      at: number,
    ) => {
      const next = frames.map((f) => ({ ...f }));
      next.forEach((f, i) => {
        if (i >= at) f.index = i + 1;
      });
      next.splice(at, 0, {
        index: at,
        narration: "（新分镜，请编辑旁白）",
        duration: 3,
        visual_url: null,
      });
      return next;
    };

    h.storyboardData = draftStoryboard;
    await renderStoryboard();

    const btns = () =>
      container.querySelectorAll<HTMLButtonElement>(
        'button[title="在此镜后插入分镜"]',
      );
    // 第一插：第 1 镜后 → 新帧 index 1；落地即高亮（timer A 于 t=0 启动）
    await act(async () => {
      btns()[0]!.click();
    });
    h.storyboardData = {
      ...draftStoryboard,
      frames: withNewFrame(draftStoryboard.frames, 1),
    };
    await renderStoryboard();
    expect(container.querySelector("#frame-card-1")?.className).toContain(
      "ring",
    );

    // t=1s（A 未到期）第二插：点第 4 镜（index 3）→ onSuccess 须清 A
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    const framesAfterFirst = h.storyboardData
      .frames as typeof draftStoryboard.frames;
    await act(async () => {
      btns()[3]!.click();
    });
    h.storyboardData = {
      ...draftStoryboard,
      frames: withNewFrame(framesAfterFirst, 4),
    };
    await renderStoryboard(); // 落地：timer B 于 t≈1s 启动

    // 推进到 t=2.2s：A 若未清会在 2.0s 清空高亮（bug）；正确行为是 B 未到期，新帧仍高亮
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1200);
    });
    expect(
      container.querySelector("#frame-card-4")?.className,
      "第二次插入的定位不得被第一次的 timer 清掉",
    ).toContain("ring");
  });
});
