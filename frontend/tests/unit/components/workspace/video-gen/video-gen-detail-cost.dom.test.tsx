/**
 * 反馈4（花费展示收尾）D1：video-gen 详情页花费行单测。
 *
 * 契约：
 * - cost=12.5 + currency="CNY" → Basic info 渲染「花费」行与「¥12.50」
 *   （formatCost 在 @/core/utils/format，CNY → ¥，两位小数）。
 * - cost=null → InfoRow value==null 短路 → **整行不渲染**（注意与 TaskCard
 *   的「—」占位行为不同，断言勿照抄）。
 *
 * 仓库无 @testing-library/react：沿用 detail-draft-audio-polling.test.tsx 的
 * createRoot + act + happy-dom 手写 harness；hooks/子组件全 mock。
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  storyboardSpy: vi.fn(
    (..._args: unknown[]) => ({ data: undefined }) as { data: unknown },
  ),
  taskQuery: {
    data: undefined as Record<string, unknown> | undefined,
    isLoading: false,
    isError: false,
  },
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ taskId: "vg_1" }),
  useRouter: () => ({ back: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
}));

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}));

vi.mock("@/core/video-gen/hooks", () => ({
  useVideoTask: () => h.taskQuery,
  useStoryboardWithAudioPolling: (...args: unknown[]) =>
    h.storyboardSpy(...args),
  useCourseProject: () => ({ data: undefined }),
  useRedoVimaxShot: () => ({ mutate: vi.fn(), isPending: false }),
  useRegenerateAllAudio: () => ({ mutate: vi.fn(), isPending: false }),
  useRecomposeTask: () => ({ mutate: vi.fn(), isPending: false }),
  useReRenderTask: () => ({ mutate: vi.fn(), isPending: false }),
  useRenderFromDraft: () => ({ mutate: vi.fn(), isPending: false }),
  useReorderFrames: () => ({ mutate: vi.fn(), isPending: false }),
  useTaskEvents: () => ({ previewFrames: new Map() }),
  useSubtitle: () => ({ data: undefined, isLoading: false }),
  useUpdateSubtitle: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateStoryboardConfig: () => ({ mutate: vi.fn(), isPending: false }),
  useVideoVoices: () => ({ data: [] }),
  useBGMList: () => ({ data: undefined }),
  getStallWarning: () => null,
  STORYBOARD_KEY: "video-storyboard",
  VIDEO_KEY: "video-task",
}));

vi.mock("@/core/video-gen/usePreviewTracker", () => ({
  usePreviewTracker: () => ({
    pending: new Set(),
    track: vi.fn(),
    release: vi.fn(),
  }),
}));

// 子组件与花费断言无关——stub 掉（AudioPreviewBar 自带 useGenerateAudio 等）
vi.mock("@/components/workspace/video-gen/audio-preview-bar", () => ({
  AudioPreviewBar: () => null,
}));
vi.mock("@/components/workspace/video-gen/scene-group-progress", () => ({
  SceneGroupProgress: () => null,
}));
vi.mock("@/components/workspace/video-gen/video-gen-frame-ops-panel", () => ({
  FrameOpsPanel: () => null,
}));
vi.mock("@/components/workspace/video-gen/video-gen-frame-strip", () => ({
  FrameStrip: () => null,
}));
vi.mock("@/components/workspace/video-gen/vimax-stage-indicator", () => ({
  VimaxStageIndicator: () => null,
}));

import { VideoGenDetailPage } from "@/components/workspace/video-gen/video-gen-detail";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const baseTask = {
  id: "vg_1",
  user_id: "u1",
  task_type: "ai_image",
  status: "ready",
  progress: 100,
  payload: { transition: "none", aspect: "9:16", style: "none" },
  result: {},
  error_message: null,
  dirty_frames: [],
  course_project_id: null,
  created_at: "2026-07-24T09:00:00Z",
  updated_at: "2026-07-24T09:00:00Z",
};

describe("详情页花费行（反馈4-D1）", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    h.storyboardSpy.mockClear();
    h.taskQuery.data = undefined;
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

  async function renderDetail() {
    const qc = new QueryClient();
    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <VideoGenDetailPage />
        </QueryClientProvider>,
      );
    });
  }

  it("cost=12.5 + CNY → 渲染「花费」行与「¥12.50」", async () => {
    h.taskQuery.data = { ...baseTask, cost: 12.5, currency: "CNY" };
    await renderDetail();

    const basic = container.querySelector(
      '[data-testid="basic-info-section"]',
    )!;
    expect(basic).toBeTruthy();
    expect(basic.textContent).toContain("花费");
    expect(basic.textContent).toContain("¥12.50");
  });

  it("cost=null → InfoRow 短路，整行不渲染（非「—」占位）", async () => {
    h.taskQuery.data = { ...baseTask, cost: null, currency: null };
    await renderDetail();

    const basic = container.querySelector(
      '[data-testid="basic-info-section"]',
    )!;
    expect(basic).toBeTruthy();
    expect(basic.textContent).not.toContain("花费");
    expect(basic.textContent).not.toContain("—");
  });

  it("进行中任务不渲染花费行（P3-1，对齐列表页 D5 闸）", async () => {
    // cost 非空也拦：recompose/重渲等会把 status 翻回 rendering 而保留上一轮
    // cost——此时展示旧值误导（列表页 D5 同款闸）。InfoRow 对 null 已短路，
    // 判别形态必须带非空 cost（计划样本 cost:null 修前亦不渲染、无从红起）。
    h.taskQuery.data = {
      ...baseTask,
      status: "rendering",
      cost: 12.5,
      currency: "CNY",
    };
    await renderDetail();

    const basic = container.querySelector(
      '[data-testid="basic-info-section"]',
    )!;
    expect(basic).toBeTruthy();
    expect(basic.textContent).not.toContain("花费");
  });
});
