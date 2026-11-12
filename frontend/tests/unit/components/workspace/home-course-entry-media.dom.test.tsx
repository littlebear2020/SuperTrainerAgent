import { act, StrictMode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  coarse: false,
  reduced: false,
  onSelect: vi.fn(),
  play: vi.fn(),
  pause: vi.fn(),
  paused: false,
}));

vi.mock("@/core/i18n/hooks", async () => {
  const { translations } = await import("@/core/i18n/translations");
  return { useI18n: () => ({ t: translations["zh-CN"] }) };
});

import { HOME_COURSE_ITEMS } from "@/components/workspace/home-course-catalog";
import {
  CourseMedia,
  HomeCourseEntry,
} from "@/components/workspace/home-course-entry";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const originalPlayDescriptor = Object.getOwnPropertyDescriptor(
  HTMLMediaElement.prototype,
  "play",
);
const originalPauseDescriptor = Object.getOwnPropertyDescriptor(
  HTMLMediaElement.prototype,
  "pause",
);

describe("HomeCourseEntry six-card media behavior", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    h.coarse = false;
    h.reduced = false;
    h.onSelect.mockReset();
    h.play.mockReset();
    h.pause.mockReset();
    h.paused = false;
    h.play.mockImplementation(() => {
      h.paused = false;
      return Promise.resolve();
    });
    h.pause.mockImplementation(() => {
      h.paused = true;
    });
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
    Object.defineProperty(HTMLMediaElement.prototype, "play", {
      configurable: true,
      writable: true,
      value: h.play,
    });
    Object.defineProperty(HTMLMediaElement.prototype, "pause", {
      configurable: true,
      writable: true,
      value: h.pause,
    });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    vi.useFakeTimers();
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    if (originalPlayDescriptor) {
      Object.defineProperty(
        HTMLMediaElement.prototype,
        "play",
        originalPlayDescriptor,
      );
    }
    if (originalPauseDescriptor) {
      Object.defineProperty(
        HTMLMediaElement.prototype,
        "pause",
        originalPauseDescriptor,
      );
    }
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

  it("shows the honest fallback and preserves creation when play rejects", async () => {
    h.play.mockRejectedValue(
      new DOMException("Playback blocked", "NotAllowedError"),
    );
    await renderEntry();
    const button = entry("course-video");
    await act(async () => button.focus());
    await act(async () => {
      await Promise.resolve();
    });

    expect(container.textContent).toContain("预览暂不可用，仍可开始创作");
    expect(h.play).toHaveBeenCalled();
    expect(videos()).toHaveLength(0);
    expect(button.querySelector("img")).not.toBeNull();
    await act(async () => button.click());
    expect(h.onSelect).toHaveBeenCalledWith(HOME_COURSE_ITEMS[0]);
  });

  it("ignores only AbortError from play without marking media failed", async () => {
    h.play.mockRejectedValue(
      new DOMException("Playback interrupted", "AbortError"),
    );
    await renderEntry();
    await act(async () => entry("course-video").focus());
    await act(async () => {
      await Promise.resolve();
    });

    expect(h.play).toHaveBeenCalled();
    expect(container.textContent).not.toContain("预览暂不可用，仍可开始创作");
    expect(videos()).toHaveLength(1);
  });

  it("does not clear a fresh failure when queued hover follows a fast focus switch", async () => {
    await renderEntry();
    await act(async () => entry("course-video").focus());
    expect(h.play).toHaveBeenCalledTimes(1);

    h.play.mockRejectedValue(
      new DOMException("Playback blocked", "NotAllowedError"),
    );
    const next = entry("ai_image");
    await act(async () => {
      next.focus();
      next.dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
      await Promise.resolve();
    });
    expect(container.textContent).toContain("预览暂不可用，仍可开始创作");
    const callsBeforeTimer = h.play.mock.calls.length;

    await act(async () => {
      vi.advanceTimersByTime(200);
      await Promise.resolve();
    });

    expect(h.play).toHaveBeenCalledTimes(callsBeforeTimer);
    expect(container.textContent).toContain("预览暂不可用，仍可开始创作");
  });

  it("deduplicates play across StrictMode and repeated renders", async () => {
    const item = HOME_COURSE_ITEMS[0]!;
    const onError = vi.fn();
    const onPlaying = vi.fn();
    const onPosterError = vi.fn();
    const media = (preview: boolean) => (
      <CourseMedia
        item={item}
        preview={preview}
        failed={false}
        posterFailed={false}
        onError={onError}
        onPlaying={onPlaying}
        onPosterError={onPosterError}
      />
    );
    await act(async () => {
      root.render(<StrictMode>{media(true)}</StrictMode>);
    });

    expect(h.paused).toBe(false);
    expect(h.play).toHaveBeenCalledTimes(2);
    expect(h.pause).toHaveBeenCalledTimes(1);
    await act(async () => {
      root.render(<StrictMode>{media(true)}</StrictMode>);
    });
    expect(h.play).toHaveBeenCalledTimes(2);

    await act(async () => {
      root.render(<StrictMode>{media(false)}</StrictMode>);
    });
    expect(h.paused).toBe(true);
    await act(async () => {
      root.render(<StrictMode>{media(true)}</StrictMode>);
    });
    expect(h.paused).toBe(false);
    expect(h.play).toHaveBeenCalledTimes(3);
  });

  it("retries and clears the failure after a new hover cycle", async () => {
    h.play
      .mockRejectedValueOnce(
        new DOMException("First failure", "NotAllowedError"),
      )
      .mockResolvedValue(undefined);
    await renderEntry();
    const button = entry("course-video");
    await act(async () => button.focus());
    await act(async () => {
      await Promise.resolve();
    });
    expect(container.textContent).toContain("预览暂不可用，仍可开始创作");

    await act(async () => button.blur());
    await act(async () => button.focus());
    await act(async () => {
      await Promise.resolve();
    });

    expect(h.play).toHaveBeenCalledTimes(2);
    expect(container.textContent).not.toContain("预览暂不可用，仍可开始创作");
    expect(videos()).toHaveLength(1);
  });

  it("keeps video preview available when only the poster fails", async () => {
    await renderEntry();
    const button = entry("course-video");
    await act(async () => {
      button
        .querySelector("img")!
        .dispatchEvent(new Event("error", { bubbles: true }));
    });
    expect(button.querySelector("img")).toBeNull();

    await act(async () => button.focus());
    expect(videos()).toHaveLength(1);
    expect(h.play).toHaveBeenCalled();
    expect(container.textContent).not.toContain("预览暂不可用，仍可开始创作");
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
