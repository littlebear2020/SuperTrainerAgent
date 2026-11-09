import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), back: vi.fn(), refresh: vi.fn() }),
}));

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
}));

vi.mock("@/core/video-gen/hooks", () => ({
  useSubmitVideo: () => ({ mutate: vi.fn(), isPending: false, reset: vi.fn() }),
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

function taskTypeSelect(): HTMLSelectElement {
  const select = Array.from(document.querySelectorAll("select")).find((item) =>
    Array.from(item.options).some((option) => option.value === "vimax"),
  );
  if (!select) throw new Error("task type select not found");
  return select;
}

async function changeTaskType(value: string) {
  const select = taskTypeSelect();
  await act(async () => {
    Reflect.set(select, "value", value);
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

describe("CreateTaskDialog initialTaskType", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
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

  it("initializes ai_video from initialTaskType", async () => {
    await render("ai_video");
    const select = taskTypeSelect();
    expect(select.value).toBe("ai_video");
    expect(select.selectedOptions[0]?.textContent).toBe(
      TYPE_CONFIG.ai_video!.label,
    );
    expect(select.value).not.toBe("ai_image");
  });

  it("initializes ai_image and defaults to ai_image", async () => {
    await render("ai_image");
    expect(taskTypeSelect().selectedOptions[0]?.textContent).toBe(
      TYPE_CONFIG.ai_image!.label,
    );
    await act(async () => root.unmount());
    root = createRoot(container);
    await render();
    expect(taskTypeSelect().selectedOptions[0]?.textContent).toBe(
      TYPE_CONFIG.ai_image!.label,
    );
  });

  it("does not rewrite task type on ordinary rerender", async () => {
    await render("ai_video");
    await changeTaskType("remotion_course");
    expect(taskTypeSelect().value).toBe("remotion_course");
    await render("ai_video");
    expect(taskTypeSelect().value).toBe("remotion_course");
  });
});
