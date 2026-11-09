/**
 * S6（2026-08-26 镜头调度表计划）：分镜帧卡片调度行只读显示。
 *
 * - 有表帧显示调度行小字「{景别} · {机位} · {人物位置}」（后端枚举中文术语映射）
 * - 无表帧（存量任务/插帧待生成行）不显示——零噪音降级
 * - 单帧重生按钮 title 补「景别与机位由调度表决定，不随重生变化（如需调整请整表重算）」
 *
 * harness 沿用 storyboard-insert-frame.test.tsx（仓库无 @testing-library/react）。
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DialogProvider } from "@/components/workspace/dialog-provider";

const h = vi.hoisted(() => ({
  storyboardData: undefined as Record<string, unknown> | undefined,
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ taskId: "vg_sd" }),
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
  useAddFrame: stubMut,
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
  config: { video_type: "ai_video" },
  frames: [
    {
      index: 0,
      narration: "第一帧",
      duration: 5,
      visual_url: null,
      image_prompt: "p0",
    },
    {
      index: 1,
      narration: "第二帧",
      duration: 5,
      visual_url: null,
      image_prompt: "p1",
    },
    {
      index: 2,
      narration: "第三帧（无调度行——模拟插帧待生成）",
      duration: 5,
      visual_url: null,
      image_prompt: "p2",
    },
  ],
  // S1 落盘帧绑定字段：帧 0/1 有行，帧 2 无行（插帧降级态）
  shot_table: [
    {
      frame_index: 0,
      shot_size: "wide",
      camera_move: "static",
      subject_position: "画面左三分之一",
      composition: "人物居左",
      continuity: "开场建立环境",
    },
    {
      frame_index: 1,
      shot_size: "medium",
      camera_move: "push_in",
      subject_position: "画面中间",
      composition: "对称构图",
      continuity: "承接上一镜站位",
    },
  ],
};

describe("S6: 调度行只读显示", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
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
            <VideoGenStoryboard taskId="vg_sd" />
          </DialogProvider>
        </QueryClientProvider>,
      );
    });
  }

  it("有表帧显示调度行小字「{景别} · {机位} · {人物位置}」（枚举中文术语）", async () => {
    h.storyboardData = draftStoryboard;
    await renderStoryboard();

    const card0 = container.querySelector("#frame-card-0");
    expect(card0).toBeTruthy();
    const text0 = card0?.textContent ?? "";
    expect(text0).toContain("远景");
    expect(text0).toContain("固定机位");
    expect(text0).toContain("画面左三分之一");
    // 调度行小字按「景别 · 机位 · 人物位置」格式拼接
    expect(text0).toContain("远景 · 固定机位 · 画面左三分之一");

    const card1 = container.querySelector("#frame-card-1");
    expect(card1?.textContent).toContain("中景 · 缓慢推近 · 画面中间");
  });

  it("无表帧不显示调度行（零噪音降级）；存量任务（无 shot_table）全部不显示", async () => {
    h.storyboardData = draftStoryboard;
    await renderStoryboard();
    const card2 = container.querySelector("#frame-card-2");
    expect(card2).toBeTruthy();
    const text2 = card2?.textContent ?? "";
    for (const term of ["远景", "中景", "固定机位", "缓慢推近"]) {
      // 无行帧不得出现调度术语（零噪音降级）
      expect(text2).not.toContain(term);
    }

    // 存量任务：storyboard 无 shot_table 键 → 所有帧零调度内容
    h.storyboardData = { ...draftStoryboard, shot_table: undefined };
    await renderStoryboard();
    for (const id of ["#frame-card-0", "#frame-card-1", "#frame-card-2"]) {
      expect(container.querySelector(id)?.textContent ?? "").not.toContain(
        "远景",
      );
      expect(container.querySelector(id)?.textContent ?? "").not.toContain(
        "固定机位",
      );
    }
  });

  it("单帧重生按钮 title 补「景别与机位由调度表决定」（仅覆盖帧）", async () => {
    h.storyboardData = draftStoryboard;
    await renderStoryboard();

    const card0 = container.querySelector("#frame-card-0");
    const marked0 = Array.from(card0?.querySelectorAll("button") ?? []).filter(
      (b) =>
        (b.getAttribute("title") ?? "").includes(
          "景别与机位由调度表决定，不随重生变化",
        ),
    );
    expect(marked0.length).toBeGreaterThan(0); // 重生按钮 title 须含调度表说明
    const withNote = marked0[0]!.getAttribute("title") ?? "";
    expect(withNote).toContain("如需调整请整表重算");

    const card2 = container.querySelector("#frame-card-2");
    const marked2 = Array.from(card2?.querySelectorAll("button") ?? []).filter(
      (b) => (b.getAttribute("title") ?? "").includes("景别与机位由调度表决定"),
    );
    // 无行帧不加调度表说明（零噪音）
    expect(marked2.length).toBe(0);
  });
});
