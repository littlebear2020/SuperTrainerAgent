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
    vi.useRealTimers();
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

  // One row per agent; each slice appends its dialog as it gains the CTA.
  const dialogCases = [
    {
      agent: "creative-studio",
      open: "showcase-open-visual-poster-agent",
      promptId: "visual-poster-agent",
      hasVideo: false,
      label: "按此风格创作",
    },
    {
      agent: "course-video",
      open: "showcase-open-video-hallucination",
      promptId: "video-hallucination",
      hasVideo: true,
      label: "按风格创作",
    },
    {
      agent: "ppt-master",
      open: "showcase-open-ppt-quick",
      promptId: "ppt-quick",
      hasVideo: false,
      label: "按此出稿",
    },
    {
      agent: "podcast-studio",
      open: "showcase-transcript-podcast-context",
      promptId: "podcast-context",
      hasVideo: false,
      label: "套用原文朗读",
    },
  ] as const;

  it.each(dialogCases)(
    "fills the composer from the $agent dialog CTA after closing its preview",
    async ({ agent, open, promptId, hasVideo, label }) => {
      const pause = mockPause();
      await renderShowcase(agent);
      const item = showcaseFor(agent)!.cases.find(
        (entry) => entry.id === promptId,
      )!;
      const trigger = element(open);
      trigger.focus();
      const returnFocus = vi.spyOn(trigger, "focus");
      await click(trigger);

      const cta = element<HTMLButtonElement>("showcase-use-style");
      expect(cta.textContent).toContain(label);
      expect(cta.getAttribute("aria-label")).toBe(`${label}: ${item.title.zh}`);
      await click(cta);

      // The preview closes first: no dialog and no media element remain.
      expect(document.querySelector("[role='dialog']")).toBeNull();
      expect(document.querySelector("video")).toBeNull();
      expect(onUsePrompt).toHaveBeenCalledTimes(1);
      expect(onUsePrompt).toHaveBeenCalledWith(item.prompt);
      // Focus moves on to the composer, not back to the card.
      expect(returnFocus).not.toHaveBeenCalled();
      if (hasVideo) expect(pause).toHaveBeenCalled();
    },
  );

  it("labels card prompt actions with the case action label", async () => {
    await renderShowcase("creative-studio");
    const item = showcaseFor("creative-studio")!.cases[0]!;
    const button = element<HTMLButtonElement>(
      "showcase-prompt-visual-poster-agent",
    );
    expect(button.textContent).toContain("按此风格创作");
    expect(button.getAttribute("aria-label")).toBe(
      `按此风格创作: ${item.title.zh}`,
    );
    // Diagrams advertise the editable SVG output on their badge.
    expect(element("showcase-open-visual-diagram-stack").textContent).toContain(
      "SVG 可编辑",
    );
  });

  it("marks the deck style row and the case-brand note as visual facts", async () => {
    await renderShowcase("ppt-master");
    // The style is what the template reuses; say so on the card.
    expect(element("showcase-case-ppt-quick").textContent).toContain(
      "视觉：轻量科技 · 暖纸钴蓝",
    );
    await click(element("showcase-open-ppt-template"));
    const note = element("showcase-recipe").textContent ?? "";
    expect(note).toContain("不会自动作为你的模板");
    // The 360 deck stays a case fact in the note, never in the prompt.
    expect(note).toContain("360");
    expect(showcaseFor("ppt-master")!.cases[2]!.prompt).not.toContain("360");
  });

  it("keeps the video card CTA short enough for the meta column", async () => {
    await renderShowcase("course-video");
    // Same five characters as the generic label: the bottom row of a video
    // card must not get wider than before (B3: no extra meta truncation).
    expect(element("showcase-prompt-video-hallucination").textContent).toBe(
      "按风格创作",
    );
    expect(element("showcase-prompt-video-agent-research").textContent).toBe(
      "调研后创作",
    );
  });

  it("shows the recipe note above the visual dialog CTA", async () => {
    await renderShowcase("creative-studio");
    await click(element("showcase-open-visual-poster-agent"));
    const dialog = document.querySelector("[role='dialog']");
    expect(dialog?.textContent).toContain("9:16");
    // Live smoke (2026-11-24): with a complete brief the agent generates
    // directly and only asks when content is missing — never promise a
    // confirmation step that does not happen.
    expect(dialog?.textContent).toContain("内容齐全时 agent 会直接出图");
    expect(dialog?.textContent).not.toContain("先确认方案再出图");
    // The case's own title stays a fact on the card, not a template variable.
    expect(element("showcase-recipe").textContent).not.toContain(
      "AI Agent 实战训练营",
    );
  });

  it("disables the dialog CTA together with the other prompt actions", async () => {
    await renderShowcase("creative-studio", true);
    await click(element("showcase-open-visual-card-rag"));
    const cta = element<HTMLButtonElement>("showcase-use-style");
    expect(cta.disabled).toBe(true);
    await click(cta);
    expect(onUsePrompt).not.toHaveBeenCalled();
    expect(document.querySelector("[role='dialog']")).not.toBeNull();
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

  it("opens four deck previews directly at their selected slide", async () => {
    await renderShowcase("ppt-master");
    expect(
      container.querySelectorAll("[data-testid^='showcase-open-ppt-quick']"),
    ).toHaveLength(4);

    for (const slideIndex of [0, 1, 2, 3]) {
      if (slideIndex > 0) await click(element("showcase-dialog-close"));
      const testId = `showcase-open-ppt-quick${slideIndex === 0 ? "" : `-${slideIndex}`}`;
      await click(element(testId));
      expect(
        document
          .querySelector<HTMLImageElement>(
            "[data-testid='showcase-deck-current']",
          )
          ?.getAttribute("src"),
      ).toContain(`/quick/slide-${slideIndex + 1}.webp`);
    }
  });

  it.each([3, 4])(
    "omits a remaining-slide badge for a %i-page deck",
    async (pageCount) => {
      const agent = showcaseFor("ppt-master")!;
      const deck = agent.cases[0]!;
      if (deck.kind !== "deck") throw new Error("deck not found");
      await act(async () => {
        root.render(
          <I18nProvider initialLocale="zh-CN">
            <AgentShowcase
              agent={{
                ...agent,
                cases: [
                  {
                    ...deck,
                    slides: deck.slides.slice(0, pageCount),
                    pageCount,
                  },
                ],
              }}
              disabled={false}
              onUsePrompt={onUsePrompt}
            />
          </I18nProvider>,
        );
      });

      expect(element("showcase-case-ppt-quick").textContent).not.toMatch(
        /[+-]\d+\s*页/,
      );
    },
  );

  it("keeps the remaining-slide badge out of the thumbnail hit area", async () => {
    await renderShowcase("ppt-master");
    const badge = Array.from(
      element("showcase-case-ppt-quick").querySelectorAll("span"),
    ).find((item) => item.textContent?.includes("+3"));

    expect(badge).toBeDefined();
    expect(badge?.className).toContain("pointer-events-none");
  });

  it("reopens the same deck at the newly selected preview after closing", async () => {
    await renderShowcase("ppt-master");
    await click(element("showcase-open-ppt-quick"));
    await click(element("showcase-deck-next"));
    await click(element("showcase-dialog-close"));

    await click(element("showcase-open-ppt-quick-2"));

    expect(
      document
        .querySelector<HTMLImageElement>(
          "[data-testid='showcase-deck-current']",
        )
        ?.getAttribute("src"),
    ).toContain("/quick/slide-3.webp");
  });

  it("keeps healthy deck previews usable when one thumbnail fails", async () => {
    await renderShowcase("ppt-master");
    const failedImage = element("showcase-open-ppt-quick-1").querySelector(
      "img",
    );
    if (!failedImage) throw new Error("thumbnail not found");

    await act(async () => {
      failedImage.dispatchEvent(new Event("error", { bubbles: true }));
    });

    expect(
      container.querySelectorAll("[data-testid^='showcase-open-ppt-quick']"),
    ).toHaveLength(3);
    expect(container.textContent).toContain("媒体暂时无法显示");
    await click(element("showcase-open-ppt-quick-2"));
    expect(
      document
        .querySelector<HTMLImageElement>(
          "[data-testid='showcase-deck-current']",
        )
        ?.getAttribute("src"),
    ).toContain("/quick/slide-3.webp");
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
      "/agent-showcase/ppt/standard-llm-app-v3.pptx",
    );
    expect(download?.download).toBe("ppt-standard.pptx");
  });

  it("reserves the intrinsic image ratio before visual previews load", async () => {
    await renderShowcase("creative-studio");

    expect(element("showcase-open-visual-poster-agent").style.aspectRatio).toBe(
      "1152 / 2048",
    );
    expect(element("showcase-open-visual-card-rag").style.aspectRatio).toBe(
      "2048 / 1152",
    );
  });

  // Layout baseline (style-workflow plan §1 / A1): pin the grid containers.
  // These assertions are the acceptance baseline and must not be edited.
  it("pins the grid containers of all four showcases", async () => {
    const casesClass = () => element("agent-guide-cases").className;

    await renderShowcase("creative-studio");
    expect(casesClass()).toContain("columns-1 gap-4 sm:columns-2 xl:columns-3");
    // A filtered subset of the visual showcase switches to the regular grid.
    await click(element("showcase-filter-poster"));
    expect(casesClass()).toContain("grid gap-4 sm:grid-cols-2 xl:grid-cols-3");
    expect(casesClass()).not.toContain("columns-");

    await renderShowcase("course-video");
    expect(casesClass()).toContain("grid gap-4 sm:grid-cols-2 xl:grid-cols-3");

    await renderShowcase("ppt-master");
    expect(casesClass()).toContain("flex flex-col gap-4");
    const deck = element("showcase-case-ppt-quick").className;
    expect(deck).toContain("md:grid-cols-[250px_minmax(0,1fr)]");
    expect(deck).toContain("xl:grid-cols-[296px_minmax(0,1fr)]");

    await renderShowcase("podcast-studio");
    expect(casesClass()).toContain("grid gap-4 sm:grid-cols-2");
  });

  it("filters visual cases with aria state and opens original and SVG downloads", async () => {
    await renderShowcase("creative-studio");
    expect(container.querySelectorAll("article")).toHaveLength(10);
    // Knowledge cards and infographics share the merged "card" filter.
    expect(
      container.querySelectorAll("[data-testid^='showcase-filter-']"),
    ).toHaveLength(5);
    expect(
      container.querySelector("[data-testid='showcase-filter-infographic']"),
    ).toBeNull();
    expect(element("showcase-filter-card").textContent).toBe("知识图解");

    await click(element("showcase-filter-card"));
    expect(container.querySelectorAll("article")).toHaveLength(4);
    expect(element("showcase-filter-card").getAttribute("aria-pressed")).toBe(
      "true",
    );
    expect(element("showcase-filter-all").getAttribute("aria-pressed")).toBe(
      "false",
    );
    // The right-edge count follows the filtered grid.
    expect(container.textContent).toContain("4 / 10 件");

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

    // Clicking the already-active category toggles back to all.
    await click(element("showcase-filter-diagram"));
    expect(container.querySelectorAll("article")).toHaveLength(10);
    expect(element("showcase-filter-all").getAttribute("aria-pressed")).toBe(
      "true",
    );
  });

  it("gives each podcast a visible 32-bar waveform", async () => {
    await renderShowcase("podcast-studio");

    expect(
      element("showcase-waveform-podcast-context").querySelectorAll("i"),
    ).toHaveLength(32);
    expect(
      element("showcase-waveform-podcast-agent2026").querySelectorAll("i"),
    ).toHaveLength(32);
  });

  it("shows concise source provenance on podcast cards", async () => {
    await renderShowcase("podcast-studio");

    expect(element("showcase-case-podcast-context").textContent).toContain(
      "原创技术笔记（未改写原文）",
    );
    expect(element("showcase-case-podcast-agent2026").textContent).toContain(
      "InfoQ · 经济参考报 · KubeCon 2026",
    );
  });

  it("labels podcast cards as case recordings with their recording-time sources", async () => {
    await renderShowcase("podcast-studio");
    // The tag and the source row are facts about the case, not the new task.
    expect(
      element("showcase-case-podcast-embodied").querySelector(".showcase-tag")
        ?.textContent,
    ).toBe("双人访谈 · 工厂案例 · 案例实录");
    expect(element("showcase-case-podcast-context").textContent).toContain(
      "案例制作时来源：原创技术笔记（未改写原文）",
    );
    // The waveform, the shared audio element and the transcript entry stay.
    expect(document.querySelectorAll("audio")).toHaveLength(1);
    expect(
      element("showcase-transcript-podcast-embodied").textContent,
    ).toContain("查看文稿");
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

  it("derives visual filters from the categories actually present", async () => {
    const creativeAgent = showcaseFor("creative-studio")!;
    const cardCase = creativeAgent.cases.find(
      (item) => item.kind === "visual" && item.category === "card",
    );
    if (!cardCase) throw new Error("card case not found");
    await act(async () => {
      root.render(
        <I18nProvider initialLocale="zh-CN">
          <AgentShowcase
            agent={{ ...creativeAgent, cases: [cardCase] }}
            disabled={false}
            onUsePrompt={onUsePrompt}
          />
        </I18nProvider>,
      );
    });

    expect(
      container.querySelectorAll("[data-testid^='showcase-filter-']"),
    ).toHaveLength(2);
    expect(element("showcase-filter-all")).not.toBeNull();
    expect(element("showcase-filter-card")).not.toBeNull();
    expect(
      container.querySelector("[data-testid='showcase-filter-poster']"),
    ).toBeNull();
    expect(container.querySelectorAll("article")).toHaveLength(1);
  });

  it("renders both audio and non-audio cases for a mixed-media agent", async () => {
    const videoAgent = showcaseFor("course-video")!;
    const podcastAgent = showcaseFor("podcast-studio")!;
    const mixedAgent = {
      ...videoAgent,
      cases: [videoAgent.cases[0]!, podcastAgent.cases[0]!],
    };
    await act(async () => {
      root.render(
        <I18nProvider initialLocale="zh-CN">
          <AgentShowcase
            agent={mixedAgent}
            disabled={false}
            onUsePrompt={onUsePrompt}
          />
        </I18nProvider>,
      );
    });

    expect(container.querySelectorAll("article")).toHaveLength(2);
    expect(element("showcase-open-video-hallucination")).not.toBeNull();
    expect(element("showcase-play-podcast-context")).not.toBeNull();
    expect(document.querySelectorAll("audio")).toHaveLength(1);
  });

  it("cancels a pending dialog focus return when the showcase unmounts", async () => {
    vi.useFakeTimers();
    await renderShowcase();
    const trigger = element("showcase-open-video-hallucination");
    trigger.focus();
    await act(async () => {
      trigger.click();
    });
    const close = document.querySelector<HTMLElement>(
      "[data-testid='showcase-dialog-close']",
    );
    if (!close) throw new Error("dialog close not found");
    const focus = vi.spyOn(trigger, "focus");

    await act(async () => {
      close.click();
    });
    await act(async () => {
      root.render(<></>);
    });
    await act(async () => {
      vi.runAllTimers();
    });

    expect(focus).not.toHaveBeenCalled();
  });

  it("ignores a late media error after the user pauses playback", async () => {
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
    mockPause();
    await renderShowcase("podcast-studio");

    await click(element("showcase-play-podcast-context"));
    await click(element("showcase-play-podcast-context"));
    const audio = document.querySelector<HTMLAudioElement>("audio");
    await act(async () => {
      audio?.dispatchEvent(new Event("error"));
    });

    expect(container.textContent).not.toContain("音频暂时无法播放，请重试。");
    expect(
      element("showcase-play-podcast-context").getAttribute("aria-label"),
    ).toContain("播放");
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
