/**
 * T1（2026-08-18 两份反馈 / P-01）：任务级风格下拉五风格化的**组件接线**测试。
 *
 * 纯函数分派/承载/回落逻辑由 style-presets-task-filter.test.ts 锁；本文件锁
 * video-gen-page.tsx 的三条接线：
 *   1. 风格下拉按当前 taskType 出对应引擎表（ai_image 图片 5 / ai_video 视频 5 /
 *      vimax 并集 7），旧 7 项不再出现；
 *   2. remotion 下风格下拉整个不渲染（style-visibility 层职责，不归分派函数）；
 *   3. handleTypeChange 切类型后受控风格值回落（残留值在新选项集下无对应项）。
 *
 * 仓库无 @testing-library/react：沿用 create-dialog-honesty.test.tsx 的
 * createRoot + act + happy-dom 手写 harness，@/components/ui/select stub 成
 * 原生 <select>（SelectItem 收集为 <option>）——测的是本组件逻辑，非 Radix 行为。
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), back: vi.fn(), refresh: vi.fn() }),
}));

vi.mock("@/core/video-gen/hooks", () => ({
  useVideoTasks: () => ({ data: [], isLoading: false }),
  useDeleteVideoTask: () => ({ mutate: vi.fn(), isPending: false }),
  useSubmitVideo: () => ({ mutate: vi.fn(), isPending: false, reset: vi.fn() }),
  useVideoVoices: () => ({ data: [] }),
  useDefaultVoice: () => ({ data: undefined }),
  useBGMList: () => ({ data: { built_in: [], custom: [] } }),
  useProviders: () => ({ data: { image: [], video: [] } }),
  useBGMUpload: () => ({
    mutate: vi.fn(),
    isPending: false,
    isSuccess: false,
    isError: false,
    error: null,
    reset: vi.fn(),
    data: undefined,
  }),
  useExtractScript: () => ({ mutate: vi.fn(), isPending: false }),
  useRecomposeTask: () => ({ mutate: vi.fn(), isPending: false }),
  useCourseProjects: () => ({ data: [], isLoading: false }),
  getStallWarning: () => null,
}));

vi.mock("@/components/workspace/video-gen/course-project-select", () => ({
  CourseProjectSelect: () => null,
}));

// Radix Select → 原生 <select> stub（同 create-dialog-honesty.test.tsx）
vi.mock("@/components/ui/select", async () => {
  const React = await import("react");

  type Opt = { value: string; label: React.ReactNode };

  const SelectItemStub = Object.assign(() => null, { __isSelectItem: true });

  function collect(children: React.ReactNode, out: Opt[]): void {
    React.Children.forEach(children, (child) => {
      if (!React.isValidElement(child)) return;
      const type = child.type as { __isSelectItem?: boolean };
      if (type.__isSelectItem) {
        const props = child.props as {
          value: string;
          children?: React.ReactNode;
        };
        out.push({ value: props.value, label: props.children });
        return;
      }
      const nested = (child.props as { children?: React.ReactNode } | null)
        ?.children;
      if (nested) collect(nested, out);
    });
  }

  function Select({
    value,
    onValueChange,
    children,
  }: {
    value?: string;
    onValueChange?: (v: string) => void;
    children?: React.ReactNode;
  }) {
    const opts: Opt[] = [];
    collect(children, opts);
    return React.createElement(
      "select",
      {
        value: value ?? "",
        onChange: (e: React.ChangeEvent<HTMLSelectElement>) =>
          onValueChange?.(e.target.value),
      },
      opts.map((o) =>
        React.createElement(
          "option",
          { key: o.value, value: o.value },
          o.label,
        ),
      ),
    );
  }

  const Null = () => null;

  return {
    Select,
    SelectContent: Null,
    SelectGroup: Null,
    SelectItem: SelectItemStub,
    SelectLabel: Null,
    SelectTrigger: Null,
    SelectValue: Null,
  };
});

import { CreateTaskDialog } from "@/components/workspace/video-gen/video-gen-create-dialog";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => undefined;
}

function findButton(root: ParentNode, text: string): HTMLButtonElement {
  const btn = Array.from(root.querySelectorAll("button")).find((b) =>
    b.textContent?.includes(text),
  );
  if (!btn) throw new Error(`button not found: ${text}`);
  return btn;
}

function findSelectByOption(
  root: ParentNode,
  optionValue: string,
): HTMLSelectElement | null {
  for (const sel of Array.from(root.querySelectorAll("select"))) {
    if (Array.from(sel.options).some((o) => o.value === optionValue)) {
      return sel;
    }
  }
  return null;
}

async function changeSelect(sel: HTMLSelectElement, value: string) {
  await act(async () => {
    // 绕过 React 的 value 追踪缓存，走原生 setter 再派 change
    const descriptor = Object.getOwnPropertyDescriptor(
      window.HTMLSelectElement.prototype,
      "value",
    );
    if (!descriptor?.set)
      throw new Error("HTMLSelectElement#value setter 缺失");
    descriptor.set.call(sel, value);
    sel.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

/** 风格下拉的唯一识别：precision_eng 选项只出现在风格下拉里 */
function findStyleSelect(): HTMLSelectElement {
  const sel = findSelectByOption(document.body, "precision_eng");
  if (!sel) throw new Error("找不到风格下拉（precision_eng 选项缺席）");
  return sel;
}

describe("T1: 创建对话框风格下拉按引擎分表（组件接线）", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
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

  async function openDialogAndExpandAdvanced() {
    await act(async () => {
      root.render(<CreateTaskDialog open={true} onOpenChange={vi.fn()} />);
    });
    expect(document.body.textContent).toContain("创建视频任务");
    await act(async () => {
      findButton(document.body, "高级设置").click();
    });
  }

  async function switchTaskType(value: string) {
    const typeSelect = findSelectByOption(document.body, "vimax");
    if (!typeSelect) throw new Error("找不到视频类型下拉");
    await changeSelect(typeSelect, value);
  }

  it("ai_image（默认类型）：风格下拉 = 默认 + 5 图片风格，视频专属档与旧 7 项均不出现", async () => {
    await openDialogAndExpandAdvanced();

    const sel = findStyleSelect();
    expect(Array.from(sel.options).map((o) => o.value)).toEqual([
      "default",
      "precision_eng",
      "paper_collage",
      "isometric_info",
      "whiteboard",
      "corporate_live",
    ]);
  });

  it("ai_video：风格下拉 = 默认 + 5 视频风格（与图片表不同：有纸艺剧场/3D角色，无等距/白板）", async () => {
    await openDialogAndExpandAdvanced();
    await switchTaskType("ai_video");

    const sel = findStyleSelect();
    expect(Array.from(sel.options).map((o) => o.value)).toEqual([
      "default",
      "precision_eng",
      "paper_collage",
      "papercraft_theater",
      "character_3d",
      "corporate_live",
    ]);
  });

  it("vimax：风格下拉 = 默认 + 两表并集 7 项", async () => {
    await openDialogAndExpandAdvanced();
    await switchTaskType("vimax");

    const sel = findStyleSelect();
    // 与 style-presets.ts UNION_STYLE_KEYS（视频 5 + 图片专属 2，推荐组展示序）逐项对齐
    expect(Array.from(sel.options).map((o) => o.value)).toEqual([
      "default",
      "precision_eng",
      "paper_collage",
      "papercraft_theater",
      "character_3d",
      "corporate_live",
      "isometric_info",
      "whiteboard",
    ]);
  });

  it("remotion：风格下拉整个不渲染（style-visibility 层排除）", async () => {
    await openDialogAndExpandAdvanced();
    await switchTaskType("remotion_course");

    expect(
      findSelectByOption(document.body, "precision_eng"),
      "remotion 下不应有风格下拉",
    ).toBeNull();
  });

  it("切类型回落：ai_video 选「纸艺剧场」后切 ai_image → 风格值回落 default（残留值在新选项集无对应项）", async () => {
    await openDialogAndExpandAdvanced();
    await switchTaskType("ai_video");

    const styleSel = findStyleSelect();
    await changeSelect(styleSel, "papercraft_theater");
    expect(styleSel.value).toBe("papercraft_theater");

    await switchTaskType("ai_image");
    const selAfter = findStyleSelect();
    expect(selAfter.value, "残留值不在图片表 → 应回落 default").toBe("default");
  });

  it("切类型保留：ai_video 选「精密工程」后切 ai_image → 三风格两用，保留不变", async () => {
    await openDialogAndExpandAdvanced();
    await switchTaskType("ai_video");

    const styleSel = findStyleSelect();
    await changeSelect(styleSel, "precision_eng");

    await switchTaskType("ai_image");
    const selAfter = findStyleSelect();
    expect(selAfter.value).toBe("precision_eng");
  });
});
