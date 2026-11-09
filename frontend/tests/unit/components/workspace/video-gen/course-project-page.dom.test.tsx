import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CourseProjectPage } from "@/components/workspace/video-gen/course-project-page";
import type { CourseProject } from "@/core/video-gen/types";

const h = vi.hoisted(() => ({
  query: {
    data: [] as CourseProject[],
    isLoading: false,
    isError: false,
  },
  detailId: null as string | null,
  selectCreated: null as ((project: CourseProject) => void) | null,
}));

vi.mock("@/core/video-gen/hooks", () => ({
  useCourseProjects: () => h.query,
}));

vi.mock("@/components/workspace/video-gen/course-project-detail", () => ({
  CourseProjectDetailPage: ({
    projectId,
    onBack,
  }: {
    projectId: string;
    onBack: () => void;
  }) => {
    h.detailId = projectId;
    return (
      <div data-testid="course-project-detail">
        <button onClick={onBack}>返回列表</button>
      </div>
    );
  },
}));

vi.mock("@/components/workspace/video-gen/course-project-dialog", () => ({
  CourseProjectDialog: ({
    children,
    onSelect,
  }: {
    children: React.ReactNode;
    onSelect?: (project: CourseProject) => void;
  }) => {
    h.selectCreated = onSelect ?? null;
    return (
      <div>
        {children}
        <button
          onClick={() =>
            onSelect?.({
              id: "created-project",
              user_id: "u1",
              title: "新建后项目",
            })
          }
        >
          模拟创建成功
        </button>
      </div>
    );
  },
}));

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function project(overrides: Partial<CourseProject> = {}): CourseProject {
  return {
    id: "p1",
    user_id: "u1",
    title: "真实资产项目",
    visual_style_preset: "precision_eng",
    color_palette: { primary: "#123456" },
    style_anchor_image: "/private/anchor.png",
    created_at: "2026-11-01T08:00:00Z",
    updated_at: "2026-11-02T09:00:00Z",
    ...overrides,
  };
}

function button(text: string): HTMLButtonElement {
  const result = Array.from(document.querySelectorAll("button")).find((item) =>
    item.textContent?.includes(text),
  );
  if (!result) throw new Error(`button not found: ${text}`);
  return result;
}

describe("CourseProjectPage assets", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    h.query = { data: [project()], isLoading: false, isError: false };
    h.detailId = null;
    h.selectCreated = null;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    document.body.innerHTML = "";
  });

  async function render() {
    await act(async () => root.render(<CourseProjectPage />));
  }

  it("anchor error falls back to style image then color", async () => {
    await render();
    let image = container.querySelector("img");
    expect(image?.getAttribute("src")).toBe(
      "/api/video/projects/p1/style-anchor-image",
    );
    await act(async () => image?.dispatchEvent(new Event("error")));
    image = container.querySelector("img");
    expect(image?.getAttribute("src")).toContain(
      "/style-previews/precision_eng.jpg",
    );
    expect(image?.getAttribute("alt")).toContain("风格示意");
    await act(async () => image?.dispatchEvent(new Event("error")));
    expect(container.querySelector("img")).toBeNull();
    const fallback = container.querySelector<HTMLElement>(
      "[data-testid='course-project-cover-fallback']",
    );
    expect(fallback?.style.backgroundColor).toBe("#123456");
  });

  it("shows title style update and new/edit actions only", async () => {
    h.query.data = [
      {
        ...project(),
        video_count: 99,
        total_duration: 88,
      } as CourseProject,
    ];
    await render();
    const text = container.textContent ?? "";
    for (const expected of [
      "我的资产",
      "真实资产项目",
      "精密工程",
      "最近更新",
      "新建项目",
      "编辑项目",
    ]) {
      expect(text).toContain(expected);
    }
    for (const forbidden of [
      "99",
      "88",
      "项目统计",
      "时长",
      "分辨率",
      "完成时间",
      "系统健康",
      "点击编辑详情",
    ]) {
      expect(text).not.toContain(forbidden);
    }
    const grid = container.querySelector("[data-testid='course-project-grid']");
    for (const className of [
      "grid-cols-1",
      "sm:grid-cols-2",
      "xl:grid-cols-3",
    ]) {
      expect(grid?.className).toContain(className);
    }
  });

  it("opens existing detail for edit and passes onSelect for create", async () => {
    await render();
    await act(async () => button("编辑项目").click());
    expect(h.detailId).toBe("p1");
    await act(async () => button("返回列表").click());
    expect(h.selectCreated).not.toBeNull();
    await act(async () => button("模拟创建成功").click());
    expect(h.detailId).toBe("created-project");
  });

  it("keeps loading empty and error states honest", async () => {
    h.query = { data: [], isLoading: true, isError: false };
    await render();
    expect(container.textContent).toContain("正在加载资产");
    h.query = { data: [], isLoading: false, isError: false };
    await render();
    expect(container.textContent).toContain("暂无课程项目");
    h.query = { data: [], isLoading: false, isError: true };
    await render();
    expect(container.textContent).toContain("资产加载失败");
    expect(container.textContent).not.toContain("暂无课程项目");
  });
});
