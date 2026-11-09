import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { HomeCourseEntry } from "@/components/workspace/home-course-entry";
import { translations } from "@/core/i18n/translations";

vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({ t: translations["zh-CN"] }),
}));

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

let mediaQuery: {
  matches: boolean;
  addEventListener: ReturnType<typeof vi.fn>;
  removeEventListener: ReturnType<typeof vi.fn>;
};
let playSpy: ReturnType<typeof vi.spyOn>;
let pauseSpy: ReturnType<typeof vi.spyOn>;

function button(text: string): HTMLButtonElement {
  const result = Array.from(document.querySelectorAll("button")).find((item) =>
    item.textContent?.includes(text),
  );
  if (!result) throw new Error(`button not found: ${text}`);
  return result;
}

describe("HomeCourseEntry", () => {
  let container: HTMLDivElement;
  let root: Root;
  let onSelect: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    mediaQuery = {
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    };
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn(() => mediaQuery),
    });
    Object.defineProperty(document, "hidden", {
      configurable: true,
      value: false,
    });
    playSpy = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
    pauseSpy = vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    onSelect = vi.fn();
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  async function render() {
    await act(async () => {
      root.render(<HomeCourseEntry onSelect={onSelect} />);
    });
  }

  it("defaults to video course and exposes three cards", async () => {
    await render();
    expect(button("视频课").getAttribute("aria-expanded")).toBe("true");
    expect(button("图文课").getAttribute("aria-expanded")).toBe("false");
    for (const title of ["Remotion动效", "图片动效", "AI视频"]) {
      expect(button(title)).toBeDefined();
    }
    expect(document.querySelector("#home-course-panel-video")).not.toBeNull();
    expect(document.querySelector("#home-course-panel-text")).toBeNull();
  });

  it("switches to text course and pauses hidden panel video", async () => {
    await render();
    expect(playSpy).toHaveBeenCalledTimes(3);
    await act(async () => button("图文课").click());
    for (const title of ["海报制作", "PPT制作", "文章写作"]) {
      expect(button(title)).toBeDefined();
    }
    expect(document.querySelector("#home-course-panel-video")).toBeNull();
    expect(pauseSpy).toHaveBeenCalledTimes(3);
    await act(async () => button("视频课").click());
    expect(document.querySelector("#home-course-panel-text")).toBeNull();
    expect(playSpy).toHaveBeenCalledTimes(6);
  });

  it("reduced motion never autoplays and change event pauses current video", async () => {
    mediaQuery.matches = true;
    await render();
    expect(playSpy).not.toHaveBeenCalled();
    pauseSpy.mockClear();

    Object.defineProperty(document, "hidden", { configurable: true, value: true });
    await act(async () => document.dispatchEvent(new Event("visibilitychange")));
    expect(pauseSpy).toHaveBeenCalledTimes(3);

    Object.defineProperty(document, "hidden", { configurable: true, value: false });
    await act(async () => document.dispatchEvent(new Event("visibilitychange")));
    mediaQuery.matches = false;
    await act(async () => {
      mediaQuery.addEventListener.mock.calls[0]?.[1]?.(new Event("change"));
    });
    expect(playSpy).toHaveBeenCalledTimes(3);

    mediaQuery.matches = true;
    await act(async () => {
      mediaQuery.addEventListener.mock.calls[0]?.[1]?.(new Event("change"));
    });
    expect(pauseSpy).toHaveBeenCalled();
  });

  it("play rejection and media error show fallback without disabling entry", async () => {
    playSpy.mockRejectedValue(new Error("not allowed"));
    await render();
    const video = document.querySelector("video");
    expect(video).not.toBeNull();
    await act(async () => video!.dispatchEvent(new Event("error")));
    expect(document.body.textContent).toContain("视频暂时无法播放");
    await act(async () => button("Remotion动效").click());
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect.mock.calls[0]?.[0].id).toBe("course-video");
  });
});
