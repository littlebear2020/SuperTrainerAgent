import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { HomeCourseItem } from "@/components/workspace/home-course-catalog";
import { HomeCourseEntry } from "@/components/workspace/home-course-entry";
import { translations } from "@/core/i18n/translations";

vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({ t: translations["zh-CN"] }),
}));

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

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
  let onSelect: ReturnType<typeof vi.fn<(item: HomeCourseItem) => void>>;
  let playSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn(() => ({
        matches: false,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    });
    Object.defineProperty(document, "hidden", {
      configurable: true,
      value: false,
    });
    playSpy = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
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

  it("shows all six capabilities without categories or initial playback", async () => {
    await render();

    expect(container.querySelectorAll("[data-course-entry]")).toHaveLength(6);
    expect(container.querySelectorAll("video")).toHaveLength(0);
    expect(playSpy).not.toHaveBeenCalled();
    expect(container.textContent).not.toContain("图文音频课");
    expect(container.textContent).not.toContain("视频课");
  });

  it("uses the confirmed titles and real artwork without technical badges", async () => {
    await render();

    for (const [title, src] of [
      ["海报制作", "/course-samples/knowledge-card.webp"],
      ["PPT制作", "/course-samples/ppt-work.webp"],
      ["播客制作", "/course-samples/podcast-work.webp"],
    ] as const) {
      const card = button(title);
      expect(card.querySelector("img")?.getAttribute("src")).toBe(src);
      expect(card.querySelector("svg")).not.toBeNull();
    }
    for (const locale of ["zh-CN", "en-US"] as const) {
      const copy =
        translations[locale].workspace.home.courseItems["course-video"];
      expect(copy.title).toBe(
        locale === "zh-CN" ? "文字动效" : "Text animation",
      );
      expect(copy.mediaAlt).not.toContain("Remotion");
      expect("badge" in copy).toBe(false);
    }
  });

  it("unmounts the active preview when the document becomes hidden", async () => {
    await render();
    await act(async () => button("文字动效").focus());
    expect(container.querySelectorAll("video")).toHaveLength(1);

    Object.defineProperty(document, "hidden", {
      configurable: true,
      value: true,
    });
    await act(async () =>
      document.dispatchEvent(new Event("visibilitychange")),
    );
    expect(container.querySelectorAll("video")).toHaveLength(0);

    Object.defineProperty(document, "hidden", {
      configurable: true,
      value: false,
    });
    await act(async () =>
      document.dispatchEvent(new Event("visibilitychange")),
    );
    expect(container.querySelectorAll("video")).toHaveLength(0);
  });
});
