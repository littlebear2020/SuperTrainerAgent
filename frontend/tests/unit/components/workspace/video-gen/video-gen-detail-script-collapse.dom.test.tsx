/**
 * 2026-08-20：video-gen 详情页「脚本/话题」卡片长文本收起/展开单测。
 *
 * 契约：
 * - 文本 >160 字符：默认收起（line-clamp-3）+ 标题行右侧「展开全部」按钮；
 *   点击后展开全文、按钮变「收起」，再点恢复。短文本（≤160）维持现状：
 *   无按钮、无 clamp。
 * - 文本取值保留空串穿透语义：String(payload.topic || payload.script || "")
 *   （|| 非 ??——topic 默认存空字符串，?? 会穿透成空白）。
 *
 * 仓库无 @testing-library/react：沿用 video-gen-detail-warning-gate.test.tsx 的
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

// 子组件与本测试无关——stub 掉（AudioPreviewBar 自带 useGenerateAudio 等）
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
  retry_count: 0,
  created_at: "2026-07-24T09:00:00Z",
  updated_at: "2026-07-24T09:00:00Z",
};

const LONG_SCRIPT = "这是一段足够长的脚本。".repeat(20); // 200 字符 > 160

function findButton(
  container: HTMLElement,
  text: string,
): HTMLButtonElement | null {
  for (const btn of Array.from(container.querySelectorAll("button"))) {
    if (btn.textContent?.includes(text)) return btn;
  }
  return null;
}

describe("详情页脚本/话题卡片收起/展开（2026-08-20）", () => {
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

  it("长脚本（>160 字符）：默认收起 3 行 + 「展开全部」按钮，点击展开/收起往返", async () => {
    h.taskQuery.data = {
      ...baseTask,
      payload: { ...baseTask.payload, script: LONG_SCRIPT },
    };
    await renderDetail();

    const paragraph = container.querySelector("p.whitespace-pre-wrap");
    expect(paragraph).toBeTruthy();
    expect(paragraph!.textContent).toBe(LONG_SCRIPT);
    expect(paragraph!.className).toContain("line-clamp-3");

    const expandBtn = findButton(container, "展开全部");
    expect(expandBtn).toBeTruthy();
    await act(async () => {
      expandBtn!.click();
    });
    expect(paragraph!.className).not.toContain("line-clamp-3");
    expect(findButton(container, "展开全部")).toBeNull();

    const collapseBtn = findButton(container, "收起");
    expect(collapseBtn).toBeTruthy();
    await act(async () => {
      collapseBtn!.click();
    });
    expect(paragraph!.className).toContain("line-clamp-3");
    expect(findButton(container, "展开全部")).toBeTruthy();
  });

  it("长话题（topic 优先）：标题为「话题」，同样收起/展开", async () => {
    h.taskQuery.data = {
      ...baseTask,
      payload: { ...baseTask.payload, topic: LONG_SCRIPT, script: "短脚本" },
    };
    await renderDetail();
    expect(container.textContent).toContain("话题");
    const paragraph = container.querySelector("p.whitespace-pre-wrap");
    // 空串穿透语义：topic 非空时优先 topic（|| 链）
    expect(paragraph!.textContent).toBe(LONG_SCRIPT);
    expect(paragraph!.className).toContain("line-clamp-3");
    expect(findButton(container, "展开全部")).toBeTruthy();
  });

  it("短脚本（≤160 字符）：无按钮、无 clamp（既有行为不变）", async () => {
    h.taskQuery.data = {
      ...baseTask,
      payload: {
        ...baseTask.payload,
        script: "一段不超过一百六十字的短脚本。",
      },
    };
    await renderDetail();
    const paragraph = container.querySelector("p.whitespace-pre-wrap");
    expect(paragraph).toBeTruthy();
    expect(paragraph!.className).not.toContain("line-clamp-3");
    expect(findButton(container, "展开全部")).toBeNull();
    expect(findButton(container, "收起")).toBeNull();
  });
});
