/** Task 23 (P2-3): 讲解词保存加 toast 引导 */

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const toastMock = vi.hoisted(() => ({ success: vi.fn() }));
vi.mock("sonner", () => ({ toast: { success: toastMock.success } }));

const updateMock = vi.hoisted(() => ({
  mutate: vi.fn(),
  isPending: false,
  reset: vi.fn(),
  data: undefined,
}));
vi.mock("@/core/video-gen/hooks", () => ({
  useRegenerateFrame: () => ({
    mutate: vi.fn(),
    isPending: false,
    variables: undefined,
  }),
  useUploadFrameVisual: () => ({
    mutate: vi.fn(),
    isPending: false,
    variables: undefined,
  }),
  useUpdateStoryboardFrame: () => updateMock,
}));

import { FrameOpsPanel } from "@/components/workspace/video-gen/video-gen-frame-ops-panel";
import type { StoryboardFrameData } from "@/core/video-gen/types";

const frame: StoryboardFrameData = {
  index: 2,
  narration: "第三句旁白",
  image_prompt: "讲师站在白板前",
  duration: 6,
  visual_url: "http://cdn/2.png",
  visual_type: "image",
};

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  // unmount 收进 afterEach 并裹 act：中途断言失败也不泄漏 root/container
  await act(async () => {
    root.unmount();
  });
  container.remove();
  document.body.innerHTML = "";
});

test("save button triggers toast on success (P2-3)", async () => {
  updateMock.mutate = vi.fn(
    (_data: unknown, options?: { onSuccess?: () => void }) => {
      options?.onSuccess?.();
    },
  );

  await act(async () => {
    root.render(
      createElement(FrameOpsPanel, {
        taskId: "vg_t",
        frame,
        taskStatus: "ready",
        onClose: vi.fn(),
      }),
    );
  });

  // Change narration to make form dirty (save button only appears when dirty)
  const textarea = container.querySelector("textarea")!;
  expect(textarea).toBeTruthy();

  // Use React's internal setter to properly trigger state update in happy-dom
  await act(async () => {
    const nativeSetter = Object.getOwnPropertyDescriptor(
      window.HTMLTextAreaElement.prototype,
      "value",
    );
    nativeSetter?.set?.call(textarea, "修改后的旁白");
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  });

  const buttons = container.querySelectorAll("button");
  const saveBtn = Array.from(buttons).find((b) =>
    b.textContent?.includes("保存修改"),
  );
  // 按钮缺席即测试失败——旧 else 分支自调 mutate 再断言 toast 是同语反复
  if (!saveBtn) throw new Error("保存修改 button not found");

  await act(async () => {
    saveBtn.click();
  });
  expect(toastMock.success).toHaveBeenCalled();
});
