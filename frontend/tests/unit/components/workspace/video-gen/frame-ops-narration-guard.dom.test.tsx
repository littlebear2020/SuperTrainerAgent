/**
 * T12（F6，2026-09-28 edits 修复批）：帧面板旁白 dirty 时「重生配音」禁用 + 提示。
 * 单拆 narration 维度——三字段合判的 dirty 不拦（image_prompt dirty 不影响配音按钮）。
 */

import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  regenMut: { mutate: vi.fn(), isPending: false },
  updateMut: {
    mutate: vi.fn(),
    reset: vi.fn(),
    isError: false,
    data: undefined,
  },
  uploadMut: { mutate: vi.fn(), isPending: false },
}));

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));
vi.mock("@/core/video-gen/hooks", () => ({
  useRegenerateFrame: () => h.regenMut,
  useUpdateStoryboardFrame: () => h.updateMut,
  useUploadFrameVisual: () => h.uploadMut,
}));
vi.mock("@/core/video-gen/api", () => ({
  getFrameVisualUrl: (taskId: string, i: number, v: number) =>
    `/api/video/tasks/${taskId}/frames/${i}/visual?v=${v}`,
}));

import { FrameOpsPanel } from "@/components/workspace/video-gen/video-gen-frame-ops-panel";
import type { StoryboardFrameData } from "@/core/video-gen/types";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const FRAME: StoryboardFrameData = {
  index: 0,
  narration: "已保存的旁白",
  image_prompt: "已保存的提示词",
  duration: 3,
};

let root: Root | null = null;
let container: HTMLDivElement | null = null;

function renderPanel(frame: StoryboardFrameData, taskStatus = "ready") {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => {
    root!.render(
      (
        <FrameOpsPanel
          taskId="vg_1"
          frame={frame}
          taskStatus={taskStatus}
          onClose={() => undefined}
          thumbVersion={1}
          refRecord={undefined}
          onVisualChanged={() => undefined}
          regenPending={false}
          videoType="ai_image"
        />
      ) as ReactElement,
    );
  });
}

function findAudioBtn(): HTMLButtonElement | undefined {
  return Array.from(container!.querySelectorAll("button")).find((b) =>
    b.textContent?.includes("重生配音"),
  );
}

beforeEach(() => vi.clearAllMocks());
afterEach(() => {
  act(() => root?.unmount());
  container?.remove();
});

describe("T12 帧面板未保存旁白拦截（F6）", () => {
  it("旁白 dirty → 「重生配音」禁用 + title 提示", () => {
    renderPanel(FRAME);
    // 改旁白（textarea 输入未保存）
    act(() => {
      const ta = container!.querySelector("textarea")!;
      ta.value = "改了还没保存的旁白";
      const tracker = (
        ta as HTMLTextAreaElement & {
          _valueTracker?: { setValue(v: string): void };
        }
      )._valueTracker;
      tracker?.setValue("");
      ta.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const btn = findAudioBtn();
    expect(btn).toBeTruthy();
    expect(btn!.hasAttribute("disabled")).toBe(true);
    expect(btn!.getAttribute("title")).toContain("请先保存旁白修改");
  });

  it("仅 image_prompt dirty（旁白未动）→ 「重生配音」可点", () => {
    renderPanel(FRAME);
    act(() => {
      const tas = Array.from(container!.querySelectorAll("textarea"));
      const promptTa = tas.find((t) => t.value === "已保存的提示词")!;
      promptTa.value = "改了提示词";
      const tracker = (
        promptTa as HTMLTextAreaElement & {
          _valueTracker?: { setValue(v: string): void };
        }
      )._valueTracker;
      tracker?.setValue("");
      promptTa.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const btn = findAudioBtn();
    expect(btn).toBeTruthy();
    expect(btn!.hasAttribute("disabled")).toBe(false);
    expect(btn!.getAttribute("title")).not.toContain("请先保存");
  });

  it("旁白未动 → 可点（回归：默认不拦）", () => {
    renderPanel(FRAME);
    const btn = findAudioBtn();
    expect(btn!.hasAttribute("disabled")).toBe(false);
  });
});
