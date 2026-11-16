import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AgentShowcase } from "@/components/workspace/agent-showcase/agent-showcase";
import { showcaseFor, type ShowcaseAgentId } from "@/core/agents/showcase";
import { I18nProvider } from "@/core/i18n/context";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function mockPause() {
  return vi
    .spyOn(HTMLMediaElement.prototype, "pause")
    .mockImplementation(function (this: HTMLMediaElement) {
      Object.defineProperty(this, "paused", {
        configurable: true,
        value: true,
      });
    });
}

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
    const pause = mockPause();
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

    await click(
      document.querySelector<HTMLElement>(
        "[data-testid='showcase-dialog-close']",
      )!,
    );
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
    expect(
      element<HTMLButtonElement>("showcase-prompt-video-feynman").disabled,
    ).toBe(true);
    expect(
      element<HTMLButtonElement>("showcase-open-video-feynman").disabled,
    ).toBe(false);
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
    expect(
      element<HTMLButtonElement>("showcase-prompt-video-hallucination")
        .disabled,
    ).toBe(false);
  });

  it("navigates deck boundaries and resets to slide one when another deck opens", async () => {
    await renderShowcase("ppt-master");
    expect(container.querySelectorAll("article")).toHaveLength(3);

    await click(element("showcase-open-ppt-quick"));
    const current = () =>
      document.querySelector<HTMLImageElement>(
        "[data-testid='showcase-deck-current']",
      );
    expect(current()?.src).toContain("/quick/slide-1.webp");
    expect(
      document.querySelector<HTMLButtonElement>(
        "[data-testid='showcase-deck-previous']",
      )?.disabled,
    ).toBe(true);

    await click(element("showcase-deck-next"));
    expect(current()?.src).toContain("/quick/slide-2.webp");
    await click(element("showcase-deck-thumbnail-6"));
    expect(current()?.src).toContain("/quick/slide-7.webp");
    expect(
      document.querySelector<HTMLButtonElement>(
        "[data-testid='showcase-deck-next']",
      )?.disabled,
    ).toBe(true);
    await click(
      document.querySelector<HTMLElement>(
        "[data-testid='showcase-dialog-close']",
      )!,
    );

    await click(element("showcase-open-ppt-standard"));
    expect(current()?.src).toContain("/standard/slide-1.webp");
    const download = document.querySelector<HTMLAnchorElement>(
      "[data-testid='showcase-download-pptx']",
    );
    expect(download?.href).toContain(
      "/agent-showcase/ppt/standard-llm-app-v2.pptx",
    );
    expect(download?.download).toBe("ppt-standard.pptx");
  });

  it("filters visual cases with aria state and opens original and SVG downloads", async () => {
    await renderShowcase("creative-studio");
    expect(container.querySelectorAll("article")).toHaveLength(10);
    expect(
      container.querySelectorAll("[data-testid^='showcase-filter-']"),
    ).toHaveLength(6);

    await click(element("showcase-filter-card"));
    expect(container.querySelectorAll("article")).toHaveLength(2);
    expect(element("showcase-filter-card").getAttribute("aria-pressed")).toBe(
      "true",
    );
    expect(element("showcase-filter-all").getAttribute("aria-pressed")).toBe(
      "false",
    );

    const trigger = element("showcase-open-visual-card-rag");
    trigger.focus();
    await click(trigger);
    const preview = document.querySelector<HTMLImageElement>(
      "[data-testid='showcase-visual-original']",
    );
    expect(preview?.src).toContain("/visual/originals/card-rag.jpg");
    const original = document.querySelector<HTMLAnchorElement>(
      "[data-testid='showcase-download-original']",
    );
    expect(original?.href).toContain("/visual/originals/card-rag.jpg");
    expect(original?.download).toBe("visual-card-rag.jpg");
    await click(
      document.querySelector<HTMLElement>(
        "[data-testid='showcase-dialog-close']",
      )!,
    );
    expect(document.activeElement).toBe(trigger);

    await click(element("showcase-filter-diagram"));
    expect(container.querySelectorAll("article")).toHaveLength(2);
    await click(element("showcase-open-visual-diagram-rag"));
    const svg = document.querySelector<HTMLAnchorElement>(
      "[data-testid='showcase-download-svg']",
    );
    expect(svg?.href).toContain(
      "/visual/diagrams/diagram-rag-architecture.svg",
    );
    expect(svg?.download).toBe("visual-diagram-rag.svg");
  });

  it("plays podcasts through one shared audio element with progress and exclusive switching", async () => {
    const play = vi
      .spyOn(HTMLMediaElement.prototype, "play")
      .mockResolvedValue();
    const pause = mockPause();
    await renderShowcase("podcast-studio");
    expect(container.querySelectorAll("article")).toHaveLength(4);
    const audio = document.querySelector<HTMLAudioElement>("audio");
    expect(audio?.preload).toBe("none");
    expect(audio?.getAttribute("src")).toBeNull();

    await click(element("showcase-play-podcast-context"));
    expect(play).toHaveBeenCalledTimes(1);
    expect(audio?.src).toContain("/podcast/ep1.mp3");
    expect(
      element("showcase-play-podcast-context").getAttribute("aria-label"),
    ).toContain("暂停");
    Object.defineProperty(audio, "currentTime", {
      configurable: true,
      value: 12,
    });
    await act(async () => {
      audio?.dispatchEvent(new Event("timeupdate"));
    });
    expect(
      element<HTMLProgressElement>("showcase-progress-podcast-context").value,
    ).toBe(12);

    await click(element("showcase-play-podcast-agent2026"));
    expect(play).toHaveBeenCalledTimes(2);
    expect(pause).toHaveBeenCalled();
    expect(audio?.src).toContain("/podcast/ep2.mp3");
    expect(
      element("showcase-play-podcast-context").getAttribute("aria-label"),
    ).toContain("播放");
    expect(
      element("showcase-play-podcast-agent2026").getAttribute("aria-label"),
    ).toContain("暂停");
    expect(document.querySelectorAll("audio")).toHaveLength(1);
  });

  it("ignores stale play promises after a rapid podcast switch", async () => {
    const pending: {
      resolve: () => void;
      reject: (reason?: unknown) => void;
    }[] = [];
    vi.spyOn(HTMLMediaElement.prototype, "play").mockImplementation(
      () =>
        new Promise<void>((resolve, reject) =>
          pending.push({ resolve, reject }),
        ),
    );
    mockPause();
    await renderShowcase("podcast-studio");

    await click(element("showcase-play-podcast-context"));
    await click(element("showcase-play-podcast-agent2026"));
    expect(pending).toHaveLength(2);
    await act(async () => {
      pending[0]!.reject(new DOMException("Interrupted", "AbortError"));
      await Promise.resolve();
    });
    expect(container.textContent).not.toContain("音频暂时无法播放");
    await act(async () => {
      pending[1]!.resolve();
      await Promise.resolve();
    });
    expect(
      element("showcase-play-podcast-agent2026").getAttribute("aria-label"),
    ).toContain("暂停");
    expect(
      element("showcase-play-podcast-context").getAttribute("aria-label"),
    ).toContain("播放");
  });

  it("handles rejected playback, ended, media errors and unmount cleanup", async () => {
    const play = vi
      .spyOn(HTMLMediaElement.prototype, "play")
      .mockRejectedValueOnce(new DOMException("Denied", "NotAllowedError"))
      .mockResolvedValue();
    const pause = mockPause();
    await renderShowcase("podcast-studio");

    await click(element("showcase-play-podcast-context"));
    expect(container.textContent).toContain("音频暂时无法播放，请重试。");
    await click(element("showcase-play-podcast-context"));
    expect(play).toHaveBeenCalledTimes(2);
    const audio = document.querySelector<HTMLAudioElement>("audio");
    Object.defineProperty(audio, "currentTime", {
      configurable: true,
      value: 9,
    });
    await act(async () => {
      audio?.dispatchEvent(new Event("timeupdate"));
      audio?.dispatchEvent(new Event("ended"));
    });
    expect(
      element("showcase-play-podcast-context").getAttribute("aria-label"),
    ).toContain("播放");
    expect(
      element<HTMLProgressElement>("showcase-progress-podcast-context").value,
    ).toBe(0);

    await act(async () => {
      audio?.dispatchEvent(new Event("error"));
    });
    expect(container.textContent).toContain("音频暂时无法播放，请重试。");
    pause.mockClear();
    await act(async () => {
      root.render(<></>);
    });
    expect(pause).toHaveBeenCalled();
  });

  it("opens transcripts with safe source links and keeps media enabled when prompts are disabled", async () => {
    await renderShowcase("podcast-studio", true);
    expect(
      element<HTMLButtonElement>("showcase-prompt-podcast-agent2026").disabled,
    ).toBe(true);
    expect(
      element<HTMLButtonElement>("showcase-play-podcast-agent2026").disabled,
    ).toBe(false);
    const trigger = element("showcase-transcript-podcast-agent2026");
    trigger.focus();

    await click(trigger);
    expect(document.body.textContent).toContain("2026 年，AI Agent 的关键词");
    expect(document.body.textContent).toContain("InfoQ 2026 趋势报告");
    const source = document.querySelector<HTMLAnchorElement>(
      "[data-testid='showcase-podcast-source-0']",
    );
    expect(source?.target).toBe("_blank");
    expect(source?.rel).toContain("noreferrer");
    expect(source?.rel).toContain("noopener");
    const download = document.querySelector<HTMLAnchorElement>(
      "[data-testid='showcase-download-mp3']",
    );
    expect(download?.href).toContain("/podcast/ep2.mp3");
    await click(
      document.querySelector<HTMLElement>(
        "[data-testid='showcase-dialog-close']",
      )!,
    );
    expect(document.activeElement).toBe(trigger);
  });
});
