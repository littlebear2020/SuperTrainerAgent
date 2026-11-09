/**
 * Task 13（FP1-10 + EP1-5 详情页侧）：
 * - FP1-10: draft 任务旁白音频在后台生成，但详情页只在 audioGenerating=true
 *   （本页点了「生成音频」）时才轮询分镜——他处创建的 draft 打开详情页，
 *   AudioPreviewBar 永远显示「旁白音频未准备」。修复：draft 且音频未就绪时
 *   持续轮询（storyboardRefetchInterval 在 audio_ready/audio_error 后自停）。
 * - EP1-5: 详情页「转场」行对 ai_image/ai_video 照显 payload.transition
 *   （如 fade），但网关 W1 强制 none——须显示诚实文案。
 *
 * 仓库无 @testing-library/react：沿用 useSubmitVideo-error.test.tsx 的
 * createRoot + act + happy-dom 手写 harness；hooks/子组件全 mock，
 * useStoryboardWithAudioPolling 用 spy 断言轮询开关实参。
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

// 子组件与轮询开关断言无关——stub 掉（AudioPreviewBar 自带 useGenerateAudio 等）
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

const draftAiImageTask = {
  id: "vg_1",
  user_id: "u1",
  task_type: "ai_image",
  status: "draft",
  progress: 100,
  payload: { transition: "fade", aspect: "9:16" },
  result: {},
  error_message: null,
  dirty_frames: [],
  course_project_id: null,
  created_at: "2026-07-24T09:00:00Z",
  updated_at: "2026-07-24T09:00:00Z",
};

const readyVimaxTask = {
  ...draftAiImageTask,
  task_type: "vimax",
  status: "ready",
};

describe("详情页 draft 音频轮询 + 转场诚实文案（FP1-10/EP1-5）", () => {
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

  it("draft 任务 audioGenerating=false 也持续轮询分镜（FP1-10）", async () => {
    h.taskQuery.data = draftAiImageTask;
    await renderDetail();

    expect(h.storyboardSpy).toHaveBeenCalled();
    const lastCall = h.storyboardSpy.mock.calls.at(-1)!;
    expect(lastCall[0]).toBe("vg_1");
    // 修前第二参恒为 audioGenerating=false → draft 音频就绪永不刷新
    expect(lastCall[1]).toBe(true);
  });

  it("draft ai_image 无转场行（EP1-5 → E 2026-08-31：转场 UI/详情行整体删除）", async () => {
    h.taskQuery.data = draftAiImageTask;
    await renderDetail();

    // payload 里的存量 transition 值不再渲染（删除键后死分支已清）
    expect(document.body.textContent).not.toContain("转场");
    expect(document.body.textContent).not.toContain(
      "无转场（已按音画同步优化）",
    );
  });

  it("ready vimax 不启用音频轮询、无转场行（对照：删除是全类型的）", async () => {
    h.taskQuery.data = readyVimaxTask;
    await renderDetail();

    const lastCall = h.storyboardSpy.mock.calls.at(-1)!;
    expect(lastCall[1]).toBe(false);
    expect(document.body.textContent).not.toContain("转场");
  });

  it("音频设置/字幕编辑位于 Basic info 之后、分镜条之前（布局归属分镜区）", async () => {
    // 回归（2026-07-27 布局割裂）：两 section 曾挂在 Basic info 之前，
    // 离分镜条一屏远——现要求紧邻分镜区下方、且不进入分镜块守卫内
    // （vimax 任务的字幕编辑器不得被 isSegmentedEngine 门隐藏）。
    h.taskQuery.data = {
      ...draftAiImageTask,
      status: "ready",
      dirty_frames: [0],
    };
    h.storyboardSpy.mockReturnValue({
      data: {
        config: {
          video_type: "ai_image",
          voice_id: "preset:146",
          tts_speed: 1.0,
          bgm_volume: 0.15,
        },
        frames: [
          { index: 0, narration: "n0", image_prompt: "p0", duration: 2 },
        ],
      },
    });
    const qc = new QueryClient();
    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <VideoGenDetailPage />
        </QueryClientProvider>,
      );
    });
    const q = (sel: string) =>
      container.querySelector(`[data-testid="${sel}"]`);
    const basic = q("basic-info-section");
    const audio = q("audio-settings-section");
    const sub = q("subtitle-edit-section");
    const strip = q("storyboard-strip-section");
    try {
      for (const el of [basic, audio, sub, strip]) {
        expect(el, "data-testid 锚点缺失").toBeTruthy();
      }
      // Basic info →（之后）分镜条 →（之后）音频设置/字幕编辑
      expect(
        basic!.compareDocumentPosition(strip!) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
      expect(
        strip!.compareDocumentPosition(audio!) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
      expect(
        strip!.compareDocumentPosition(sub!) & Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    } finally {
      // mockReturnValue 不被 mockClear 清除——try/finally 保证还原
      h.storyboardSpy.mockReturnValue({ data: undefined });
    }
  });

  it("音频设置默认折叠：头部摘要可见、控件不渲染；点头部展开后出现控件", async () => {
    // 产品评审 T3-P1 高度控制的落地语义：默认折叠只显示当前值摘要，
    // 编辑才展开。
    h.taskQuery.data = {
      ...draftAiImageTask,
      status: "ready",
      dirty_frames: [0],
    };
    h.storyboardSpy.mockReturnValue({
      data: {
        config: {
          video_type: "ai_image",
          voice_id: "preset:146",
          tts_speed: 1.2,
          bgm_volume: 0.15,
        },
        frames: [
          { index: 0, narration: "n0", image_prompt: "p0", duration: 2 },
        ],
      },
    });
    const qc = new QueryClient();
    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <VideoGenDetailPage />
        </QueryClientProvider>,
      );
    });
    const section = container.querySelector(
      '[data-testid="audio-settings-section"]',
    )!;
    try {
      expect(section).toBeTruthy();
      // 折叠态：头部摘要（含当前语速/BGM）可见，音色 select 不渲染
      expect(section.textContent).toContain("音频设置");
      expect(section.textContent).toContain("1.2x");
      expect(section.textContent).toContain("BGM 15%");
      expect(section.querySelector("select")).toBeNull();
      // 点头部展开 → 控件出现
      await act(async () => {
        section.querySelector("button")!.click();
      });
      expect(section.querySelector("select")).not.toBeNull();
    } finally {
      h.storyboardSpy.mockReturnValue({ data: undefined });
    }
  });
});
