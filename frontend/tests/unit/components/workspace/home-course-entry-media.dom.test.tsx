import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  coarse: false,
  reduced: false,
  onSelect: vi.fn(),
}));

vi.mock("@/core/i18n/hooks", async () => {
  const { translations } = await import("@/core/i18n/translations");
  return { useI18n: () => ({ t: translations["zh-CN"] }) };
});

import { HOME_COURSE_ITEMS } from "@/components/workspace/home-course-catalog";
import { HomeCourseEntry } from "@/components/workspace/home-course-entry";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

describe("HomeCourseEntry six-card media behavior", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    h.coarse = false;
    h.reduced = false;
    h.onSelect.mockReset();
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn((query: string) => ({
        get matches() {
          return query.includes("pointer: coarse") ? h.coarse : h.reduced;
        },
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    });
    Object.defineProperty(document, "hidden", {
      configurable: true,
      value: false,
    });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    vi.useFakeTimers();
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.useRealTimers();
  });

  async function renderEntry() {
    await act(async () => {
      root.render(<HomeCourseEntry onSelect={h.onSelect} />);
    });
  }

  function entry(id: string): HTMLButtonElement {
    const button = container.querySelector<HTMLButtonElement>(
      `[data-course-entry='${id}']`,
    );
    if (!button) throw new Error(`missing entry ${id}`);
    return button;
  }

  function videos(): HTMLVideoElement[] {
    return [...container.querySelectorAll("video")];
  }

  it("shows all six cards with equal-row layout and no initial video", async () => {
    await renderEntry();

    expect(container.querySelectorAll("[data-course-entry]")).toHaveLength(6);
    expect(videos()).toHaveLength(0);
    const grid = container.querySelector("[data-course-entry]")!.parentElement!;
    expect(grid.className).toContain("auto-rows-fr");
    expect(grid.className).toContain("sm:grid-cols-2");
    expect(grid.className).toContain("lg:grid-cols-3");
    for (const media of container.querySelectorAll("img")) {
      expect(media.className).toContain("object-contain");
    }
  });

  it("mounts only the hovered card after 200ms and unmounts on leave and blur", async () => {
    await renderEntry();
    const button = entry("course-video");
    await act(async () => {
      button.dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
      vi.advanceTimersByTime(199);
    });
    expect(videos()).toHaveLength(0);

    await act(async () => vi.advanceTimersByTime(1));
    expect(videos()).toHaveLength(1);
    expect(videos()[0]!.dataset.courseItem).toBe("course-video");

    await act(async () => {
      button.dispatchEvent(new MouseEvent("mouseout", { bubbles: true }));
    });
    expect(videos()).toHaveLength(0);

    await act(async () => button.focus());
    expect(videos()).toHaveLength(1);
    await act(async () => button.blur());
    expect(videos()).toHaveLength(0);
  });

  it("never mounts video for reduced motion or coarse pointers", async () => {
    h.reduced = true;
    h.coarse = true;
    await renderEntry();
    await act(async () => entry("ai_image").focus());

    expect(videos()).toHaveLength(0);
  });

  it("keeps creation available after video failure and falls back to a neutral image block", async () => {
    await renderEntry();
    const button = entry("course-video");
    await act(async () => button.focus());
    await act(async () => {
      videos()[0]!.dispatchEvent(new Event("error", { bubbles: true }));
    });

    expect(container.textContent).toContain("预览暂不可用，仍可开始创作");
    await act(async () => button.click());
    expect(h.onSelect).toHaveBeenCalledWith(HOME_COURSE_ITEMS[0]);

    const staticCard = entry("creative-studio");
    await act(async () => {
      staticCard
        .querySelector("img")!
        .dispatchEvent(new Event("error", { bubbles: true }));
    });
    expect(staticCard.querySelector("img")).toBeNull();
    await act(async () => staticCard.click());
    expect(h.onSelect).toHaveBeenCalledWith(HOME_COURSE_ITEMS[3]);
  });
});
