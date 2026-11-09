/**
 * 反馈2-2 P0：video-gen 详情页警告卡终态门控单测。
 *
 * 契约：
 * - 警告卡与「重新合成」按钮只在 ready/failed 渲染——谓词刻意对齐 recompose
 *   端点（routers/video_gen.py:1478），不含 cancelled（按钮动作对 cancelled 必 409）。
 * - 活跃态（pending/running/composing）+ retry_count>0 → 中性自动重试提示（无按钮）。
 * - cancelled 一律隐藏——cancelled 任务永远不会后台重试，显示"自动重试中"是误导
 *   （审查 P1：cancelled+retry_count>0 是真实可达组合）。
 *
 * 仓库无 @testing-library/react：沿用 video-gen-detail-cost.test.tsx 的
 * createRoot + act + happy-dom 手写 harness；hooks/子组件全 mock。
 *
 * 2026-09-13 复审补测：死链修复批（db348ceff）的假就绪提示块分支同文件追加
 * （artifact_missing === true 时提示块替换 <video>，不再打 /download 404）。
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

// 子组件与门控断言无关——stub 掉（AudioPreviewBar 自带 useGenerateAudio 等）
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
  // cost 测试的 baseTask 未含 retry_count；VideoTaskBrief 类型有该字段，带上
  // 避免 undefined 语义混入门控判断。
  retry_count: 0,
  created_at: "2026-07-24T09:00:00Z",
  updated_at: "2026-07-24T09:00:00Z",
};

describe("详情页警告卡终态门控（反馈2-2 P0）", () => {
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

  const warnResult = {
    ai_video_coverage_warning:
      "ai_video 部分帧生成失败并用占位补齐：7/27 成功（26%），建议重试失败帧",
  };

  it("running + retry_count>0 + 残留警告 → 无警告卡/无重新合成按钮，显示自动重试提示", async () => {
    h.taskQuery.data = {
      ...baseTask,
      status: "running",
      retry_count: 1,
      result: warnResult,
    };
    await renderDetail();
    expect(container.textContent).not.toContain("成片存在以下问题");
    expect(container.textContent).not.toContain("重新合成");
    const notice = container.querySelector('[data-testid="auto-retry-notice"]');
    expect(notice).toBeTruthy();
    expect(notice!.textContent).toContain("第 1 次");
  });

  it("running 首跑（retry_count=0）+ 残留警告 → 全部隐藏", async () => {
    h.taskQuery.data = {
      ...baseTask,
      status: "running",
      retry_count: 0,
      result: warnResult,
    };
    await renderDetail();
    expect(container.textContent).not.toContain("成片存在以下问题");
    expect(
      container.querySelector('[data-testid="auto-retry-notice"]'),
    ).toBeNull();
  });

  it("ready + 警告 → 卡片与「重新合成」按钮可见（既有行为保持）", async () => {
    h.taskQuery.data = { ...baseTask, status: "ready", result: warnResult };
    await renderDetail();
    expect(container.textContent).toContain("成片存在以下问题");
    expect(container.textContent).toContain("重新合成");
  });

  it("cancelled + 残留警告 → 隐藏（按钮动作对 cancelled 必 409）", async () => {
    h.taskQuery.data = {
      ...baseTask,
      status: "cancelled",
      result: warnResult,
    };
    await renderDetail();
    expect(container.textContent).not.toContain("成片存在以下问题");
    expect(
      container.querySelector('[data-testid="auto-retry-notice"]'),
    ).toBeNull();
  });

  it("cancelled + retry_count>0 + 残留警告 → 无自动重试提示（cancelled 不会后台重试）", async () => {
    // 审查 P1（2026-08-19）：真实可达组合——自动重试过一次（retry_count=1）→ 用户
    // 手动取消。修前草案的"非终态即提示"会显示"后台正在自动重试（第 1 次）"，但
    // cancelled 任务永远不会后台重试——UI 撒谎，与本 Task 要根治的 409 事故同型。
    h.taskQuery.data = {
      ...baseTask,
      status: "cancelled",
      retry_count: 1,
      result: warnResult,
    };
    await renderDetail();
    expect(container.textContent).not.toContain("成片存在以下问题");
    expect(
      container.querySelector('[data-testid="auto-retry-notice"]'),
    ).toBeNull();
    expect(container.textContent).not.toContain("自动重试");
  });
});

describe("详情页假就绪提示块（2026-09-13 死链修复 T4，复审补测）", () => {
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

  it("ready + artifact_missing=true → 提示块替换播放器，不再打 /download 404", async () => {
    h.taskQuery.data = { ...baseTask, status: "ready", artifact_missing: true };
    await renderDetail();
    const notice = container.querySelector(
      '[data-testid="artifact-missing-notice"]',
    );
    expect(notice).toBeTruthy();
    expect(notice!.textContent).toContain("产物文件已被清理或丢失");
    expect(container.querySelector('video[src*="/download"]')).toBeNull();
  });

  it("ready + artifact_missing 缺省（旧后端/其他域响应）→ 播放器路径保持（=== true 严格比较）", async () => {
    h.taskQuery.data = { ...baseTask, status: "ready" };
    await renderDetail();
    expect(
      container.querySelector('[data-testid="artifact-missing-notice"]'),
    ).toBeNull();
    expect(container.querySelector('video[src*="/download"]')).toBeTruthy();
  });
});
