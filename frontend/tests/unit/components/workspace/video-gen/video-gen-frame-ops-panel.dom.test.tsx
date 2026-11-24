/**
 * E5（2026-11-24 前端可靠性批，PR1）：帧面板「重生画面」的付费前守卫。
 *
 * 事故：画面提示词 / 视觉来源改了没保存就点「重生画面」——后端拿旧参数付费重生，
 * 用户以为改完直接出图。与 T12（F6）的 narrationDirty 分开：旁白未保存只挡
 * 「重生配音」，画面参数未保存只挡「重生画面」，互不误伤。守卫只对
 * ai_image/ai_video/ai_visual 生效，ViMax 保持原按钮规则。
 *
 * 仓库无 @testing-library/react：createRoot + act + happy-dom 手写 harness。
 * VisualSourceSwitcher 是 Radix Select（happy-dom 无法驱动指针开合），这里换成
 * 原生 select 桩——被测的是面板的 dirty 判定，不是切换器本身。
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
vi.mock("@/components/workspace/video-gen/visual-source-switcher", () => ({
  VisualSourceSwitcher: (props: {
    value: string | null | undefined;
    onChange: (value: string) => void;
    disabled?: boolean;
  }) => (
    <select
      data-testid="visual-source"
      value={props.value ?? ""}
      disabled={props.disabled}
      onChange={(e) => props.onChange(e.target.value)}
    >
      <option value="ai_image">AI 生图</option>
      <option value="ai_video">AI 生视频</option>
    </select>
  ),
}));

import { FrameOpsPanel } from "@/components/workspace/video-gen/video-gen-frame-ops-panel";
import type { StoryboardFrameData } from "@/core/video-gen/types";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const FRAME: StoryboardFrameData = {
  index: 0,
  narration: "已保存的旁白",
  image_prompt: "已保存的提示词",
  visual_source: "ai_image",
  duration: 3,
};

const GUARDED_TYPES = ["ai_image", "ai_video", "ai_visual"] as const;

let root: Root | null = null;
let container: HTMLDivElement | null = null;

function renderPanel(
  opts: {
    videoType?: string;
    taskStatus?: string;
    frame?: StoryboardFrameData;
  } = {},
) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => {
    root!.render(
      (
        <FrameOpsPanel
          taskId="vg_1"
          frame={opts.frame ?? FRAME}
          taskStatus={opts.taskStatus ?? "ready"}
          onClose={() => undefined}
          thumbVersion={1}
          refRecord={undefined}
          onVisualChanged={() => undefined}
          regenPending={false}
          videoType={opts.videoType}
        />
      ) as ReactElement,
    );
  });
}

function findButton(text: string): HTMLButtonElement {
  const btn = Array.from(container!.querySelectorAll("button")).find((b) =>
    b.textContent?.includes(text),
  );
  expect(btn, `找不到按钮「${text}」`).toBeTruthy();
  return btn!;
}

function typeInto(el: HTMLTextAreaElement, value: string) {
  act(() => {
    el.value = value;
    // React 的 value tracker 认为值没变时不触发 onChange：重置它（同
    // frame-ops-narration-guard 的写法），让「改回原值」也能被听到。
    (
      el as HTMLTextAreaElement & {
        _valueTracker?: { setValue(v: string): void };
      }
    )._valueTracker?.setValue("");
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

/** 第二个 textarea = 画面提示词（面板顺序固定：[0] 旁白，[1] 画面提示词）。 */
function typePrompt(value: string) {
  const tas = container!.querySelectorAll("textarea");
  typeInto(tas[1]!, value);
}

function typeNarration(value: string) {
  const tas = container!.querySelectorAll("textarea");
  typeInto(tas[0]!, value);
}

function pickSource(value: string) {
  const select = container!.querySelector<HTMLSelectElement>(
    '[data-testid="visual-source"]',
  )!;
  expect(select, "视觉来源切换器桩未渲染").toBeTruthy();
  act(() => {
    select.value = value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

function click(el: HTMLElement) {
  act(() => {
    el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

beforeEach(() => vi.clearAllMocks());
afterEach(() => {
  act(() => root?.unmount());
  container?.remove();
});

describe("E5 帧面板未保存编辑禁止付费重生", () => {
  it.each(GUARDED_TYPES)(
    "%s：画面提示词未保存 → 「重生画面」禁用并提示先保存，点击不发付费 mutation",
    (videoType) => {
      renderPanel({ videoType });
      typePrompt("改了还没保存的提示词");
      const btn = findButton("重生画面");
      expect(btn.hasAttribute("disabled")).toBe(true);
      expect(btn.getAttribute("title")).toContain("请先保存");
      click(btn);
      expect(h.regenMut.mutate).not.toHaveBeenCalled();
    },
  );

  it.each(GUARDED_TYPES)(
    "%s：视觉来源未保存 → 「重生画面」禁用并提示先保存",
    (videoType) => {
      renderPanel({ videoType });
      pickSource("ai_video");
      const btn = findButton("重生画面");
      expect(btn.hasAttribute("disabled")).toBe(true);
      expect(btn.getAttribute("title")).toContain("请先保存");
      click(btn);
      expect(h.regenMut.mutate).not.toHaveBeenCalled();
    },
  );

  it("仅旁白未保存 → 「重生画面」可点、「重生配音」禁用（narrationDirty 只挡配音）", () => {
    renderPanel({ videoType: "ai_image" });
    typeNarration("改了还没保存的旁白");
    expect(findButton("重生画面").hasAttribute("disabled")).toBe(false);
    expect(findButton("重生配音").hasAttribute("disabled")).toBe(true);
  });

  it("仅画面提示词未保存 → 「重生配音」可点（visualDirty 不误挡配音）", () => {
    renderPanel({ videoType: "ai_image" });
    typePrompt("改了还没保存的提示词");
    expect(findButton("重生配音").hasAttribute("disabled")).toBe(false);
    expect(findButton("重生配音").getAttribute("title")).not.toContain(
      "请先保存",
    );
  });

  it("旁白与画面提示词都未保存 → 两个重生按钮都禁用", () => {
    renderPanel({ videoType: "ai_image" });
    typeNarration("改了旁白");
    typePrompt("改了提示词");
    expect(findButton("重生画面").hasAttribute("disabled")).toBe(true);
    expect(findButton("重生配音").hasAttribute("disabled")).toBe(true);
  });

  it("未保存的提示词改回原值 → 「重生画面」恢复可点", () => {
    renderPanel({ videoType: "ai_image" });
    typePrompt("改了还没保存的提示词");
    expect(findButton("重生画面").hasAttribute("disabled")).toBe(true);
    typePrompt(FRAME.image_prompt!);
    expect(findButton("重生画面").hasAttribute("disabled")).toBe(false);
  });

  it("failed 态同样受守卫（canRegen 含 failed）", () => {
    renderPanel({ videoType: "ai_image", taskStatus: "failed" });
    typePrompt("改了还没保存的提示词");
    expect(findButton("重生画面").hasAttribute("disabled")).toBe(true);
  });

  it("vimax：提示词与视觉来源都未保存时「重生画面」保持可点（原按钮规则）", () => {
    renderPanel({ videoType: "vimax" });
    typePrompt("改了还没保存的提示词");
    pickSource("ai_video");
    const btn = findButton("重生画面");
    expect(btn.hasAttribute("disabled")).toBe(false);
    click(btn);
    expect(h.regenMut.mutate).toHaveBeenCalledTimes(1);
    expect(h.regenMut.mutate.mock.calls[0]![0]).toMatchObject({
      frameIndex: 0,
      mode: "visual_only",
    });
  });

  it("不传 videoType（既有调用零改动）→ 不启用守卫", () => {
    renderPanel();
    typePrompt("改了还没保存的提示词");
    expect(findButton("重生画面").hasAttribute("disabled")).toBe(false);
  });

  it("对照组：无未保存编辑时点「重生画面」发 visual_only mutation", () => {
    renderPanel({ videoType: "ai_image" });
    click(findButton("重生画面"));
    expect(h.regenMut.mutate).toHaveBeenCalledTimes(1);
    expect(h.regenMut.mutate.mock.calls[0]![0]).toMatchObject({
      frameIndex: 0,
      mode: "visual_only",
    });
  });
});
