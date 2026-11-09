/**
 * P1 / BUG-008：详情页两处 recompose 调用点（failed「重试合成」+ 降级面板「重新合成」）
 * 加 AlertDialog 确认弹层——修前无确认直调（分镜页有弹层、详情页没有）。
 *
 * 行为契约：
 * - 点按钮 → 弹层出现，mutate **未**发出（确认前零副作用）；
 * - 点「确认合成」→ mutate 恰发一次；取消 → 无副作用；
 * - failed 态弹层文案含「若提示缺素材/缺音频…编辑分镜」指引。
 *
 * 静态守卫（防回退）：
 * - detail 页所有 recomposeMut.mutate 调用必须包在 AlertDialogAction 内；
 * - handleRecomposeError 内不得出现 toast 调用（全局 onError 是唯一 toast 出口，
 *   P2-4 依赖此收敛——防双 toast 回退）。
 *
 * 仓库无 @testing-library/react：沿用 video-gen-detail-cost.test.tsx 的
 * createRoot + act + happy-dom 手写 harness；hooks/子组件全 mock。
 */

import { readFileSync } from "fs";
import { join } from "path";

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
  recomposeMutate: vi.fn(),
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
  useCostEstimate: () => ({
    data: undefined,
    isLoading: false,
    isError: false,
  }),
  useStoryboardWithAudioPolling: (...args: unknown[]) =>
    h.storyboardSpy(...args),
  useCourseProject: () => ({ data: undefined }),
  useRedoVimaxShot: () => ({ mutate: vi.fn(), isPending: false }),
  useRegenerateAllAudio: () => ({ mutate: vi.fn(), isPending: false }),
  useRecomposeTask: () => ({ mutate: h.recomposeMutate, isPending: false }),
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

// 子组件与确认弹层断言无关——stub 掉
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
  created_at: "2026-09-14T09:00:00Z",
  updated_at: "2026-09-14T09:00:00Z",
};

function findButton(text: string): HTMLButtonElement | undefined {
  return Array.from(document.querySelectorAll("button")).find(
    (b) => b.textContent === text,
  );
}

describe("详情页 recompose 确认弹层（P1/BUG-008）", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    h.storyboardSpy.mockClear();
    h.recomposeMutate.mockClear();
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
      "ai_video 部分帧生成失败并用占位补齐，建议重试失败帧",
  };

  it("failed 态：点「重试合成」→ 弹层出现、mutate 未发；确认 → 恰发一次", async () => {
    h.taskQuery.data = {
      ...baseTask,
      status: "failed",
      error_message: "TTS 阶段失败",
    };
    await renderDetail();

    const retryBtn = findButton("重试合成（渲染中断可断点续跑）");
    expect(retryBtn).toBeTruthy();
    await act(async () => {
      retryBtn!.dispatchEvent(
        new MouseEvent("click", { bubbles: true, cancelable: true }),
      );
    });

    // 确认前零副作用
    expect(h.recomposeMutate).not.toHaveBeenCalled();
    // 弹层在 body（Radix portal）
    expect(document.body.textContent).toContain("确认合成");
    // failed 态文案含缺素材/缺音频指引
    expect(document.body.textContent).toContain("缺素材/缺音频");

    const confirmBtn = findButton("确认合成");
    expect(confirmBtn).toBeTruthy();
    await act(async () => {
      confirmBtn!.dispatchEvent(
        new MouseEvent("click", { bubbles: true, cancelable: true }),
      );
    });
    expect(h.recomposeMutate).toHaveBeenCalledTimes(1);
  });

  it("ready 降级面板：点「重新合成」→ 弹层出现；确认前 mutation 未发", async () => {
    h.taskQuery.data = { ...baseTask, status: "ready", result: warnResult };
    await renderDetail();

    expect(container.textContent).toContain("成片存在以下问题");
    const reBtn = findButton("重新合成");
    expect(reBtn).toBeTruthy();
    await act(async () => {
      reBtn!.dispatchEvent(
        new MouseEvent("click", { bubbles: true, cancelable: true }),
      );
    });

    expect(h.recomposeMutate).not.toHaveBeenCalled();
    expect(document.body.textContent).toContain("确认合成");

    // 取消路径：无副作用
    const cancelBtn = findButton("取消");
    expect(cancelBtn).toBeTruthy();
    await act(async () => {
      cancelBtn!.dispatchEvent(
        new MouseEvent("click", { bubbles: true, cancelable: true }),
      );
    });
    expect(h.recomposeMutate).not.toHaveBeenCalled();
  });
});

describe("静态守卫：recompose 调用点收敛（P1/BUG-008 防回退）", () => {
  const detailSrc = readFileSync(
    join(
      process.cwd(),
      "src/components/workspace/video-gen/video-gen-detail.tsx",
    ),
    "utf-8",
  );

  it("所有 recomposeMut.mutate 调用必须包在确认弹层内（detail/列表页/分镜页三文件）", () => {
    // review-⑤（2026-09-14）：列表页 TaskCard 纳入（修前是唯一无确认直调的
    // 触发点）；分镜页 Task 21 既有弹层同检。
    // 2026-10-06 模块内合并批：三处弹层收敛为共享 RecomposeConfirmDialog
    // （其 AlertDialogAction 在 video-gen-recompose-dialog.tsx 内），调用方
    // 以 onConfirm 回调包 mutate——守卫口径同步：调用点上下文须见
    // RecomposeConfirmDialog（共享件）或 AlertDialogAction（历史内联形态）。
    const files = [
      "src/components/workspace/video-gen/video-gen-detail.tsx",
      "src/components/workspace/video-gen/video-gen-page.tsx",
      "src/components/workspace/video-gen/video-gen-storyboard.tsx",
    ];
    for (const f of files) {
      const src = readFileSync(join(process.cwd(), f), "utf-8");
      const occurrences = src.split("recomposeMut.mutate").length - 1;
      expect(occurrences, `${f} 应至少有一处调用`).toBeGreaterThanOrEqual(1);
      let idx = -1;
      for (let i = 0; i < occurrences; i++) {
        idx = src.indexOf("recomposeMut.mutate", idx + 1);
        const window = src.slice(Math.max(0, idx - 900), idx);
        expect(
          window.includes("AlertDialogAction") ||
            window.includes("RecomposeConfirmDialog"),
          `${f} 第 ${i + 1} 处 recomposeMut.mutate 前未见确认弹层（可能回退成直调）`,
        ).toBe(true);
      }
    }
  });

  it("F1 批量重生配音的自动重合成：同意标记钉死（弹层选项1）", () => {
    // 2026-09-28 edits 修复批 F1：AudioSettingsSection 用 regenAutoRecomposeMut
    // 在全帧重生终态后自动 recompose——同意在弹层选项1 捕获（「完成后自动重新合成」
    // 文案），不属「无确认直调」；此守卫防它漂移成第二处裸直调。
    const occurrences =
      detailSrc.split("regenAutoRecomposeMut.mutate").length - 1;
    expect(occurrences).toBeLessThanOrEqual(1);
    let idx = -1;
    for (let i = 0; i < occurrences; i++) {
      idx = detailSrc.indexOf("regenAutoRecomposeMut.mutate", idx + 1);
      const window = detailSrc.slice(Math.max(0, idx - 600), idx);
      expect(
        window.includes("弹层选项1 已获用户同意"),
        "自动重合成缺少同意标记（F1 弹层选项1）",
      ).toBe(true);
    }
  });

  it("detail 页触发按钮只开弹层（setConfirmRecompose），计数钉死为 3", () => {
    // 计划原文列两处（failed 重试 / 降级面板）；核查发现 detail 页还有第三处
    // （分镜条 recomposeActionFor 按钮）——同口径纳入（偏差清单已记）。
    // review-7a：≥3 收紧为恰 3——新增第 4 处触发点须经评审并同步改此守卫
    // （列表页第 4 处已单独加弹层，不在 detail 页）。
    const opens = detailSrc.split("setConfirmRecompose(true)").length - 1;
    expect(opens).toBe(3);
  });

  it("handleRecomposeError 内无 toast 调用（全局 onError 是唯一 toast 出口）", () => {
    const start = detailSrc.indexOf("const handleRecomposeError");
    expect(start).toBeGreaterThan(-1);
    const end = detailSrc.indexOf("const videoRef", start);
    const body = detailSrc.slice(start, end);
    expect(body).not.toContain("toast.");
  });
});
