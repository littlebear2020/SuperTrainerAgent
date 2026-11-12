import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  search: "",
  push: vi.fn(),
  replace: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: h.push, replace: h.replace }),
  useSearchParams: () => new URLSearchParams(h.search),
}));

vi.mock("@/core/video-gen/hooks", async () => {
  const actual = await vi.importActual<Record<string, unknown>>(
    "@/core/video-gen/hooks",
  );
  return {
    ...actual,
    useVideoTasks: () => ({ data: [], isLoading: false, isError: false }),
    useDeleteVideoTask: () => ({ mutate: vi.fn() }),
  };
});

vi.mock("@/components/workspace/video-gen/completed-video-grid", () => ({
  CompletedVideoGrid: () => <div data-testid="works-panel" />,
}));

vi.mock("@/components/workspace/video-gen/course-project-page", () => ({
  CourseProjectPage: () => <div data-testid="assets-panel" />,
}));

import { VideoGenPage } from "@/components/workspace/video-gen/video-gen-page";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

describe("VideoGenPage URL routing", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    h.search = "";
    h.push.mockReset();
    h.replace.mockReset();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });

  async function renderPage() {
    await act(async () => root.render(<VideoGenPage />));
  }

  function tab(value: "works" | "assets"): HTMLButtonElement {
    const element = container.querySelector<HTMLButtonElement>(
      `#video-assets-tab-${value}`,
    );
    if (!element) throw new Error(`missing ${value} tab`);
    return element;
  }

  it("derives works and assets panels from the URL and pushes tab changes", async () => {
    await renderPage();
    expect(
      container.querySelector("[data-testid='works-panel']"),
    ).not.toBeNull();

    await act(async () => tab("assets").click());
    expect(h.push).toHaveBeenCalledWith("/workspace/video-gen?tab=assets");

    h.search = "tab=assets&capability=ai_video";
    await renderPage();
    expect(
      container.querySelector("[data-testid='assets-panel']"),
    ).not.toBeNull();

    await act(async () => tab("works").click());
    expect(h.push).toHaveBeenCalledWith("/workspace/video-gen");
  });

  it("replaces invalid route values with the canonical bare works URL", async () => {
    h.search = "tab=works&capability=invalid";
    await renderPage();

    expect(h.replace).toHaveBeenCalledWith("/workspace/video-gen");
  });

  it("supports keyboard tab changes through the same URL guard", async () => {
    await renderPage();
    await act(async () => {
      tab("works").dispatchEvent(
        new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }),
      );
    });

    expect(h.push).toHaveBeenCalledWith("/workspace/video-gen?tab=assets");
  });
});
