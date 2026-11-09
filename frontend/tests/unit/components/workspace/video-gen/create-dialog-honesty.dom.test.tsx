/**
 * Task 13（EP1-5 / FP2-11 / FP2-20）：创建对话框诚实化。
 * - EP1-5 → E（2026-08-31 转场清理）：转场 UI 整体删除（假选项比没有选项更
 *   误导；W1 对 ai_* 强制 none + 后端默认值已对齐硬切）——全类型均无转场控件。
 * - FP2-11: ai_video 类型描述须告知单镜时长上限与真人照片审查限制。
 * - FP2-20: 脚本上传 accept 缺 .pdf（后端 routers/video_gen.py M2-1 已支持解析）。
 *
 * 仓库无 @testing-library/react：沿用 useSubmitVideo-error.test.tsx 的
 * createRoot + act + happy-dom 手写 harness。Radix Select 交互依赖 pointer
 * capture（happy-dom 不完整），这里把 @/components/ui/select stub 成原生
 * <select>（遍历 children 收集 SelectItem 为 <option>）——测的是本组件的
 * 门控/文案逻辑，不是 Radix 行为。
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
  useSubmitVideo: () => ({
    mutate: vi.fn(),
    isPending: false,
    reset: vi.fn(),
  }),
  useVideoVoices: () => ({ data: [] }),
  // Task 15: page 新增「默认」音色解析 query——stub 为无数据（走静态兜底链）
  useDefaultVoice: () => ({ data: undefined }),
  useBGMList: () => ({ data: { built_in: [], custom: [] } }),
  // Task 6：生成模型下拉。空列表 → 不渲染该下拉，与本文件断言无关。
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

// CourseProjectSelect 自带 hooks/对话框子树，与门控断言无关——stub 掉。
vi.mock("@/components/workspace/video-gen/course-project-select", () => ({
  CourseProjectSelect: () => null,
}));

// Radix Select → 原生 <select> stub：Select 遍历 children 收集 SelectItem
// （SelectTrigger/SelectContent/SelectGroup/SelectLabel/SelectValue 仅为
// 结构标记，不实际渲染）。
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

import { VideoGenPage } from "@/components/workspace/video-gen/video-gen-page";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

// happy-dom 兜底：Radix Dialog 聚焦陷阱可能触到。
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

describe("创建对话框诚实化（EP1-5/FP2-11/FP2-20）", () => {
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
    // Radix Dialog portal 挂在 body 下，一并清掉
    document.body.innerHTML = "";
  });

  async function openDialogAndExpandAdvanced() {
    await act(async () => {
      root.render(<VideoGenPage />);
    });
    await act(async () => {
      findButton(document.body, "新建任务").click();
    });
    expect(document.body.textContent).toContain("创建视频任务");
    await act(async () => {
      findButton(document.body, "高级设置").click();
    });
  }

  it("ai_image（默认）下无转场 UI（EP1-5 → E 2026-08-31 直接删除）", async () => {
    await openDialogAndExpandAdvanced();

    expect(document.body.textContent).not.toContain("转场效果");
    expect(findSelectByOption(document.body, "dissolve")).toBeNull();
  });

  it("ai_video 类型下转场门控 + 描述包含单镜时长与人脸限制（FP2-11）", async () => {
    await openDialogAndExpandAdvanced();

    const typeSelect = findSelectByOption(document.body, "vimax");
    expect(typeSelect).not.toBeNull();
    await changeSelect(typeSelect!, "ai_video");

    expect(document.body.textContent).toContain("单镜最长 15 秒");
    expect(document.body.textContent).toContain("真人照片");
    expect(document.body.textContent).not.toContain("转场效果");
    expect(findSelectByOption(document.body, "dissolve")).toBeNull();
  });

  it("vimax 类型下同样无转场 UI（对照：删除是全类型的，不再是 ai_* 门控）", async () => {
    await openDialogAndExpandAdvanced();

    const typeSelect = findSelectByOption(document.body, "vimax");
    expect(typeSelect).not.toBeNull();
    await changeSelect(typeSelect!, "vimax");

    expect(findSelectByOption(document.body, "dissolve")).toBeNull();
    expect(document.body.textContent).not.toContain("转场效果");
    expect(document.body.textContent).not.toContain("转场时长");
  });

  it("脚本上传 accept 含 .pdf（FP2-20，后端已支持解析）", async () => {
    await act(async () => {
      root.render(<VideoGenPage />);
    });
    await act(async () => {
      findButton(document.body, "新建任务").click();
    });

    const input = document.body.querySelector<HTMLInputElement>(
      'input[type="file"][accept*=".docx"]',
    );
    expect(input).not.toBeNull();
    expect(input!.getAttribute("accept")).toContain(".pdf");
  });

  it("vimax fixed 的目标时长在主表单，未展开高级设置也可见（E1）", async () => {
    await act(async () => {
      root.render(<VideoGenPage />);
    });
    await act(async () => {
      findButton(document.body, "新建任务").click();
    });
    const typeSelect = findSelectByOption(document.body, "vimax");
    expect(typeSelect).not.toBeNull();
    await changeSelect(typeSelect!, "vimax");

    expect(document.body.textContent).toContain("目标时长（秒，可选）");
    expect(document.body.textContent).not.toContain("转场效果");
  });
});
