/**
 * 条目 44 + 39（2026-09-12 缺陷批）组件接线测试（harness 同
 * style-dropdown-engine-filter.test.tsx：createRoot + act + happy-dom 手写，
 * @/components/ui/select stub 成原生 <select>）：
 *   44. 风格预览通栏（StylePreviewPanel）：默认无预览 img；切 precision_eng 出现
 *       img[src*="/style-previews/precision_eng.jpg?v="] + description 文案；
 *       切另一 key src 变化（修前 UI 渲染层零覆盖，仅数据层 5 单测）。
 *   39. 语速「跟随」checkbox：取消跟随不再硬编码 1.0——绑项目 → 项目 tts_speed；
 *       未绑 → 1.2（config 缺省注释锚定）；payload tts_speed 随滑条值下发。
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), back: vi.fn(), refresh: vi.fn() }),
}));

// 可变测试夹具（vi.mock 工厂内闭包引用，用例内改值）
const fixtures = {
  courseProjects: [] as Array<{ id: string; tts_speed: number | null }>,
  submitPayloads: [] as Array<Record<string, unknown>>,
};

vi.mock("@/core/video-gen/hooks", () => ({
  useVideoTasks: () => ({ data: [], isLoading: false }),
  useDeleteVideoTask: () => ({ mutate: vi.fn(), isPending: false }),
  useSubmitVideo: () => ({
    mutate: (payload: Record<string, unknown>) => {
      fixtures.submitPayloads.push(payload);
    },
    isPending: false,
    reset: vi.fn(),
  }),
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
  useCourseProjects: () => ({
    data: fixtures.courseProjects,
    isLoading: false,
  }),
  getStallWarning: () => null,
}));

// 课程项目选择器 → 原生 select stub（走真实 onChange=setCourseProjectId 接线）
vi.mock("@/components/workspace/video-gen/course-project-select", async () => {
  const React = await import("react");
  return {
    CourseProjectSelect: ({
      value,
      onChange,
    }: {
      value?: string;
      onChange: (v: string | undefined) => void;
    }) =>
      React.createElement(
        "select",
        {
          "data-testid": "course-project-select",
          value: value ?? "",
          onChange: (e: React.ChangeEvent<HTMLSelectElement>) =>
            onChange(e.target.value || undefined),
        },
        [
          React.createElement("option", { key: "none", value: "" }, "不绑定"),
          React.createElement(
            "option",
            { key: "cp15", value: "cp_15" },
            "1.5x 项目",
          ),
        ],
      ),
  };
});

// Radix Select → 原生 <select> stub（同 style-dropdown-engine-filter.test.tsx）
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

function findStyleSelect(): HTMLSelectElement {
  const sel = findSelectByOption(document.body, "precision_eng");
  if (!sel) throw new Error("找不到风格下拉（precision_eng 选项缺席）");
  return sel;
}

/** 语速块：label 文本含「语速」的网格单元内取 checkbox/range。 */
function ttsSpeedControls() {
  const labels = Array.from(document.querySelectorAll("label"));
  const speedLabel = labels.find((l) => l.textContent?.trim() === "语速");
  if (!speedLabel) throw new Error("找不到语速 label");
  const cell = speedLabel.closest("div.space-y-2");
  if (!cell) throw new Error("找不到语速所在单元");
  const checkbox = cell.querySelector<HTMLInputElement>(
    'input[type="checkbox"]',
  );
  const range = cell.querySelector<HTMLInputElement>('input[type="range"]');
  if (!checkbox || !range) throw new Error("语速 checkbox/range 缺席");
  return { checkbox, range, followLabel: cell.textContent ?? "" };
}

async function openDialogFillScriptAndExpandAdvanced() {
  await act(async () => {
    rootRef.current!.render(
      <CreateTaskDialog open={true} onOpenChange={vi.fn()} />,
    );
  });
  // fixed 模式 canSubmit 需要 script 非空
  await act(async () => {
    const textarea = document.body.querySelector("textarea");
    if (!textarea) throw new Error("找不到脚本 textarea");
    const descriptor = Object.getOwnPropertyDescriptor(
      window.HTMLTextAreaElement.prototype,
      "value",
    );
    descriptor?.set?.call(textarea, "一段足够长的测试脚本内容。");
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => {
    findButton(document.body, "高级设置").click();
  });
}

let containerRef: HTMLDivElement;
const rootRef: { current: Root | null } = { current: null };

beforeEach(() => {
  fixtures.courseProjects = [];
  fixtures.submitPayloads = [];
  containerRef = document.createElement("div");
  document.body.appendChild(containerRef);
  rootRef.current = createRoot(containerRef);
});

afterEach(async () => {
  await act(async () => {
    rootRef.current?.unmount();
  });
  containerRef.remove();
  document.body.innerHTML = "";
});

describe("条目 44：风格预览通栏（StylePreviewPanel 组件接线）", () => {
  it("默认 default → 无预览 img", async () => {
    await openDialogFillScriptAndExpandAdvanced();
    expect(
      document.body.querySelector(
        'img[class*="aspect-video"][src*="/style-previews/"]',
      ),
    ).toBeNull();
  });

  it("切 precision_eng → 出现 img[src*='/style-previews/precision_eng.jpg?v='] + description 文案", async () => {
    await openDialogFillScriptAndExpandAdvanced();
    await changeSelect(findStyleSelect(), "precision_eng");

    const img = document.body.querySelector(
      'img[class*="aspect-video"][src*="/style-previews/"]',
    );
    expect(img, "通栏预览图应渲染").not.toBeNull();
    expect(img!.getAttribute("src")).toContain(
      "/style-previews/precision_eng.jpg?v=",
    );
    expect(document.body.textContent).toContain("金属质感的精密工程渲染");
  });

  it("切另一风格 key → img src 变化", async () => {
    await openDialogFillScriptAndExpandAdvanced();
    await changeSelect(findStyleSelect(), "precision_eng");
    await changeSelect(findStyleSelect(), "paper_collage");

    const img = document.body.querySelector(
      'img[class*="aspect-video"][src*="/style-previews/"]',
    );
    expect(img).not.toBeNull();
    expect(img!.getAttribute("src")).toContain(
      "/style-previews/paper_collage.jpg?v=",
    );
  });
});

describe("条目 39：语速「跟随」取消不再硬编码 1.0", () => {
  it("未绑项目 → 取消跟随滑条置 1.2 且 payload tts_speed=1.2", async () => {
    await openDialogFillScriptAndExpandAdvanced();
    const { checkbox, range, followLabel } = ttsSpeedControls();
    expect(followLabel).toContain("跟随默认");
    expect(checkbox.checked).toBe(true); // 初始跟随（null）

    await act(async () => {
      checkbox.click(); // React checkbox onChange 归一到 click（checked true→false）
    });
    expect(range.value).toBe("1.2");

    await act(async () => {
      findButton(document.body, "生成视频").click();
    });
    expect(fixtures.submitPayloads).toHaveLength(1);
    expect(fixtures.submitPayloads[0]!.tts_speed).toBe(1.2);
  });

  it("绑 1.5x 项目 → 取消跟随滑条置 1.5 且 payload tts_speed=1.5", async () => {
    fixtures.courseProjects = [{ id: "cp_15", tts_speed: 1.5 }];
    await openDialogFillScriptAndExpandAdvanced();

    // 绑定项目（CourseProjectSelect stub → 真实 onChange 接线）
    const projectSelect = document.body.querySelector<HTMLSelectElement>(
      '[data-testid="course-project-select"]',
    )!;
    await changeSelect(projectSelect, "cp_15");

    const { checkbox, range, followLabel } = ttsSpeedControls();
    expect(followLabel).toContain("跟随项目");
    expect(checkbox.checked).toBe(true);

    await act(async () => {
      checkbox.click(); // React checkbox onChange 归一到 click（checked true→false）
    });
    expect(range.value, "取消跟随 → 项目语速 1.5（修前硬编码 1.0）").toBe(
      "1.5",
    );

    await act(async () => {
      findButton(document.body, "生成视频").click();
    });
    expect(fixtures.submitPayloads).toHaveLength(1);
    expect(fixtures.submitPayloads[0]!.tts_speed).toBe(1.5);
  });

  it("跟随态（null）不下发 tts_speed 字段（三级链按缺省解析）", async () => {
    await openDialogFillScriptAndExpandAdvanced();
    await act(async () => {
      findButton(document.body, "生成视频").click();
    });
    expect(fixtures.submitPayloads).toHaveLength(1);
    expect(fixtures.submitPayloads[0]!).not.toHaveProperty("tts_speed");
  });
});
