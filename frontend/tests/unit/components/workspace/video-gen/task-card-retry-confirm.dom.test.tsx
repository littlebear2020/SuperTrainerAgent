/**
 * review-⑤/⑥（2026-09-14）：列表页 TaskCard 的 failed「重试合成」——
 * 修前是全仓唯一无确认直调的 recompose 触发点，且 hook 实例未传 opts
 * （409 时 toast 无「去编辑分镜」action 按钮）。
 *
 * 契约：
 * - 点「重试合成」→ 弹确认层（文案对齐 detail 页：含缺素材/缺音频指引），
 *   mutate 未发；取消 → 零副作用；确认 → 恰发一次。
 * - useRecomposeTask 收到 opts.onEditStoryboard → 调 onNavigate(id, "storyboard")。
 *
 * 仓库无 @testing-library/react：createRoot + act + happy-dom 手写 harness。
 */

import { createElement, act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  onNavigate: vi.fn(),
  recomposeMutate: vi.fn(),
  recomposeOpts: undefined as { onEditStoryboard?: () => void } | undefined,
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), back: vi.fn(), refresh: vi.fn() }),
}));

// video-gen-page.tsx 模块级导入面（渲染 TaskCard 须整模块 mock）
vi.mock("@/core/video-gen/hooks", () => ({
  useBGMList: () => ({ data: undefined }),
  useProviders: () => ({ data: [] }),
  useBGMUpload: () => ({ mutate: vi.fn() }),
  useCourseProjects: () => ({ data: undefined }),
  useDefaultVoice: () => ({ data: undefined }),
  useDeleteVideoTask: () => ({ mutate: vi.fn() }),
  useExtractScript: () => ({ mutate: vi.fn() }),
  useRecomposeTask: (_id: string, opts?: { onEditStoryboard?: () => void }) => {
    h.recomposeOpts = opts;
    return { mutate: h.recomposeMutate, isPending: false };
  },
  useSubmitVideo: () => ({ mutate: vi.fn(), isPending: false }),
  useVideoTasks: () => ({ data: [] }),
  useVideoVoices: () => ({ data: [] }),
  getStallWarning: () => null,
}));

import { TaskCard } from "@/components/workspace/video-gen/video-gen-page";
import type { VideoTaskBrief } from "@/core/video-gen/types";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const failedTask: VideoTaskBrief = {
  id: "vg_t1",
  user_id: "u1",
  domain: "video_gen",
  task_type: "ai_image",
  status: "failed",
  progress: 85,
  title: "测试任务",
  error_message: "ffmpeg timeout",
  retry_count: 0,
  dirty_frames: [],
  created_at: "2026-07-22T09:00:00Z",
  updated_at: "2026-07-22T10:00:00Z",
  thumbnail_url: null,
  cost: null,
  currency: null,
};

function findButton(text: string): HTMLButtonElement | undefined {
  return Array.from(document.querySelectorAll("button")).find(
    (b) => b.textContent === text,
  );
}

function click(el: Element) {
  el.dispatchEvent(
    new MouseEvent("click", { bubbles: true, cancelable: true }),
  );
}

describe("列表卡片 failed 重试确认弹层（review-⑤/⑥）", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    h.recomposeMutate.mockClear();
    h.onNavigate.mockClear();
    h.recomposeOpts = undefined;
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

  async function renderCard() {
    await act(async () => {
      root.render(
        createElement(TaskCard, {
          task: failedTask,
          onDelete: () => undefined,
          previewTaskId: null,
          setPreviewTaskId: () => undefined,
          onNavigate: h.onNavigate,
        }),
      );
    });
  }

  it("点「重试合成」→ 弹层出现、mutate 未发；取消零副作用；确认 → 恰发一次", async () => {
    await renderCard();

    const retryBtn = findButton("重试合成");
    expect(retryBtn).toBeTruthy();
    await act(async () => {
      click(retryBtn!);
    });

    // 确认前零副作用；弹层文案对齐 detail 页（含缺素材/缺音频指引）
    expect(h.recomposeMutate).not.toHaveBeenCalled();
    expect(document.body.textContent).toContain("确认合成");
    expect(document.body.textContent).toContain("缺素材/缺音频");

    // 取消路径：无副作用
    const cancelBtn = findButton("取消");
    expect(cancelBtn).toBeTruthy();
    await act(async () => {
      click(cancelBtn!);
    });
    expect(h.recomposeMutate).not.toHaveBeenCalled();

    // 重开 → 确认 → 恰一次
    await act(async () => {
      click(retryBtn!);
    });
    const confirmBtn = findButton("确认合成");
    expect(confirmBtn).toBeTruthy();
    await act(async () => {
      click(confirmBtn!);
    });
    expect(h.recomposeMutate).toHaveBeenCalledTimes(1);
  });

  it('hook 实例收到 opts.onEditStoryboard → onNavigate(id, "storyboard")', async () => {
    await renderCard();
    expect(h.recomposeOpts?.onEditStoryboard).toBeTypeOf("function");
    h.recomposeOpts!.onEditStoryboard!();
    expect(h.onNavigate).toHaveBeenCalledWith(failedTask.id, "storyboard");
  });
});
