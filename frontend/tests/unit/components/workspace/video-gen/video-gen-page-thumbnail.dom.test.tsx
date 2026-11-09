/**
 * 2026-09-13 死链修复 T4：列表卡缩略图 onError 降级 + artifact_missing 展示。
 *
 * 契约（docs/plans/2026-09-13-video-gen-stale-artifact-404-fix-plan.md T4）：
 * - thumbnail_url 存在且未出错 → 渲染 <img>；
 * - 触发 onError → 降级为占位图标（同卡片不再重复请求死链）；
 * - artifact_missing=true → 显示「产物已清理」且不渲染 <img>；
 * - url 变化 → 错误态复位（React 复用同节点，useEffect 依赖 url）。
 *
 * 仓库无 @testing-library/react：沿用 video-gen-detail-warning-gate.test.tsx
 * 的 createRoot + act + happy-dom 手写 harness；直接渲染导出的 TaskThumbnail
 * （页面重依赖的 hooks/重组件按 warning-gate 先例 mock 掉以保模块可加载）。
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ back: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
}));

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}));

// video-gen-page.tsx 模块级导入的全部 hooks 命名导出（缺一个即 import 失败）
vi.mock("@/core/video-gen/hooks", () => ({
  useBGMList: () => ({ data: undefined }),
  useProviders: () => ({ data: undefined }),
  useBGMUpload: () => ({ mutate: vi.fn(), isPending: false }),
  useCourseProjects: () => ({ data: undefined }),
  useDefaultVoice: () => ({ data: undefined }),
  useDeleteVideoTask: () => ({ mutate: vi.fn(), isPending: false }),
  useExtractScript: () => ({ mutate: vi.fn(), isPending: false }),
  useRecomposeTask: () => ({ mutate: vi.fn(), isPending: false }),
  useSubmitVideo: () => ({ mutate: vi.fn(), isPending: false }),
  useVideoTasks: () => ({ data: [] }),
  useVideoVoices: () => ({ data: [] }),
  getStallWarning: () => null,
}));

// 重组件子树与 TaskThumbnail 无关——stub 掉以砍断 import 图
vi.mock("@/components/workspace/video-gen/course-project-page", () => ({
  CourseProjectPage: () => null,
}));
vi.mock("@/components/workspace/video-gen/course-project-select", () => ({
  CourseProjectSelect: () => null,
}));
vi.mock("@/components/workspace/video-gen/bgm-picker", () => ({
  BgmPicker: () => null,
}));

import { TaskThumbnail } from "@/components/workspace/video-gen/video-gen-page";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

describe("TaskThumbnail 缩略图降级（2026-09-13 死链修复 T4）", () => {
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

  async function renderThumb(props: {
    url: string | null;
    artifactMissing?: boolean;
  }) {
    await act(async () => {
      root.render(<TaskThumbnail {...props} />);
    });
  }

  it("thumbnail_url 存在且未出错 → 渲染 <img>", async () => {
    await renderThumb({ url: "/api/video/tasks/vg_1/thumbnail" });
    const img = container.querySelector<HTMLImageElement>(
      'img[data-testid="thumbnail-img"]',
    );
    expect(img).toBeTruthy();
    expect(img!.getAttribute("src")).toBe("/api/video/tasks/vg_1/thumbnail");
    expect(
      container.querySelector('[data-testid="thumbnail-placeholder"]'),
    ).toBeNull();
  });

  it("触发 onError → 降级为占位图标，不再渲染 <img>", async () => {
    await renderThumb({ url: "/api/video/tasks/vg_dead/thumbnail" });
    const img = container.querySelector<HTMLImageElement>(
      "img[data-testid='thumbnail-img']",
    );
    expect(img).toBeTruthy();
    await act(async () => {
      // error 事件不冒泡，React 对媒体事件直接挂在元素上——dispatch 即触发 onError
      img!.dispatchEvent(new Event("error"));
    });
    expect(
      container.querySelector('[data-testid="thumbnail-placeholder"]'),
    ).toBeTruthy();
    expect(container.querySelector("img")).toBeNull();
  });

  it("artifact_missing=true → 显示「产物已清理」且不渲染 <img>", async () => {
    await renderThumb({
      url: "/api/video/tasks/vg_gone/thumbnail",
      artifactMissing: true,
    });
    const badge = container.querySelector(
      '[data-testid="thumbnail-artifact-missing"]',
    );
    expect(badge).toBeTruthy();
    expect(badge!.textContent).toContain("产物已清理");
    expect(container.querySelector("img")).toBeNull();
  });

  it("url 变化 → 错误态复位（同节点复用时重新渲染 <img>）", async () => {
    await renderThumb({ url: "/api/video/tasks/vg_a/thumbnail" });
    const img = container.querySelector<HTMLImageElement>(
      'img[data-testid="thumbnail-img"]',
    );
    await act(async () => {
      img!.dispatchEvent(new Event("error"));
    });
    expect(container.querySelector("img")).toBeNull();

    await renderThumb({ url: "/api/video/tasks/vg_b/thumbnail" });
    const img2 = container.querySelector<HTMLImageElement>(
      'img[data-testid="thumbnail-img"]',
    );
    expect(img2).toBeTruthy();
    expect(img2!.getAttribute("src")).toBe("/api/video/tasks/vg_b/thumbnail");
  });

  it("url=null → 占位图标（既有行为保持）", async () => {
    await renderThumb({ url: null });
    expect(
      container.querySelector('[data-testid="thumbnail-placeholder"]'),
    ).toBeTruthy();
    expect(container.querySelector("img")).toBeNull();
  });
});
