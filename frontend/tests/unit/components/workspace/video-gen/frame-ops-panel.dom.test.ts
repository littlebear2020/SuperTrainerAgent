import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, test, vi } from "vitest";

const h = vi.hoisted(() => ({
  regenMutate: vi.fn(),
}));

vi.mock("@/core/video-gen/hooks", () => ({
  useRegenerateFrame: (_taskId, _opts) => ({
    mutate: h.regenMutate,
    isPending: false,
    variables: undefined,
  }),
  useUploadFrameVisual: () => ({
    mutate: vi.fn(),
    isPending: false,
    variables: undefined,
  }),
  useUpdateStoryboardFrame: () => ({
    mutate: vi.fn(),
    isPending: false,
    data: undefined,
    reset: vi.fn(),
  }),
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

test("panel renders frame number, narration and action buttons", () => {
  const html = renderToStaticMarkup(
    createElement(FrameOpsPanel, {
      taskId: "vg_t",
      frame,
      taskStatus: "ready",
      onClose: vi.fn(),
    }),
  );
  expect(html).toContain("帧 3");
  expect(html).toContain("第三句旁白");
  // T13 后按钮拆分为「重生画面/重生配音」——断言真实按钮文案，而非 title 属性
  // 里残留的"重新生成"字样（此前断言命中 title，语义错位）。
  expect(html).toContain("重生画面");
  expect(html).toContain("重生配音");
  expect(html).toContain("上传替换");
});

test("panel shows prompt editor for ai_image/ai_video tasks", () => {
  const html = renderToStaticMarkup(
    createElement(FrameOpsPanel, {
      taskId: "vg_t",
      frame,
      taskStatus: "ready",
      onClose: vi.fn(),
    }),
  );
  expect(html).toContain("讲师站在白板前");
});

// ── B1: thumbVersion 破缓存接线 ─────────────────────────────────────────

test("panel busts local thumbnail cache via thumbVersion", () => {
  const localFrame: StoryboardFrameData = {
    index: 1,
    narration: "第二句",
    local_visual_path: "visuals/frame_001.jpg",
    visual_url: null,
    visual_type: "image",
    duration: 3,
  };
  const html = renderToStaticMarkup(
    createElement(FrameOpsPanel, {
      taskId: "vg_t",
      frame: localFrame,
      taskStatus: "ready",
      thumbVersion: 1721700000000,
      onClose: vi.fn(),
    }),
  );
  // 修复前：src 无 ?v= → 同名文件覆盖后浏览器不发新请求，显示旧图
  expect(html).toContain("?v=1721700000000");
});

test("panel editable/canRegen branches: draft edits but cannot regen, composing locks all", () => {
  // draft：可编辑（editable=true）但不可重生（canRegen=false）
  const draftHtml = renderToStaticMarkup(
    createElement(FrameOpsPanel, {
      taskId: "vg_t",
      frame,
      taskStatus: "draft",
      onClose: vi.fn(),
    }),
  );
  expect(draftHtml).toContain("仅 ready/failed 可重生"); // canRegen=false 的 title

  // composing：全部锁定（editable=false → textarea disabled）
  const composingHtml = renderToStaticMarkup(
    createElement(FrameOpsPanel, {
      taskId: "vg_t",
      frame,
      taskStatus: "composing",
      onClose: vi.fn(),
    }),
  );
  // composing 态旁白/提示词 textarea 应 disabled（两个 textarea 都带 disabled 属性）
  const textareaDisabledCount = (
    composingHtml.match(/<textarea[^>]*disabled/g) ?? []
  ).length;
  expect(textareaDisabledCount).toBe(2);
});

test("failed status disables upload button with hint", () => {
  const html = renderToStaticMarkup(
    createElement(FrameOpsPanel, {
      taskId: "vg_t",
      frame,
      taskStatus: "failed",
      onClose: vi.fn(),
    }),
  );
  // H1c: failed 态后端 409（upload 仅 draft/ready）——按钮必须禁用，不得可点必败
  expect(html).toContain("failed 状态不支持上传替换");
  // 上传按钮本身必须 disabled（此前仅断言提示文案，移除 disabled 的回归不会变红）
  expect(html).toMatch(/<button[^>]*disabled=""[^>]*>[\s\S]*?上传替换/);
});

test("regenPending disables and spins the regenerate button", () => {
  const html = renderToStaticMarkup(
    createElement(FrameOpsPanel, {
      taskId: "vg_t",
      frame,
      taskStatus: "ready",
      regenPending: true,
      onClose: vi.fn(),
    }),
  );
  expect(html).toContain("animate-spin");
});

// ── 2026-07-27 T13：重生画面/重生配音 模式分流（visual_only / audio_only） ──

test("重生画面 calls regenerate with visual_only; 重生配音 with audio_only", async () => {
  h.regenMutate.mockClear();
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
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
  const buttons = Array.from(container.querySelectorAll("button"));
  const visualBtn = buttons.find((b) => b.textContent?.includes("重生画面"));
  const audioBtn = buttons.find((b) => b.textContent?.includes("重生配音"));
  expect(visualBtn).toBeTruthy();
  expect(audioBtn).toBeTruthy();
  await act(async () => {
    visualBtn!.click();
  });
  await act(async () => {
    audioBtn!.click();
  });
  expect(h.regenMutate).toHaveBeenCalledTimes(2);
  expect(h.regenMutate.mock.calls[0]?.[0]).toEqual({
    frameIndex: 2,
    mode: "visual_only",
  });
  expect(h.regenMutate.mock.calls[1]?.[0]).toEqual({
    frameIndex: 2,
    mode: "audio_only",
  });
  await act(async () => {
    root.unmount();
  });
  container.remove();
});

// 2026-08-02（用户实测）：「上传替换」按钮被右侧画面提示词模块挡住。
//
// 根因是尺寸算术，不是层级：三个按钮曾并排 `flex` 在 w-48（192px）左栏里，
// 扣掉两个 gap-2（16px）后每个仅约 58px；而 size=sm 的 px-3(24px) + 图标 12px
// + mr-1(4px) 已占 40px，「上传替换」四个汉字（约 48px）放不下 → 溢出容器、
// 压到右栏的画面提示词 textarea 上。
//
// 本用例锁「按钮不并排挤在一行」这条几何前提。断言容器类名而非像素——
// happy-dom 不做真实布局（拿不到 getBoundingClientRect 的真值），
// 类名是此环境下可靠且能表达意图的判据。
test("action buttons must not be squeezed into one flex row (upload overflow)", () => {
  const html = renderToStaticMarkup(
    createElement(FrameOpsPanel, {
      taskId: "vg_t",
      frame,
      taskStatus: "ready",
      onClose: vi.fn(),
    }),
  );

  // 按钮行必须是两列 grid，且「上传替换」独占整行
  expect(html).toMatch(/grid\s+grid-cols-2/);
  expect(html).toContain("col-span-2");

  // 回归护栏：三个按钮不得再退回 flex-1 并排（那正是溢出成因）
  const buttonRow = html.slice(
    html.indexOf("重生画面") - 800,
    html.indexOf("上传替换") + 200,
  );
  expect(
    buttonRow,
    "按钮行不得再用 flex-1 并排（w-48 容器放不下三个中文按钮）",
  ).not.toMatch(/flex-1[^"]*"[^>]*>\s*(<svg|[^<]*重生)/);
});
