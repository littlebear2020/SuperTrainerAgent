import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AgentShowcase } from "@/components/workspace/agent-showcase/agent-showcase";
import { showcaseFor, type ShowcaseAgentId } from "@/core/agents/showcase";
import { I18nProvider } from "@/core/i18n/context";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

describe("AgentShowcase", () => {
  let container: HTMLDivElement;
  let root: Root;
  let onUsePrompt: ReturnType<typeof vi.fn<(prompt: string) => void>>;

  beforeEach(() => {
    document.cookie = "locale=zh-CN; path=/";
    onUsePrompt = vi.fn<(prompt: string) => void>();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.restoreAllMocks();
  });

  async function renderShowcase(
    agentName: ShowcaseAgentId = "course-video",
    disabled = false,
  ) {
    const agent = showcaseFor(agentName);
    if (!agent) throw new Error("showcase not found");
    await act(async () => {
      root.render(
        <I18nProvider initialLocale="zh-CN">
          <AgentShowcase
            agent={agent}
            disabled={disabled}
            onUsePrompt={onUsePrompt}
          />
        </I18nProvider>,
      );
    });
  }

  async function click(element: HTMLElement) {
    await act(async () => {
      element.click();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }

  function element<T extends HTMLElement>(testId: string): T {
    const value = document.querySelector<T>(`[data-testid='${testId}']`);
    if (!value) throw new Error(`element not found: ${testId}`);
    return value;
  }

  it("opens a real video only in the dialog and pauses and unmounts it on close", async () => {
    const pause = vi
      .spyOn(HTMLMediaElement.prototype, "pause")
      .mockImplementation(() => {});
    await renderShowcase();
    expect(container.querySelectorAll("article")).toHaveLength(3);
    expect(document.querySelector("video")).toBeNull();
    const trigger = element("showcase-open-video-hallucination");
    trigger.focus();

    await click(trigger);
    const video = document.querySelector<HTMLVideoElement>("video");
    expect(document.querySelector("[role='dialog']")).not.toBeNull();
    expect(video?.src).toContain("/agent-showcase/videos/v1.mp4");
    expect(video?.controls).toBe(true);
    expect(video?.autoplay).toBe(false);
    expect(video?.preload).toBe("metadata");

    await click(document.querySelector<HTMLElement>("[data-testid='showcase-dialog-close']")!);
    expect(document.querySelector("[role='dialog']")).toBeNull();
    expect(document.querySelector("video")).toBeNull();
    expect(pause).toHaveBeenCalled();
    expect(document.activeElement).toBe(trigger);
  });

  it("keeps media and prompt actions separate and never auto-submits", async () => {
    await renderShowcase();
    const item = showcaseFor("course-video")!.cases[1]!;

    await click(element("showcase-prompt-video-feynman"));

    expect(onUsePrompt).toHaveBeenCalledTimes(1);
    expect(onUsePrompt).toHaveBeenCalledWith(item.prompt);
    expect(document.querySelector("[role='dialog']")).toBeNull();
    expect(document.querySelector("video")).toBeNull();
  });

  it("disables prompt actions without blocking real media previews", async () => {
    await renderShowcase("course-video", true);
    expect(element<HTMLButtonElement>("showcase-prompt-video-feynman").disabled).toBe(true);
    expect(element<HTMLButtonElement>("showcase-open-video-feynman").disabled).toBe(false);
  });

  it("replaces a broken poster with an honest media fallback", async () => {
    await renderShowcase();
    const image = container.querySelector<HTMLImageElement>(
      "[data-testid='showcase-open-video-hallucination'] img",
    );
    if (!image) throw new Error("poster not found");

    await act(async () => {
      image.dispatchEvent(new Event("error", { bubbles: true }));
    });

    expect(container.textContent).toContain("媒体暂时无法显示");
    expect(element<HTMLButtonElement>("showcase-prompt-video-hallucination").disabled).toBe(false);
  });

  it("navigates deck boundaries and resets to slide one when another deck opens", async () => {
    await renderShowcase("ppt-master");
    expect(container.querySelectorAll("article")).toHaveLength(3);

    await click(element("showcase-open-ppt-quick"));
    const current = () => document.querySelector<HTMLImageElement>("[data-testid='showcase-deck-current']");
    expect(current()?.src).toContain("/quick/slide-1.webp");
    expect(document.querySelector<HTMLButtonElement>("[data-testid='showcase-deck-previous']")?.disabled).toBe(true);

    await click(element("showcase-deck-next"));
    expect(current()?.src).toContain("/quick/slide-2.webp");
    await click(element("showcase-deck-thumbnail-6"));
    expect(current()?.src).toContain("/quick/slide-7.webp");
    expect(document.querySelector<HTMLButtonElement>("[data-testid='showcase-deck-next']")?.disabled).toBe(true);
    await click(document.querySelector<HTMLElement>("[data-testid='showcase-dialog-close']")!);

    await click(element("showcase-open-ppt-standard"));
    expect(current()?.src).toContain("/standard/slide-1.webp");
    const download = document.querySelector<HTMLAnchorElement>("[data-testid='showcase-download-pptx']");
    expect(download?.href).toContain("/agent-showcase/ppt/standard-llm-app-v2.pptx");
    expect(download?.download).toBe("ppt-standard.pptx");
  });

  it("filters visual cases with aria state and opens original and SVG downloads", async () => {
    await renderShowcase("creative-studio");
    expect(container.querySelectorAll("article")).toHaveLength(10);
    expect(container.querySelectorAll("[data-testid^='showcase-filter-']")).toHaveLength(6);

    await click(element("showcase-filter-card"));
    expect(container.querySelectorAll("article")).toHaveLength(2);
    expect(element("showcase-filter-card").getAttribute("aria-pressed")).toBe("true");
    expect(element("showcase-filter-all").getAttribute("aria-pressed")).toBe("false");

    const trigger = element("showcase-open-visual-card-rag");
    trigger.focus();
    await click(trigger);
    const preview = document.querySelector<HTMLImageElement>("[data-testid='showcase-visual-original']");
    expect(preview?.src).toContain("/visual/originals/card-rag.jpg");
    const original = document.querySelector<HTMLAnchorElement>("[data-testid='showcase-download-original']");
    expect(original?.href).toContain("/visual/originals/card-rag.jpg");
    expect(original?.download).toBe("visual-card-rag.jpg");
    await click(document.querySelector<HTMLElement>("[data-testid='showcase-dialog-close']")!);
    expect(document.activeElement).toBe(trigger);

    await click(element("showcase-filter-diagram"));
    expect(container.querySelectorAll("article")).toHaveLength(2);
    await click(element("showcase-open-visual-diagram-rag"));
    const svg = document.querySelector<HTMLAnchorElement>("[data-testid='showcase-download-svg']");
    expect(svg?.href).toContain("/visual/diagrams/diagram-rag-architecture.svg");
    expect(svg?.download).toBe("visual-diagram-rag.svg");
  });
});
