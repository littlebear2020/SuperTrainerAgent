import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  search: "",
  push: vi.fn(),
  replace: vi.fn(),
  tasksCalls: 0,
  workType: undefined as string | undefined,
  agentName: undefined as string | undefined,
  agentLabel: undefined as string | undefined,
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: h.push, replace: h.replace }),
  useSearchParams: () => new URLSearchParams(h.search),
}));

vi.mock("@/core/i18n/hooks", async () => {
  const { translations } = await import("@/core/i18n/translations");
  return { useI18n: () => ({ t: translations["zh-CN"] }) };
});

vi.mock("@/core/video-gen/hooks", async () => {
  const actual = await vi.importActual<Record<string, unknown>>(
    "@/core/video-gen/hooks",
  );
  return {
    ...actual,
    useVideoTasks: () => {
      h.tasksCalls += 1;
      return { data: [], isLoading: false, isError: false };
    },
    useDeleteVideoTask: () => ({ mutate: vi.fn() }),
  };
});

vi.mock("@/components/workspace/video-gen/completed-video-grid", () => ({
  CompletedVideoGrid: ({ workType }: { workType: string }) => {
    h.workType = workType;
    return <div data-testid="works-panel">{workType}</div>;
  },
}));

vi.mock("@/components/workspace/video-gen/agent-conversation-grid", () => ({
  AgentConversationGrid: ({
    agentName,
    label,
  }: {
    agentName: string;
    label: string;
  }) => {
    h.agentName = agentName;
    h.agentLabel = label;
    return (
      <div data-testid="agent-panel">
        {agentName}:{label}
      </div>
    );
  },
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
    h.tasksCalls = 0;
    h.workType = undefined;
    h.agentName = undefined;
    h.agentLabel = undefined;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });

  async function renderPage() {
    window.history.replaceState(
      null,
      "",
      `/workspace/video-gen${h.search ? `?${h.search}` : ""}`,
    );
    await act(async () => root.render(<VideoGenPage />));
  }

  function tab(value: "works" | "assets"): HTMLButtonElement {
    const element = container.querySelector<HTMLButtonElement>(
      `#video-assets-tab-${value}`,
    );
    if (!element) throw new Error(`missing ${value} tab`);
    return element;
  }

  function capabilityTab(value: string): HTMLButtonElement {
    const element = container.querySelector<HTMLButtonElement>(
      `#work-capability-tab-${value}`,
    );
    if (!element) throw new Error(`missing ${value} capability tab`);
    return element;
  }

  function key(element: HTMLButtonElement, key: string) {
    element.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
  }

  it("derives outer panels from the URL and only queries videos for video works", async () => {
    h.search = "tab=works&capability=ai_image";
    await renderPage();
    expect(
      container.querySelector("[data-testid='works-panel']"),
    ).not.toBeNull();
    expect(h.workType).toBe("ai_image");
    expect(h.tasksCalls).toBeGreaterThan(0);

    await act(async () => tab("assets").click());
    expect(h.push).toHaveBeenCalledWith("/workspace/video-gen?tab=assets");

    const beforeAssets = h.tasksCalls;
    h.search = "tab=assets&capability=ai_video";
    await renderPage();
    expect(
      container.querySelector("[data-testid='assets-panel']"),
    ).not.toBeNull();
    expect(h.tasksCalls).toBe(beforeAssets);

    await act(async () => tab("works").click());
    expect(h.push).toHaveBeenCalledWith("/workspace/video-gen");
    h.search = "";
    h.agentName = undefined;
    await renderPage();
    expect(
      container.querySelector("[data-testid='agent-panel']"),
    ).not.toBeNull();
    expect(container.querySelector("[data-testid='works-panel']")).toBeNull();
    expect(h.agentName).toBe("course-video");
    expect(h.tasksCalls).toBe(beforeAssets);
  });

  it("preserves tracking params while replacing invalid values without a loop", async () => {
    h.search = "foo=1&utm_source=review&tab=invalid&capability=invalid";
    await renderPage();

    expect(h.replace).toHaveBeenCalledExactlyOnceWith(
      "/workspace/video-gen?foo=1&utm_source=review",
    );
    expect(h.agentName).toBe("course-video");

    h.replace.mockClear();
    h.search = "foo=1&utm_source=review";
    await renderPage();
    expect(h.replace).not.toHaveBeenCalled();
  });

  it("preserves unrelated params when selecting tabs and capabilities", async () => {
    h.search = "foo=1&utm_source=review";
    await renderPage();
    await act(async () => tab("assets").click());
    expect(h.push).toHaveBeenCalledWith(
      "/workspace/video-gen?foo=1&utm_source=review&tab=assets",
    );

    h.search = "foo=1&utm_source=review&tab=assets&capability=ai_video";
    h.push.mockClear();
    await renderPage();
    await act(async () => tab("works").click());
    expect(h.push).toHaveBeenCalledWith(
      "/workspace/video-gen?foo=1&utm_source=review",
    );

    h.search = "foo=1&utm_source=review";
    h.push.mockClear();
    await renderPage();
    await act(async () => capabilityTab("ai_image").click());
    expect(h.push).toHaveBeenCalledWith(
      "/workspace/video-gen?foo=1&utm_source=review&tab=works&capability=ai_image",
    );
  });

  it("replaces invalid route values with the canonical bare works URL", async () => {
    h.search = "tab=works&capability=invalid";
    await renderPage();

    expect(h.replace).toHaveBeenCalledWith("/workspace/video-gen");
    expect(h.agentName).toBe("course-video");
  });

  it("supports arrow, Home and End keys on outer tabs", async () => {
    await renderPage();
    await act(async () => key(tab("works"), "ArrowRight"));
    expect(h.push).toHaveBeenLastCalledWith("/workspace/video-gen?tab=assets");
    h.push.mockClear();
    await act(async () => key(tab("works"), "End"));
    expect(h.push).toHaveBeenCalledExactlyOnceWith(
      "/workspace/video-gen?tab=assets",
    );

    h.search = "tab=assets";
    h.push.mockClear();
    await renderPage();
    await act(async () => key(tab("assets"), "ArrowLeft"));
    expect(h.push).toHaveBeenLastCalledWith("/workspace/video-gen");
    h.push.mockClear();
    await act(async () => key(tab("assets"), "Home"));
    expect(h.push).toHaveBeenCalledExactlyOnceWith("/workspace/video-gen");
  });

  it("renders all six i18n capabilities with roving ARIA and pushes selection", async () => {
    await renderPage();
    const labels = [
      "文字动效",
      "图片动效",
      "AI视频",
      "海报制作",
      "PPT制作",
      "播客制作",
    ];
    const tabs = Array.from(
      container.querySelectorAll<HTMLButtonElement>(
        "#work-capability-tabs [role='tab']",
      ),
    );
    expect(tabs.map((item) => item.textContent)).toEqual(labels);
    expect(tabs.map((item) => item.tabIndex)).toEqual([0, -1, -1, -1, -1, -1]);
    expect(capabilityTab("course-video").getAttribute("aria-selected")).toBe(
      "true",
    );
    expect(
      container
        .querySelector("#work-capability-panel-course-video")
        ?.getAttribute("aria-labelledby"),
    ).toBe("work-capability-tab-course-video");

    await act(async () => capabilityTab("ai_image").click());
    expect(h.push).toHaveBeenCalledWith(
      "/workspace/video-gen?tab=works&capability=ai_image",
    );
    h.search = "tab=works&capability=ai_image";
    await renderPage();
    expect(h.workType).toBe("ai_image");
    expect(capabilityTab("ai_image").getAttribute("tabindex")).toBe("0");
  });

  it("supports arrows, wrapping, Home and End on capability tabs", async () => {
    await renderPage();
    await act(async () => key(capabilityTab("course-video"), "End"));
    expect(h.push).toHaveBeenLastCalledWith(
      "/workspace/video-gen?tab=works&capability=podcast-studio",
    );

    h.search = "tab=works&capability=podcast-studio";
    await renderPage();
    await act(async () => key(capabilityTab("podcast-studio"), "ArrowRight"));
    expect(h.push).toHaveBeenLastCalledWith("/workspace/video-gen");
    await act(async () => key(capabilityTab("podcast-studio"), "Home"));
    expect(h.push).toHaveBeenLastCalledWith("/workspace/video-gen");
    await act(async () => key(capabilityTab("podcast-studio"), "ArrowLeft"));
    expect(h.push).toHaveBeenLastCalledWith(
      "/workspace/video-gen?tab=works&capability=ppt-master",
    );
    expect(h.agentName).toBe("podcast-studio");
    expect(h.tasksCalls).toBe(0);
  });
});
