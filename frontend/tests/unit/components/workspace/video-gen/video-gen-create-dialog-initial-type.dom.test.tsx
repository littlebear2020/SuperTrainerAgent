import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({ submitMutate: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), back: vi.fn(), refresh: vi.fn() }),
}));

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
}));

vi.mock("@/core/video-gen/hooks", () => ({
  useSubmitVideo: () => ({
    mutate: h.submitMutate,
    isPending: false,
    reset: vi.fn(),
  }),
  useBGMUpload: () => ({
    mutate: vi.fn(),
    isPending: false,
    isSuccess: false,
    isError: false,
    error: null,
    reset: vi.fn(),
    data: undefined,
  }),
  useProviders: () => ({ data: { image: [], video: [] } }),
  useCourseProjects: () => ({ data: [], isLoading: false }),
  useBGMList: () => ({
    data: { built_in: [], custom: [] },
    isLoading: false,
  }),
  useVideoVoices: () => ({ data: [], isLoading: false }),
  useDefaultVoice: () => ({ data: undefined }),
  useExtractScript: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("@/components/workspace/video-gen/course-project-select", () => ({
  CourseProjectSelect: () => null,
}));

vi.mock("@/components/ui/select", async () => {
  const React = await import("react");
  type Option = { value: string; label: React.ReactNode };
  const SelectItem = Object.assign(() => null, { __isSelectItem: true });

  function collect(children: React.ReactNode, options: Option[]): void {
    React.Children.forEach(children, (child) => {
      if (!React.isValidElement(child)) return;
      const type = child.type as { __isSelectItem?: boolean };
      if (type.__isSelectItem) {
        const props = child.props as {
          value: string;
          children?: React.ReactNode;
        };
        options.push({ value: props.value, label: props.children });
        return;
      }
      collect(
        (child.props as { children?: React.ReactNode } | null)?.children,
        options,
      );
    });
  }

  function Select({
    value,
    onValueChange,
    children,
  }: {
    value?: string;
    onValueChange?: (value: string) => void;
    children?: React.ReactNode;
  }) {
    const options: Option[] = [];
    collect(children, options);
    return React.createElement(
      "select",
      {
        value: value ?? "",
        onChange: (event: React.ChangeEvent<HTMLSelectElement>) =>
          onValueChange?.(event.target.value),
      },
      options.map((option) =>
        React.createElement(
          "option",
          { key: option.value, value: option.value },
          option.label,
        ),
      ),
    );
  }

  const Null = () => null;
  return {
    Select,
    SelectContent: Null,
    SelectGroup: Null,
    SelectItem,
    SelectLabel: Null,
    SelectTrigger: Null,
    SelectValue: Null,
  };
});

import {
  CreateTaskDialog,
  TYPE_CONFIG,
} from "@/components/workspace/video-gen/video-gen-create-dialog";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function findTaskTypeSelect(): HTMLSelectElement | null {
  return (
    Array.from(document.querySelectorAll("select")).find((item) =>
      Array.from(item.options).some((option) => option.value === "vimax"),
    ) ?? null
  );
}

async function changeTaskType(value: string) {
  const select = findTaskTypeSelect();
  if (!select) throw new Error("task type select not found");
  await act(async () => {
    Reflect.set(select, "value", value);
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

function button(text: string): HTMLButtonElement {
  const result = Array.from(document.querySelectorAll("button")).find((item) =>
    item.textContent?.includes(text),
  );
  if (!result) throw new Error(`button not found: ${text}`);
  return result;
}

describe("CreateTaskDialog initialTaskType", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    h.submitMutate.mockClear();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    document.body.innerHTML = "";
  });

  async function render(initialTaskType?: "ai_image" | "ai_video") {
    await act(async () => {
      root.render(
        <CreateTaskDialog
          open={true}
          onOpenChange={vi.fn()}
          initialTaskType={initialTaskType}
        />,
      );
    });
  }

  it("uses focused AI dynamic video copy without type or topic controls", async () => {
    await render("ai_video");
    const text = document.body.textContent ?? "";
    expect(text).toContain("创建 AI 动态视频");
    expect(text).toContain("用脚本生成连贯的动态场景");
    expect(text).not.toContain("视频类型");
    expect(text).not.toContain("生成方式");
    expect(text).not.toContain("话题生成");
    expect(findTaskTypeSelect()).toBeNull();
  });

  it("uses image-motion copy and submits the existing fixed-script payload", async () => {
    await render("ai_image");
    const text = document.body.textContent ?? "";
    expect(text).toContain("创建图片动效");
    expect(text).toContain("通过镜头运动串成视频");
    expect(text).toContain("作品标题（可选）");
    expect(text).toContain("视频脚本");
    expect(findTaskTypeSelect()).toBeNull();
    expect(text).not.toContain("话题生成");
    expect(
      document
        .querySelector("[role='dialog']")
        ?.className.includes("from-white"),
    ).toBe(true);

    const script = document.querySelector("textarea");
    if (!script) throw new Error("script textarea not found");
    await act(async () => {
      Object.getOwnPropertyDescriptor(
        HTMLTextAreaElement.prototype,
        "value",
      )?.set?.call(script, "第一句。\n第二句。");
      script.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => button("创建并编辑分镜").click());
    expect(h.submitMutate).toHaveBeenCalledTimes(1);
    expect(h.submitMutate.mock.calls[0]?.[0]).toMatchObject({
      video_type: "ai_image",
      mode: "fixed",
      script: "第一句。\n第二句。",
    });
  });

  it("keeps the legacy generic dialog and default ai_image without initialTaskType", async () => {
    await render();
    expect(document.body.textContent).toContain("创建视频任务");
    expect(document.body.textContent).toContain("生成方式");
    const select = findTaskTypeSelect();
    expect(select?.value).toBe("ai_image");
    expect(select?.selectedOptions[0]?.textContent).toBe(
      TYPE_CONFIG.ai_image!.label,
    );
  });

  it("does not rewrite legacy task type on ordinary rerender", async () => {
    await render();
    await changeTaskType("remotion_course");
    expect(findTaskTypeSelect()?.value).toBe("remotion_course");
    await render();
    expect(findTaskTypeSelect()?.value).toBe("remotion_course");
  });
});
