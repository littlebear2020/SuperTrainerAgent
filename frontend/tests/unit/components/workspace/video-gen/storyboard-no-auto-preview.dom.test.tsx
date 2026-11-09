/**
 * Task 21（前端 D4）：批量预览不再自动触发。
 *
 * 修复前：video-gen-storyboard.tsx 有个 useEffect（M1-2 遗留），draft 态且存在
 * 未预览帧时挂载后 1500ms 自动 previewAllMutate() —— 用户零操作即产生 N 次生图
 * + N 次 TTS 计费。打开一个 20 帧草稿看一眼就烧掉 20 次生图。
 *
 * 本测试锁的是「不自动发起」这一条不变量：推进 5s 假时钟后 previewAll
 * 必须仍未被调用；显式点「一键生成全部预览」按钮才发起。
 *
 * 仓库无 @testing-library/react：沿用 detail-draft-audio-polling.test.tsx 的
 * createRoot + act + happy-dom 手写 harness。
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DialogProvider } from "@/components/workspace/dialog-provider";

const h = vi.hoisted(() => ({
  previewAllMutate: vi.fn(),
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
  usePreviewAll: () => ({ mutate: h.previewAllMutate, isPending: false }),
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

// 子组件与「是否自动发起批量预览」无关——stub 掉，避免各自的 hooks 牵连
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

describe("Task 21: 批量预览不自动触发（前端 D4）", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.useFakeTimers();
    h.previewAllMutate.mockClear();
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

  it("挂载 draft 草稿（含未预览帧）后推进 5s，不得自动发起批量预览", async () => {
    h.storyboardData = draftStoryboard;
    await renderStoryboard();

    // 旧实现在 1500ms 时 fire；给到 5s 留足余量
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });

    expect(h.previewAllMutate).not.toHaveBeenCalled();
  });

  it("显式点「一键生成全部预览」并确认后才发起批量预览", async () => {
    h.storyboardData = draftStoryboard;
    await renderStoryboard();

    const btn = Array.from(container.querySelectorAll("button")).find((b) =>
      (b.textContent ?? "").includes("生成全部预览"),
    );
    expect(btn, "应存在显式的批量预览按钮").toBeTruthy();

    await act(async () => {
      btn!.click();
    });
    // Task 21 起点按钮只开确认框，此刻还不该计费
    expect(h.previewAllMutate).not.toHaveBeenCalled();

    const confirm = Array.from(document.querySelectorAll("button")).find((b) =>
      (b.textContent ?? "").trim().startsWith("确认生成"),
    );
    expect(confirm, "确认框应有确认按钮").toBeTruthy();
    await act(async () => {
      confirm!.click();
    });

    expect(h.previewAllMutate).toHaveBeenCalled();
  });

  // Task 21 后半：三个花钱操作（批量预览 / 开始渲染 / 重试合成）点下去即计费，
  // 修复前无任何二次确认。锁「先出确认框、确认后才发起」这条不变量。
  it("「一键生成全部预览」先弹确认框，取消则不发起", async () => {
    h.storyboardData = draftStoryboard;
    await renderStoryboard();

    const btn = Array.from(container.querySelectorAll("button")).find((b) =>
      (b.textContent ?? "").includes("生成全部预览"),
    );
    await act(async () => {
      btn!.click();
    });

    // 确认框应出现且此时尚未发起
    const dialogText = document.body.textContent ?? "";
    expect(dialogText, "应出现批量预览的二次确认文案").toMatch(/将消耗/);
    expect(h.previewAllMutate).not.toHaveBeenCalled();

    // 点「取消」——仍不得发起
    const cancel = Array.from(document.querySelectorAll("button")).find(
      (b) => (b.textContent ?? "").trim() === "取消",
    );
    expect(cancel, "确认框应有取消按钮").toBeTruthy();
    await act(async () => {
      cancel!.click();
    });
    expect(h.previewAllMutate).not.toHaveBeenCalled();
  });

  it("确认框点「确认生成」后才真正发起批量预览", async () => {
    h.storyboardData = draftStoryboard;
    await renderStoryboard();

    const btn = Array.from(container.querySelectorAll("button")).find((b) =>
      (b.textContent ?? "").includes("生成全部预览"),
    );
    await act(async () => {
      btn!.click();
    });

    const confirm = Array.from(document.querySelectorAll("button")).find((b) =>
      (b.textContent ?? "").includes("确认生成"),
    );
    expect(confirm, "确认框应有确认按钮").toBeTruthy();
    await act(async () => {
      confirm!.click();
    });

    expect(h.previewAllMutate).toHaveBeenCalled();
  });
});
