/**
 * 弹层统一（OBS-3，2026-10-09）：分镜删除确认从 window.confirm 换 useConfirm 弹层。
 *
 * 契约（两分支）：
 * - draft 态帧卡点删除 → 弹层出现（「删除分镜 N？」）；取消 → deleteFrame.mutate 未被调用；
 * - 确认 → mutate 以帧 index 调用。
 *
 * harness 沿用 storyboard-shot-direction.test.tsx（draft storyboard → 平铺 FrameCard）。
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  storyboardData: undefined as Record<string, unknown> | undefined,
  deleteFrameMut: { mutate: vi.fn(), isPending: false },
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ taskId: "vg_dc" }),
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
  useDeleteFrame: () => h.deleteFrameMut,
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

import { DialogProvider } from "@/components/workspace/dialog-provider";

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
  ],
};

describe("分镜删除确认弹层（OBS-3）", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    h.storyboardData = undefined;
    h.deleteFrameMut.mutate.mockClear();
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
            <VideoGenStoryboard taskId="vg_dc" />
          </DialogProvider>
        </QueryClientProvider>,
      );
    });
  }

  /** 帧卡内删除按钮是 Trash2 图标按钮——按 lucide svg class 定位。 */
  function trashButtonInCard(cardIndex: number): HTMLButtonElement | undefined {
    const card = container.querySelector(`#frame-card-${cardIndex}`);
    return Array.from(card?.querySelectorAll("button") ?? []).find((b) =>
      b.innerHTML.includes("trash2"),
    );
  }

  function buttonsWithText(text: string): HTMLButtonElement[] {
    return Array.from(document.querySelectorAll("button")).filter(
      (b) => b.textContent?.trim() === text,
    );
  }

  async function clickRadix(el: HTMLElement) {
    await act(async () => {
      el.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true }));
      el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
  }

  it("draft 态点删除 → 弹层出现；取消 → deleteFrame 未被调用", async () => {
    h.storyboardData = draftStoryboard;
    await renderStoryboard();

    const trash = trashButtonInCard(0);
    expect(trash).toBeTruthy();
    await clickRadix(trash!);

    expect(document.body.textContent).toContain("删除分镜 1？");
    expect(h.deleteFrameMut.mutate).not.toHaveBeenCalled();

    const cancel = buttonsWithText("取消")[0];
    expect(cancel).toBeTruthy();
    await clickRadix(cancel!);
    expect(h.deleteFrameMut.mutate).not.toHaveBeenCalled();
  });

  it("draft 态点删除 → 确认 → deleteFrame 以帧 index 调用", async () => {
    h.storyboardData = draftStoryboard;
    await renderStoryboard();

    await clickRadix(trashButtonInCard(1)!);
    expect(document.body.textContent).toContain("删除分镜 2？");

    const confirm = buttonsWithText("确定")[0];
    expect(confirm).toBeTruthy();
    await clickRadix(confirm!);
    expect(h.deleteFrameMut.mutate).toHaveBeenCalledTimes(1);
    expect(h.deleteFrameMut.mutate).toHaveBeenCalledWith(1);
  });
});
