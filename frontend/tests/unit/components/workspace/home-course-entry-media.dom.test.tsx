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

describe("HomeCourseEntry continuous media behavior", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    h.coarse = false;
    h.reduced = false;
    h.onSelect.mockReset();
    h.play.mockReset();
    h.pause.mockReset();
    h.paused = false;
    h.play.mockImplementation(function (this: HTMLMediaElement) {
      h.paused = false;
      Object.defineProperty(this, "paused", {
        configurable: true,
        value: false,
      });
      return Promise.resolve();
    });
    h.pause.mockImplementation(function (this: HTMLMediaElement) {
      h.paused = true;
      Object.defineProperty(this, "paused", {
        configurable: true,
        value: true,
      });
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
  });

  async function renderEntry() {
    await act(async () => {
      root.render(<HomeCourseEntry onSelect={h.onSelect} />);
    });
  }

  async function flushPlay() {
    await act(async () => {
      await Promise.resolve();
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

  it("mounts and plays all three videos by default with cover media", async () => {
    await renderEntry();

    expect(videos()).toHaveLength(3);
    expect(h.play).toHaveBeenCalledTimes(3);
    for (const video of videos()) {
      expect(video.autoplay).toBe(true);
      expect(video.muted).toBe(true);
      expect(video.loop).toBe(true);
      expect(video.playsInline).toBe(true);
      expect(video.className).toContain("object-cover");
    }
    for (const image of container.querySelectorAll("img")) {
      expect(image.className).toContain("object-cover");
      expect(image.className).not.toContain("object-contain");
    }
    expect(container.querySelector("[data-media-status]")).toBeNull();
  });

  it("plays on coarse pointers but shows posters for reduced motion", async () => {
    h.coarse = true;
    await renderEntry();
    expect(videos()).toHaveLength(3);

    await act(async () => root.unmount());
    h.reduced = true;
    root = createRoot(container);
    await renderEntry();
    expect(videos()).toHaveLength(0);
    expect(container.querySelectorAll("img")).toHaveLength(6);
    expect(container.querySelector("[data-media-status]")).toBeNull();
  });

  it("pauses while hidden and resumes all three videos when visible", async () => {
    await renderEntry();
    expect(h.play).toHaveBeenCalledTimes(3);

    Object.defineProperty(document, "hidden", {
      configurable: true,
      value: true,
    });
    await act(async () =>
      document.dispatchEvent(new Event("visibilitychange")),
    );
    expect(videos()).toHaveLength(0);
    expect(h.pause).toHaveBeenCalledTimes(3);

    Object.defineProperty(document, "hidden", {
      configurable: true,
      value: false,
    });
    await act(async () =>
      document.dispatchEvent(new Event("visibilitychange")),
    );
    expect(videos()).toHaveLength(3);
    expect(h.play).toHaveBeenCalledTimes(6);
  });

  it("deduplicates play across StrictMode and repeated renders", async () => {
    const item = HOME_COURSE_ITEMS[0]!;
    const onError = vi.fn();
    const onPlaying = vi.fn();
    const onPosterError = vi.fn();
    const media = (playbackAllowed: boolean) => (
      <CourseMedia
        item={item}
        playbackAllowed={playbackAllowed}
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

  it("shows the honest fallback and preserves creation when play rejects", async () => {
    h.play.mockRejectedValueOnce(
      new DOMException("Playback blocked", "NotAllowedError"),
    );
    await renderEntry();
    await flushPlay();

    const button = entry("course-video");
    expect(container.textContent).toContain("预览暂不可用，仍可开始创作");
    expect(videos()).toHaveLength(2);
    expect(button.querySelector("video")).toBeNull();
    expect(button.querySelector("img")).not.toBeNull();
    await act(async () => button.click());
    expect(h.onSelect).toHaveBeenCalledWith(HOME_COURSE_ITEMS[0]);
  });

  it("ignores only AbortError without marking media failed", async () => {
    h.play.mockRejectedValueOnce(
      new DOMException("Playback interrupted", "AbortError"),
    );
    await renderEntry();
    await flushPlay();

    expect(container.textContent).not.toContain("预览暂不可用，仍可开始创作");
    expect(videos()).toHaveLength(3);
  });

  it("retries a rejected video without losing the creation entry", async () => {
    h.play.mockRejectedValueOnce(
      new DOMException("First failure", "NotAllowedError"),
    );
    await renderEntry();
    await flushPlay();
    expect(container.textContent).toContain("预览暂不可用");

    const retry = container.querySelector<HTMLButtonElement>(
      "[data-course-retry='course-video']",
    );
    expect(retry).not.toBeNull();
    expect(entry("course-video").contains(retry)).toBe(false);
    await act(async () => retry!.click());
    await flushPlay();

    expect(entry("course-video").textContent).not.toContain("预览暂不可用");
    expect(document.activeElement).toBe(entry("course-video"));
    expect(videos()).toHaveLength(3);
    expect(h.play).toHaveBeenCalledTimes(4);
    await act(async () => entry("course-video").click());
    expect(h.onSelect).toHaveBeenCalledWith(HOME_COURSE_ITEMS[0]);
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
    expect(videos()).toHaveLength(3);
    expect(container.textContent).not.toContain("预览暂不可用，仍可开始创作");
  });

  it("keeps creation available after media errors and static fallback", async () => {
    await renderEntry();
    const button = entry("course-video");
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
