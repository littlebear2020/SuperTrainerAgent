/**
 * 条目 34（2026-09-12 缺陷批）组件接线测试——复审二轮（2026-09-13）补齐。
 *
 * 计划「验证方式」原文：组件测试 mock storyboard 响应（无 preview_state +
 * visual_url 变化）断言结案+提示。修前只交付了 previewAll.test.ts 纯函数单测，
 * 漏了接线层：release effect 的旧后端探测分支（指纹变化 + preview_state 持续
 * 缺席 → 结案 + 页面级一次 toast）。
 *
 * Harness 沿用同目录 storyboard-no-auto-preview.test.tsx（createRoot + act，
 * 仓库刻意未装 @testing-library/react）。关键差异：useStoryboardWithAudioPolling
 * 的 mock **直接读真实 QueryClient 缓存**——trackPreview 的基线记录与乐观双清
 * （qc.setQueryData 清 preview_error/preview_state）必须与 effect 消费的数据
 * 同源，否则时序与生产分叉（静态 mock 数据会让双清失效、effect 立即误结案）。
 * usePreviewTracker 用真实现（pending Set 的 track/release 是被测闭环的一环）。
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, type ComponentType } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DialogProvider } from "@/components/workspace/dialog-provider";

const h = vi.hoisted(() => ({
  qc: undefined as QueryClient | undefined,
  previewFrameOpts: undefined as
    | { onMutate?: (frameIndex: number) => void }
    | undefined,
  toastError: vi.fn(),
  toastSuccess: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ taskId: "vg_1" }),
  useRouter: () => ({ back: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
}));

vi.mock("sonner", () => ({
  toast: { error: h.toastError, success: h.toastSuccess, info: vi.fn() },
}));

const stubMut = () => ({ mutate: vi.fn(), isPending: false });

vi.mock("@/core/video-gen/hooks", () => ({
  // 缓存同源：effect 消费的数据 = trackPreview 双清写入的缓存
  useStoryboardWithAudioPolling: (taskId: string) => ({
    data: h.qc?.getQueryData([STORYBOARD_KEY, taskId]),
    isLoading: false,
  }),
  useVideoTask: () => ({ data: undefined, isLoading: false, isError: false }),
  useCostEstimate: () => ({
    data: undefined,
    isLoading: false,
    isError: false,
  }),
  useCourseProject: () => ({ data: undefined }),
  usePreviewAll: () => ({ mutate: vi.fn(), isPending: false }),
  // 捕获 onMutate（= trackPreview）：测试以 fire onMutate(i) 模拟 POST 预览 202
  usePreviewFrame: (
    _taskId: string,
    opts: { onMutate?: (i: number) => void },
  ) => {
    h.previewFrameOpts = opts;
    return { mutate: vi.fn(), isPending: false };
  },
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

// 注意：不 mock @/core/video-gen/usePreviewTracker——release 闭环是被测行为。

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

// vi.mock 工厂内引用的常量须在其内可解析——提升后的 mock 用的是模块内常量名
const STORYBOARD_KEY = "video-storyboard";
const QUERY_KEY: readonly unknown[] = [STORYBOARD_KEY, "vg_1"];

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

type Frame = {
  index: number;
  narration: string;
  duration: number;
  visual_url: string | null;
  preview_state?: string | null;
  preview_error?: string | null;
};
type Board = {
  status: string;
  config: Record<string, unknown>;
  frames: Frame[];
};

function board(frames: Frame[]): Board {
  return { status: "draft", config: {}, frames };
}

describe("条目 34：旧后端预览写回的组件级接线", () => {
  let container: HTMLDivElement;
  let root: Root;
  let qc: QueryClient;
  let VideoGenStoryboard: ComponentType<{ taskId: string }>;

  beforeEach(async () => {
    h.toastError.mockClear();
    h.toastSuccess.mockClear();
    h.previewFrameOpts = undefined;
    qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    h.qc = qc;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    ({ VideoGenStoryboard } =
      await import("@/components/workspace/video-gen/video-gen-storyboard"));
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    qc.clear();
    container.remove();
    document.body.innerHTML = "";
  });

  function setBoard(updater: (old: Board | undefined) => Board | undefined) {
    qc.setQueryData<Board>(QUERY_KEY, updater);
  }

  async function rerender() {
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

  /** 模拟后端把 frame 写成「visual_url 变化且无 preview_state」（旧后端形态）。 */
  function legacyWriteback(frames: Frame[], index: number, url: string) {
    setBoard((old) => ({
      ...old!,
      frames: old!.frames.map((f) =>
        f.index === index
          ? {
              ...f,
              visual_url: url,
              preview_state: undefined,
              preview_error: undefined,
            }
          : f,
      ),
    }));
  }

  it("旧后端写回（指纹变化 + preview_state 持续缺席）→ 结案 + 页面级一次提示", async () => {
    // 两帧都带旧图 + done（用户对已有图的帧点「重新预览」是典型场景；同时验证
    // 提示只弹一次——页面级去重，不逐帧刷屏）
    setBoard(() =>
      board([
        {
          index: 0,
          narration: "第一帧",
          duration: 5,
          visual_url: "http://x/old0.png",
          preview_state: "done",
        },
        {
          index: 1,
          narration: "第二帧",
          duration: 5,
          visual_url: "http://x/old1.png",
          preview_state: "done",
        },
      ]),
    );
    await rerender();

    // 帧 0 发起预览（POST 202）：trackPreview 记基线（old0 指纹）+ 乐观双清缓存
    await act(async () => {
      h.previewFrameOpts?.onMutate?.(0);
    });
    // 双清后（preview_state 已清）不得立即结案——isPreviewResolved 为假
    await rerender();
    expect(h.toastSuccess).not.toHaveBeenCalled();
    expect(h.toastError).not.toHaveBeenCalled();

    // 旧后端写回：url 换新、无 preview_state → 结案 + 提示（第一次）
    legacyWriteback(
      qc.getQueryData<Board>(QUERY_KEY)?.frames ?? [],
      0,
      "http://x/new0.png",
    );
    await rerender();
    expect(h.toastError).toHaveBeenCalledTimes(1);
    expect(h.toastError).toHaveBeenCalledWith("后端需升级以获得预览状态跟踪");
    // 不得误走成功文案（那是正常状态机结案的路径）
    expect(h.toastSuccess).not.toHaveBeenCalledWith("分镜 1 画面已更新");

    // 帧 1 同样旧后端写回 → 结案但**不再**重复提示（页面级一次）
    await act(async () => {
      h.previewFrameOpts?.onMutate?.(1);
    });
    legacyWriteback(
      qc.getQueryData<Board>(QUERY_KEY)?.frames ?? [],
      1,
      "http://x/new1.png",
    );
    await rerender();
    expect(h.toastError).toHaveBeenCalledTimes(1);
  });

  it("新后端（preview_state 状态机在场）→ 不触发旧后端提示，走正常结案", async () => {
    setBoard(() =>
      board([
        {
          index: 0,
          narration: "第一帧",
          duration: 5,
          visual_url: "http://x/old0.png",
          preview_state: "done",
        },
      ]),
    );
    await rerender();

    await act(async () => {
      h.previewFrameOpts?.onMutate?.(0);
    });

    // 新后端：POST 202 后立即写 running（指纹未变也不误判）
    setBoard((old) => ({
      ...old!,
      frames: [{ ...old!.frames[0]!, preview_state: "running" }],
    }));
    await rerender();
    expect(h.toastError).not.toHaveBeenCalled();

    // running → done + url 更新 → 正常结案（成功文案），全程无旧后端提示
    setBoard((old) => ({
      ...old!,
      frames: [
        {
          ...old!.frames[0]!,
          visual_url: "http://x/new0.png",
          preview_state: "done",
        },
      ],
    }));
    await rerender();
    expect(h.toastSuccess).toHaveBeenCalledWith("分镜 1 画面已更新");
    expect(h.toastError).not.toHaveBeenCalledWith(
      "后端需升级以获得预览状态跟踪",
    );
  });
});
