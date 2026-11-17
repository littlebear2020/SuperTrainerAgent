import { expect, test, type Locator, type Page } from "@playwright/test";

import { type AgentNewChatGuideId } from "../../src/core/agents/new-chat";
import { showcaseFor } from "../../src/core/agents/showcase";
import { enUS, zhCN } from "../../src/core/i18n/locales";

import { pinEnglishLocale } from "./utils/locale";
import { mockLangGraphAPI, MOCK_THREAD_ID } from "./utils/mock-api";

const GUIDE_AGENTS = [
  {
    name: "course-video",
    description: "Course video",
    skills: ["auto-video-flow"],
  },
  {
    name: "ppt-master",
    description: "PPT master",
    skills: ["ppt-master"],
    suggestion_prompts: [
      { label: "改一份现有 PPT", prompt: "旧改造提示词" },
      { label: "旧快速入口", prompt: "旧快速提示词" },
    ],
  },
  {
    name: "creative-studio",
    description: "Creative studio",
    skills: ["trainerai-imagine"],
  },
  { name: "co-writer", description: "Co-writer", skills: [] },
  {
    name: "podcast-studio",
    description: "Podcast production",
    skills: ["podcast-studio", "deep-research"],
  },
];

let pageErrors: string[];

test.describe("Agent new-chat task guides", () => {
  test.beforeEach(async ({ page }) => {
    pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
  });

  test.afterEach(() => {
    expect(pageErrors).toEqual([]);
  });

  function mockGuideAPI(page: Page) {
    void page.route("**/api/v1/auth/csrf-token", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ token: "mock-csrf-token" }),
      }),
    );
    mockLangGraphAPI(page, { agents: GUIDE_AGENTS });
  }

  function agentHeader(page: Page) {
    return page.locator("header").filter({
      has: page.getByRole("button", {
        name: zhCN.agents.newChat,
        exact: true,
      }),
    });
  }

  function trackSending(page: Page) {
    const calls = { threads: 0, runs: 0 };
    void page.route("**/api/langgraph/threads", (route) => {
      if (route.request().method() === "POST") calls.threads += 1;
      return route.fallback();
    });
    for (const pattern of [
      "**/api/langgraph/runs/stream",
      "**/api/langgraph/threads/*/runs/stream",
    ]) {
      void page.route(pattern, (route) => {
        calls.runs += 1;
        return route.fallback();
      });
    }
    return calls;
  }

  function contrastRatio(foreground: string, background: string) {
    const channels = (color: string) => {
      const srgb = /^color\(srgb\s+(.+)\)$/.exec(color);
      if (srgb?.[1]) {
        return srgb[1]
          .split(/\s+/)
          .slice(0, 3)
          .map((value) => Math.max(0, Math.min(1, Number(value))) * 255);
      }
      if (/^#[\da-f]{6}$/i.test(color)) {
        return [1, 3, 5].map((offset) =>
          parseInt(color.slice(offset, offset + 2), 16),
        );
      }
      return (
        color
          .match(/[\d.]+/g)
          ?.slice(0, 3)
          .map(Number) ?? []
      );
    };
    const luminance = (color: string) => {
      const [r = 0, g = 0, b = 0] = channels(color).map((value) => {
        const channel = value / 255;
        return channel <= 0.03928
          ? channel / 12.92
          : ((channel + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    };
    const first = luminance(foreground);
    const second = luminance(background);
    return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
  }

  async function openGuide(page: Page, agentName: AgentNewChatGuideId) {
    await page.goto(`/workspace/agents/${agentName}/chats/new`);
    await expect(page.locator("textarea[name='message']")).toBeVisible();
  }

  for (const agentName of ["co-writer"] as const) {
    test(`legacy ${agentName} renders four cases and prefills each without sending`, async ({
      page,
    }) => {
      mockGuideAPI(page);
      const calls = trackSending(page);
      const guide = zhCN.agents.newChatGuides[agentName];
      await openGuide(page, agentName);

      await expect(
        page.getByRole("heading", { name: guide.title, exact: true }),
      ).toBeVisible();
      await expect(agentHeader(page)).toContainText(guide.displayName);
      await expect(agentHeader(page)).not.toContainText(agentName);
      const textarea = page.locator("textarea[name='message']");

      for (const [index, item] of guide.cases.entries()) {
        await page
          .getByRole("button", { name: item.title, exact: true })
          .click();
        if (index > 0) {
          await expect(page.getByRole("dialog")).toBeVisible();
          await page
            .getByRole("button", {
              name: zhCN.agents.newChatGuide.draftConfirmReplace,
              exact: true,
            })
            .click();
        }
        await expect(textarea).toHaveValue(item.prompt);
        await expect(textarea).toBeFocused();
      }

      expect(calls).toEqual({ threads: 0, runs: 0 });
      await expect(
        page.getByRole("button", { name: "小惊喜", exact: true }),
      ).toHaveCount(0);
      await expect(page.getByText("/skill", { exact: false })).toHaveCount(0);
      await expect(
        page.getByText("改一份现有 PPT", { exact: true }),
      ).toHaveCount(0);
    });
  }

  test("course-video showcase plays real media, prefills prompts and keeps composer overlays usable", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    mockGuideAPI(page);
    const calls = trackSending(page);
    const showcase = showcaseFor("course-video")!;
    await openGuide(page, "course-video");

    await expect(page.getByTestId("showcase-hero")).toBeVisible();
    await expect(
      page.getByRole("heading", {
        name: showcase.heroLine.zh.join(" "),
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", {
        name: zhCN.agents.newChatGuides["course-video"].title,
        exact: true,
      }),
    ).toHaveCount(0);
    await expect(page.locator("[data-testid^='showcase-case-']")).toHaveCount(
      3,
    );

    const frame = page.getByTestId("agent-guide-input");
    const inputBox = frame.locator(".glass-input-box");
    const inputGroup = inputBox.locator("[data-slot='input-group']").first();
    const composerWidth = (await inputBox.boundingBox())!.width;
    expect(composerWidth).toBeGreaterThanOrEqual(840);
    expect(composerWidth).toBeLessThanOrEqual(880);
    await expect(inputBox).toHaveCSS("border-radius", "28px");
    await expect(inputGroup).toHaveCSS("border-radius", "28px");
    expect(
      await frame.evaluate((element) => getComputedStyle(element).overflow),
    ).not.toBe("hidden");

    await frame.locator("[data-slot='dialog-trigger']").click();
    await expect(
      page.getByRole("dialog", { name: "Model Selector" }),
    ).toBeVisible();
    await expect(
      page.getByPlaceholder(zhCN.inputBox.searchModels),
    ).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByText("/skill", { exact: false })).toHaveCount(0);

    await frame.locator("input[type='file']").setInputFiles({
      name: "verification.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("Agent showcase composer verification"),
    });
    const removeAttachment = frame.getByLabel("Remove attachment");
    await expect(removeAttachment).toBeVisible();
    await expect(removeAttachment).toBeInViewport();
    await expect(
      frame.getByText("verification.txt", { exact: true }),
    ).toBeVisible();
    expect(
      await frame.evaluate((element) => getComputedStyle(element).overflow),
    ).not.toBe("hidden");
    await removeAttachment.click();

    const firstMedia = page.getByTestId("showcase-open-video-hallucination");
    await firstMedia.click();
    const video = page.locator("video");
    await expect(video).toBeVisible();
    expect(
      await video.evaluate((element: HTMLVideoElement) => element.autoplay),
    ).toBe(false);
    await video.evaluate((element: HTMLVideoElement) => element.play());
    await expect
      .poll(() =>
        video.evaluate((element: HTMLVideoElement) => element.currentTime),
      )
      .toBeGreaterThan(0);
    await page.getByTestId("showcase-dialog-close").click();
    await expect(page.locator("video")).toHaveCount(0);
    await expect(firstMedia).toBeFocused();

    const textarea = page.locator("textarea[name='message']");
    for (const [index, item] of showcase.cases.entries()) {
      await page.getByTestId(`showcase-prompt-${item.id}`).click();
      if (index > 0) {
        await page
          .getByRole("button", {
            name: zhCN.agents.newChatGuide.draftConfirmReplace,
            exact: true,
          })
          .click();
      }
      await expect(textarea).toHaveValue(item.prompt);
      await expect(textarea).toBeFocused();
    }
    expect(calls).toEqual({ threads: 0, runs: 0 });
  });

  test("ppt-master showcase pages through slides and downloads a real PPTX", async ({
    page,
  }) => {
    mockGuideAPI(page);
    await openGuide(page, "ppt-master");
    await expect(page.locator("[data-testid^='showcase-case-']")).toHaveCount(
      3,
    );

    await page.getByTestId("showcase-open-ppt-quick").click();
    const current = page.getByTestId("showcase-deck-current");
    await expect(current).toHaveAttribute("src", /quick\/slide-1\.webp$/);
    await expect(page.getByTestId("showcase-deck-previous")).toBeDisabled();
    await page.getByTestId("showcase-deck-next").click();
    await expect(current).toHaveAttribute("src", /quick\/slide-2\.webp$/);
    await page.keyboard.press("ArrowRight");
    await expect(current).toHaveAttribute("src", /quick\/slide-3\.webp$/);
    await page.getByTestId("showcase-deck-thumbnail-6").click();
    await expect(current).toHaveAttribute("src", /quick\/slide-7\.webp$/);
    await expect(page.getByTestId("showcase-deck-next")).toBeDisabled();

    const downloadPromise = page.waitForEvent("download");
    await page.getByTestId("showcase-download-pptx").click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toBe("ppt-quick.pptx");
    expect(await download.failure()).toBeNull();
  });

  test("creative-studio showcase filters real designs and downloads JPG and SVG originals", async ({
    page,
  }) => {
    mockGuideAPI(page);
    await openGuide(page, "creative-studio");
    await expect(page.locator("[data-testid^='showcase-case-']")).toHaveCount(
      10,
    );

    await page.getByTestId("showcase-filter-card").click();
    await expect(page.locator("[data-testid^='showcase-case-']")).toHaveCount(
      2,
    );
    await expect(page.getByTestId("showcase-filter-card")).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    const imageTrigger = page.getByTestId("showcase-open-visual-card-rag");
    await imageTrigger.click();
    const original = page.getByTestId("showcase-visual-original");
    await expect(original).toBeVisible();
    await expect
      .poll(() =>
        original.evaluate((image: HTMLImageElement) => image.naturalWidth),
      )
      .toBeGreaterThan(0);
    const jpgPromise = page.waitForEvent("download");
    await page.getByTestId("showcase-download-original").click();
    const jpg = await jpgPromise;
    expect(jpg.suggestedFilename()).toBe("visual-card-rag.jpg");
    expect(await jpg.failure()).toBeNull();
    await page.getByTestId("showcase-dialog-close").click();
    await expect(imageTrigger).toBeFocused();

    await page.getByTestId("showcase-filter-diagram").click();
    await expect(page.locator("[data-testid^='showcase-case-']")).toHaveCount(
      2,
    );
    await page.getByTestId("showcase-open-visual-diagram-rag").click();
    await expect(page.getByTestId("showcase-visual-original")).toHaveAttribute(
      "src",
      /diagram-rag-architecture\.svg$/,
    );
    const svgPromise = page.waitForEvent("download");
    await page.getByTestId("showcase-download-svg").click();
    const svg = await svgPromise;
    expect(svg.suggestedFilename()).toBe("visual-diagram-rag.svg");
    expect(await svg.failure()).toBeNull();
  });

  test("podcast-studio showcase plays and switches real audio and opens transcript sources", async ({
    page,
  }) => {
    mockGuideAPI(page);
    const calls = trackSending(page);
    await openGuide(page, "podcast-studio");
    await expect(page.locator("[data-testid^='showcase-case-']")).toHaveCount(
      4,
    );
    const audio = page.locator("audio");
    await expect(audio).toHaveCount(1);
    await expect(audio).toHaveAttribute("preload", "none");
    await expect(audio).not.toHaveAttribute("src", /.+/);

    await page.getByTestId("showcase-play-podcast-context").click();
    await expect
      .poll(() =>
        audio.evaluate((element: HTMLAudioElement) => element.currentTime),
      )
      .toBeGreaterThan(0);
    await page.getByTestId("showcase-play-podcast-agent2026").click();
    await expect(audio).toHaveAttribute("src", /ep2\.mp3$/);
    await expect
      .poll(() =>
        audio.evaluate((element: HTMLAudioElement) => element.currentTime),
      )
      .toBeGreaterThan(0);
    await expect(
      page.getByTestId("showcase-play-podcast-context"),
    ).toHaveAttribute("aria-label", /^播放 /);
    await expect(
      page.getByTestId("showcase-play-podcast-agent2026"),
    ).toHaveAttribute("aria-label", /^暂停 /);

    await page.getByTestId("showcase-transcript-podcast-agent2026").click();
    await expect(page.getByRole("dialog")).toContainText(
      "2026 年，AI Agent 的关键词",
    );
    const source = page.getByTestId("showcase-podcast-source-0");
    await expect(source).toHaveAttribute("target", "_blank");
    await expect(source).toHaveAttribute("rel", /noreferrer/);
    await expect(source).toHaveAttribute("rel", /noopener/);
    await expect(source).toHaveAttribute("href", /infoq\.cn/);
    await expect(page.getByTestId("showcase-download-mp3")).toHaveAttribute(
      "href",
      /ep2\.mp3$/,
    );
    expect(calls).toEqual({ threads: 0, runs: 0 });
  });

  test("wide video showcase keeps all three styles in the first row", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    mockGuideAPI(page);
    await openGuide(page, "course-video");

    const tops = await page
      .locator("[data-testid^='showcase-case-']")
      .evaluateAll((cards) =>
        cards.map((card) => card.getBoundingClientRect().top),
      );
    expect(tops).toHaveLength(3);
    expect(tops[1]).toBeCloseTo(tops[0]!, 0);
    expect(tops[2]).toBeCloseTo(tops[0]!, 0);
  });

  test("visual showcase preserves the full portrait instead of cropping it to a card ratio", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    mockGuideAPI(page);
    await openGuide(page, "creative-studio");
    const image = page
      .getByTestId("showcase-open-visual-poster-agent")
      .locator("img");
    await expect(image).toBeVisible();
    await expect
      .poll(() =>
        image.evaluate((element: HTMLImageElement) => element.naturalWidth),
      )
      .toBeGreaterThan(0);

    const ratio = await image.evaluate((element) => {
      const bounds = element.getBoundingClientRect();
      return bounds.width / bounds.height;
    });
    expect(ratio).toBeCloseTo(9 / 16, 2);
    await expect(image).toHaveCSS("object-fit", "contain");
  });

  test("mobile hero keeps the full character above the headline", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    mockGuideAPI(page);
    await openGuide(page, "course-video");
    const image = page.getByTestId("showcase-hero").locator("img");
    const title = page.getByTestId("showcase-hero").getByRole("heading");
    const imageBox = await image.boundingBox();
    const titleBox = await title.boundingBox();

    await expect(image).toHaveCSS("object-fit", "contain");
    expect(imageBox!.y + imageBox!.height).toBeLessThanOrEqual(titleBox!.y);
  });

  test("hero art is a transparent cut-out without card chrome", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    mockGuideAPI(page);

    for (const agentName of [
      "course-video",
      "ppt-master",
      "podcast-studio",
      "creative-studio",
    ] as const) {
      await openGuide(page, agentName);
      const image = page.getByTestId("showcase-hero").locator("img");
      await expect(image, agentName).toHaveCSS(
        "background-color",
        "rgba(0, 0, 0, 0)",
      );
      await expect(image, agentName).toHaveCSS("border-top-width", "0px");
      await expect(image, agentName).toHaveCSS("border-radius", "0px");
      await expect(image, agentName).toHaveCSS("box-shadow", "none");

      const report = await page.evaluate(async () => {
        const image = document.querySelector<HTMLImageElement>(
          "[data-testid='showcase-hero'] img",
        );
        if (!image) throw new Error("hero image missing");
        await image.decode();
        const canvas = document.createElement("canvas");
        canvas.width = image.naturalWidth;
        canvas.height = image.naturalHeight;
        const context = canvas.getContext("2d");
        if (!context) throw new Error("canvas unavailable");
        context.drawImage(image, 0, 0);
        const { data, width, height } = context.getImageData(
          0,
          0,
          canvas.width,
          canvas.height,
        );
        const alphaAt = (x: number, y: number) =>
          data[(y * width + x) * 4 + 3]!;
        const corners = [
          alphaAt(0, 0),
          alphaAt(width - 1, 0),
          alphaAt(0, height - 1),
          alphaAt(width - 1, height - 1),
        ];
        const band = 12;
        let edgeAlpha = 0;
        for (let y = 0; y < band; y += 1) {
          for (let x = 0; x < width; x += 1) {
            edgeAlpha = Math.max(edgeAlpha, alphaAt(x, y));
          }
        }
        let opaque = 0;
        let light = 0;
        for (let index = 0; index < data.length; index += 4) {
          const alpha = data[index + 3]!;
          if (alpha <= 200) continue;
          opaque += 1;
          if (
            data[index]! > 238 &&
            data[index + 1]! > 238 &&
            data[index + 2]! > 238
          ) {
            light += 1;
          }
        }
        return {
          width,
          height,
          corners,
          edgeAlpha,
          lightRatio: light / Math.max(1, opaque),
        };
      });

      // Corners and the top/left/right bands must be fully transparent: a
      // rounded-rect card also yields transparent corners, so the edge bands
      // are what distinguishes a cut-out from a re-carded rectangle.
      expect(report.corners, agentName).toEqual([0, 0, 0, 0]);
      expect(report.edgeAlpha, agentName).toBe(0);
      // A baked light card would make most opaque pixels near-white; real
      // clothing highlights stay far below the threshold.
      expect(report.lightRatio, agentName).toBeLessThan(0.15);
    }
  });

  test("podcast waveform animates only on the card that is playing", async ({
    page,
  }) => {
    mockGuideAPI(page);
    await openGuide(page, "podcast-studio");
    await page.getByTestId("showcase-play-podcast-context").click();
    const audio = page.locator("audio");
    await expect
      .poll(() =>
        audio.evaluate((element: HTMLAudioElement) => element.currentTime),
      )
      .toBeGreaterThan(0);

    const animationName = (id: string) =>
      page
        .getByTestId(`showcase-waveform-${id}`)
        .locator("i")
        .first()
        .evaluate((element) => getComputedStyle(element).animationName);
    expect(await animationName("podcast-context")).not.toBe("none");
    expect(await animationName("podcast-agent2026")).toBe("none");

    await page.getByTestId("showcase-play-podcast-agent2026").click();
    await expect
      .poll(() =>
        audio.evaluate((element: HTMLAudioElement) => element.currentTime),
      )
      .toBeGreaterThan(0);
    expect(await animationName("podcast-context")).toBe("none");
    expect(await animationName("podcast-agent2026")).not.toBe("none");

    await page.emulateMedia({ reducedMotion: "reduce" });
    expect(await animationName("podcast-agent2026")).toBe("none");
  });

  test("shared podcast case only prefills and explicit submit creates one run with podcast assistant", async ({
    page,
  }) => {
    mockGuideAPI(page);
    await page.route("**/api/langgraph/threads", (route) => {
      if (route.request().method() !== "POST") return route.fallback();
      const payload = JSON.parse(route.request().postData() ?? "{}");
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          thread_id: MOCK_THREAD_ID,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          metadata: { agent_name: payload.assistant_id ?? "podcast-studio" },
          status: "idle",
          values: {},
        }),
      });
    });
    const runPayloads: string[] = [];
    const events = [
      {
        event: "metadata",
        data: { run_id: "podcast-run", thread_id: MOCK_THREAD_ID },
      },
      {
        event: "values",
        data: {
          messages: [
            { type: "ai", id: "podcast-ai", content: "播客音频任务已接收" },
          ],
        },
      },
      { event: "end", data: {} },
    ];
    const body = events
      .map(
        (event) =>
          `event: ${event.event}\ndata: ${JSON.stringify(event.data)}\n\n`,
      )
      .join("");
    for (const pattern of [
      "**/api/langgraph/runs/stream",
      "**/api/langgraph/threads/*/runs/stream",
    ]) {
      await page.route(pattern, (route) => {
        runPayloads.push(route.request().postData() ?? "");
        return route.fulfill({
          status: 200,
          contentType: "text/event-stream",
          headers: {
            "Content-Location": `/threads/${MOCK_THREAD_ID}/runs/podcast-run`,
          },
          body,
        });
      });
    }
    const calls = trackSending(page);
    await openGuide(page, "podcast-studio");
    const item = showcaseFor("podcast-studio")!.cases[0]!;
    await page.getByTestId(`showcase-prompt-${item.id}`).click();
    const textarea = page.locator("textarea[name='message']");
    await expect(textarea).toHaveValue(item.prompt);
    expect(item.prompt).toContain("[模式: read_original]");
    expect(item.prompt).toContain("MP3");
    expect(calls).toEqual({ threads: 0, runs: 0 });

    await textarea.press("Enter");
    await expect.poll(() => calls.runs).toBe(1);
    await expect(page).toHaveURL(
      `/workspace/agents/podcast-studio/chats/${MOCK_THREAD_ID}`,
    );
    expect(calls).toEqual({ threads: 1, runs: 1 });
    expect(runPayloads).toHaveLength(1);
    expect(JSON.parse(runPayloads[0]!).context.agent_name).toBe(
      "podcast-studio",
    );
  });

  test("Co-writer draft choices cancel and append without sending", async ({
    page,
  }) => {
    mockGuideAPI(page);
    const calls = trackSending(page);
    await openGuide(page, "co-writer");
    const textarea = page.locator("textarea[name='message']");
    const item = zhCN.agents.newChatGuides["co-writer"].cases[0];

    await textarea.fill("我的草稿");
    await page.getByRole("button", { name: item.title, exact: true }).click();
    await page.getByRole("button", { name: "取消", exact: true }).click();
    await expect(textarea).toHaveValue("我的草稿");

    await page.getByRole("button", { name: item.title, exact: true }).click();
    await page.getByRole("button", { name: "追加", exact: true }).click();
    await expect(textarea).toHaveValue(`我的草稿\n${item.prompt}`);
    expect(calls).toEqual({ threads: 0, runs: 0 });
  });

  test("shared URL handoff wins over case clicks and never auto-sends", async ({
    page,
  }) => {
    mockGuideAPI(page);
    const calls = trackSending(page);
    await page.goto("/workspace/agents/co-writer/chats/new?msg=handoff");
    const textarea = page.locator("textarea[name='message']");
    await expect(textarea).toHaveValue("handoff");

    const item = zhCN.agents.newChatGuides["co-writer"].cases[0];
    await page.getByRole("button", { name: item.title, exact: true }).click();
    await expect(
      page.getByText(zhCN.agents.newChatGuide.draftConfirmTitle),
    ).toBeVisible();
    await page.getByRole("button", { name: "取消", exact: true }).click();
    await expect(textarea).toHaveValue("handoff");
    expect(calls).toEqual({ threads: 0, runs: 0 });
  });

  test("shared sessionStorage handoff is consumed once and not overwritten", async ({
    page,
  }) => {
    const handoff = "长文本 handoff。".repeat(40);
    await page.addInitScript((prompt) => {
      sessionStorage.setItem(
        "deerflow_pending_prompt",
        JSON.stringify({ prompt, autoSend: true }),
      );
    }, handoff);
    mockGuideAPI(page);
    const calls = trackSending(page);
    await openGuide(page, "co-writer");
    const textarea = page.locator("textarea[name='message']");
    await expect(textarea).toHaveValue(handoff);

    const item = zhCN.agents.newChatGuides["co-writer"].cases[1];
    await page.getByRole("button", { name: item.title, exact: true }).click();
    await page.getByRole("button", { name: "取消", exact: true }).click();
    await expect(textarea).toHaveValue(handoff);
    expect(
      await page.evaluate(() =>
        sessionStorage.getItem("deerflow_pending_prompt"),
      ),
    ).toBeNull();
    expect(calls).toEqual({ threads: 0, runs: 0 });
  });

  test("legacy English prompts come from the locale and preserve Co-writer markers", async ({
    page,
  }) => {
    await pinEnglishLocale(page);
    mockGuideAPI(page);
    await openGuide(page, "co-writer");
    const guide = enUS.agents.newChatGuides["co-writer"];

    await expect(
      page.getByRole("heading", { name: guide.title, exact: true }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: guide.cases[0].title, exact: true })
      .click();
    await expect(page.locator("textarea[name='message']")).toHaveValue(
      guide.cases[0].prompt,
    );
    expect(guide.cases[0].prompt).toContain("[类型: article]");
    expect(guide.cases[0].prompt).toContain("[从零创作]");
  });

  test("shared only explicit submission creates one thread and stage markers stay hidden", async ({
    page,
  }) => {
    mockGuideAPI(page);
    const aiMessage = {
      type: "ai",
      id: "guided-ai",
      content: "[[STAGE:OUTLINE]]\n\n# 课程大纲\n\n第一章",
    };
    const events = [
      {
        event: "metadata",
        data: { run_id: "guided-run", thread_id: MOCK_THREAD_ID },
      },
      { event: "values", data: { messages: [aiMessage] } },
      { event: "end", data: {} },
    ];
    const body = events
      .map(
        (event) =>
          `event: ${event.event}\ndata: ${JSON.stringify(event.data)}\n\n`,
      )
      .join("");
    for (const pattern of [
      "**/api/langgraph/runs/stream",
      "**/api/langgraph/threads/*/runs/stream",
    ]) {
      await page.route(pattern, (route) =>
        route.fulfill({
          status: 200,
          contentType: "text/event-stream",
          headers: {
            "Content-Location": `/threads/${MOCK_THREAD_ID}/runs/guided-run`,
          },
          body,
        }),
      );
    }
    const calls = trackSending(page);
    await openGuide(page, "co-writer");
    const item = zhCN.agents.newChatGuides["co-writer"].cases[0];
    await page.getByRole("button", { name: item.title, exact: true }).click();
    expect(calls).toEqual({ threads: 0, runs: 0 });

    const textarea = page.locator("textarea[name='message']");
    await textarea.press("Enter");
    await expect.poll(() => calls.runs).toBe(1);
    await expect(page).toHaveURL(
      `/workspace/agents/co-writer/chats/${MOCK_THREAD_ID}`,
    );
    await expect(
      page.getByRole("heading", { name: "课程大纲", exact: true }),
    ).toBeVisible();
    await expect(page.getByText("[[STAGE:OUTLINE]]")).toHaveCount(0);
    await expect(page.getByTestId("agent-new-chat-guide")).toHaveCount(0);
    expect(calls).toEqual({ threads: 1, runs: 1 });
  });

  test("shared existing target threads keep their messages and do not show a guide", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      agents: GUIDE_AGENTS,
      threads: [
        {
          thread_id: MOCK_THREAD_ID,
          title: "Existing lesson",
          agent_name: "course-video",
        },
      ],
    });
    await page.goto(`/workspace/agents/course-video/chats/${MOCK_THREAD_ID}`);

    await expect(page.getByText("Previous question")).toBeVisible();
    await expect(
      page.getByText("Response in thread Existing lesson"),
    ).toBeVisible();
    await expect(page.getByTestId("agent-new-chat-guide")).toHaveCount(0);
    await expect(page.locator("textarea[name='message']")).toHaveAttribute(
      "placeholder",
      zhCN.inputBox.placeholder,
    );
  });

  test("shared retired aliases resolve to their approved guide", async ({
    page,
  }) => {
    mockGuideAPI(page);
    for (const [alias, agentName] of [
      ["card-poster", "creative-studio"],
      ["course-ppt", "ppt-master"],
    ] as const) {
      await page.goto(`/workspace/agents/${alias}/chats/new`);
      await expect(
        page.getByRole("heading", {
          name: showcaseFor(agentName)!.heroLine.zh.join(" "),
          exact: true,
        }),
      ).toBeVisible();
    }
  });

  test("non-target agents keep the legacy welcome", async ({ page }) => {
    mockLangGraphAPI(page, {
      agents: [{ name: "test-agent", description: "A test agent" }],
    });
    await page.goto("/workspace/agents/test-agent/chats/new");

    await expect(agentHeader(page)).toContainText("test-agent");
    await expect(page.getByText("A test agent")).toBeVisible();
    await expect(page.getByTestId("agent-new-chat-guide")).toHaveCount(0);
  });

  test("vimax keeps the global skill fallback with a real skills fixture", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      agents: [{ name: "vimax-video", description: "Vimax", skills: [] }],
      skills: [{ name: "deep-research", enabled: true }],
    });
    await page.goto("/workspace/agents/vimax-video/chats/new");

    await expect(
      page.getByText("deep-research", { exact: true }),
    ).toBeVisible();
    await expect(page.getByTestId("agent-new-chat-guide")).toHaveCount(0);
  });

  test("shared manual retry, not automatic query retry, recovers an agent failure", async ({
    page,
  }) => {
    let allowSuccess = false;
    let requestCount = 0;
    mockLangGraphAPI(page, { agents: [] });
    await page.route("**/api/agents/course-video", (route) => {
      requestCount += 1;
      if (!allowSuccess) {
        return route.fulfill({
          status: 500,
          contentType: "application/json",
          body: JSON.stringify({ detail: "failed" }),
        });
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(GUIDE_AGENTS[0]),
      });
    });
    await page.goto("/workspace/agents/course-video/chats/new");
    await expect(
      page.getByText(zhCN.agents.newChatGuide.loadFailed),
    ).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("agent-guide-cases")).toHaveCount(0);

    const settledRequests = requestCount;
    allowSuccess = true;
    await page.evaluate(async () => {
      window.dispatchEvent(new Event("focus"));
      window.dispatchEvent(new Event("online"));
      await new Promise<void>((resolve) => {
        requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
      });
    });
    expect(requestCount).toBe(settledRequests);
    await expect(
      page.getByText(zhCN.agents.newChatGuide.loadFailed),
    ).toBeVisible();

    await page.getByRole("link", { name: "首页", exact: true }).click();
    await expect(page).toHaveURL("/workspace");
    await page.goBack();
    await expect(page).toHaveURL("/workspace/agents/course-video/chats/new");
    await expect(
      page.getByText(zhCN.agents.newChatGuide.loadFailed),
    ).toBeVisible();
    expect(requestCount).toBe(settledRequests);

    await page
      .getByRole("button", { name: zhCN.agents.newChatGuide.retry })
      .click();
    await expect(
      page.getByRole("heading", {
        name: showcaseFor("course-video")!.heroLine.zh.join(" "),
      }),
    ).toBeVisible();
  });

  test("shared loading disables submission and reveals cases only after agent success", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { agents: [] });
    let releaseAgent: (() => void) | undefined;
    await page.route("**/api/agents/course-video", async (route) => {
      await new Promise<void>((resolve) => {
        releaseAgent = resolve;
      });
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(GUIDE_AGENTS[0]),
      });
    });
    await page.goto("/workspace/agents/course-video/chats/new");

    try {
      await expect(
        page.getByText(zhCN.agents.newChatGuide.loading),
      ).toBeVisible();
      await expect(page.locator("textarea[name='message']")).toBeDisabled();
      await expect(page.getByTestId("agent-guide-cases")).toHaveCount(0);
    } finally {
      releaseAgent?.();
    }
    await expect(page.getByTestId("agent-guide-cases")).toBeVisible();
  });

  test("showcase accent tags keep at least 4.5:1 text contrast", async ({
    page,
  }) => {
    mockGuideAPI(page);
    for (const agentName of [
      "course-video",
      "ppt-master",
      "podcast-studio",
      "creative-studio",
    ] as const) {
      await openGuide(page, agentName);
      const tag = page
        .locator("[data-testid='agent-guide-cases'] .showcase-tag")
        .first();
      await expect(tag).toBeVisible();
      const colors = await tag.evaluate((element) => {
        const style = getComputedStyle(element);
        return { foreground: style.color, background: style.backgroundColor };
      });
      expect(
        contrastRatio(colors.foreground, colors.background),
        agentName,
      ).toBeGreaterThanOrEqual(4.5);
    }
  });

  test("PPT download action keeps accessible contrast in default and hover states", async ({
    page,
  }) => {
    mockGuideAPI(page);
    await openGuide(page, "ppt-master");
    await page.getByTestId("showcase-open-ppt-quick").click();
    const download = page.getByTestId("showcase-download-pptx");
    const readContrast = async () => {
      const colors = await download.evaluate((element) => {
        const style = getComputedStyle(element);
        const normalize = (color: string) => {
          const probe = document.createElement("span");
          probe.style.color = `rgb(from ${color} r g b)`;
          element.append(probe);
          const normalized = getComputedStyle(probe).color;
          probe.remove();
          return normalized;
        };
        return {
          foreground: normalize(style.color),
          background: normalize(style.backgroundColor),
        };
      });
      return contrastRatio(colors.foreground, colors.background);
    };

    expect(await readContrast()).toBeGreaterThanOrEqual(4.5);
    await download.hover();
    expect(await readContrast()).toBeGreaterThanOrEqual(4.5);
  });

  test("dark mode keeps the guide hero readable", async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem("theme", "dark");
    });
    mockGuideAPI(page);
    await openGuide(page, "course-video");
    await expect
      .poll(() =>
        page.evaluate(() =>
          document.documentElement.classList.contains("dark"),
        ),
      )
      .toBe(true);

    const foreground = await page
      .getByRole("heading", {
        name: showcaseFor("course-video")!.heroLine.zh.join(" "),
      })
      .evaluate((element) => getComputedStyle(element).color);
    const background = await page.evaluate(() =>
      getComputedStyle(document.documentElement)
        .getPropertyValue("--blob-root-bg")
        .trim(),
    );
    expect(foreground).toBe("rgb(241, 245, 249)");
    expect(background).toBe("#040810");
    const parseRgb = (value: string) =>
      value.match(/\d+/g)?.map((part) => Number(part)) ?? [];
    const parseHex = (value: string) =>
      value
        .replace("#", "")
        .match(/[\da-f]{2}/gi)
        ?.map((part) => parseInt(part, 16)) ?? [];
    const luminance = (values: number[]) => {
      const [r = 0, g = 0, b = 0] = values.map((value) => {
        const channel = value / 255;
        return channel <= 0.03928
          ? channel / 12.92
          : ((channel + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    };
    const foregroundLuminance = luminance(parseRgb(foreground));
    const backgroundLuminance = luminance(parseHex(background));
    const contrast =
      (Math.max(foregroundLuminance, backgroundLuminance) + 0.05) /
      (Math.min(foregroundLuminance, backgroundLuminance) + 0.05);
    expect(contrast).toBeGreaterThanOrEqual(4.5);

    const cardTitle = page
      .locator("[data-testid^='showcase-case-'] h3")
      .first();
    await expect(cardTitle).toHaveCSS("color", foreground);
    await page.getByTestId("showcase-open-video-hallucination").click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole("heading")).toHaveCSS("color", foreground);
    await expect(dialog.locator("video")).toHaveCSS(
      "background-color",
      /^(?:rgb\(10, 10, 10\)|lab\(2\.75381 0 0\))$/,
    );
  });

  for (const viewport of [
    { width: 1440, height: 900, firstCardTop: 581 },
    { width: 1024, height: 768, firstCardTop: 545 },
    { width: 390, height: 844, firstCardTop: 618 },
  ]) {
    test(`showcase viewport ${viewport.width}x${viewport.height} preserves the first screen without overflow`, async ({
      page,
    }) => {
      await page.setViewportSize(viewport);
      mockGuideAPI(page);
      await openGuide(page, "course-video");
      await page.evaluate(async () => {
        await document.fonts.ready;
        await Promise.all(
          document
            .getAnimations()
            .map((animation) => animation.finished.catch(() => undefined)),
        );
      });

      const hero = await page.getByTestId("showcase-hero").boundingBox();
      const input = await page.getByTestId("agent-guide-input").boundingBox();
      const showcase = await page.getByTestId("agent-showcase").boundingBox();
      const cases = await page.getByTestId("agent-guide-cases").boundingBox();
      const casesTitle = await page
        .getByRole("heading", {
          name: new RegExp(`^${showcaseFor("course-video")!.caseLabel.zh}`),
        })
        .boundingBox();
      const firstCard = page.locator("[data-testid^='showcase-case-']").first();
      const firstCardBox = await firstCard.boundingBox();
      const firstCardTitle = await firstCard.locator("h3").boundingBox();
      expect(hero).not.toBeNull();
      expect(input).not.toBeNull();
      expect(showcase).not.toBeNull();
      expect(cases).not.toBeNull();
      expect(casesTitle).not.toBeNull();
      expect(firstCardBox).not.toBeNull();
      expect(firstCardTitle).not.toBeNull();
      expect(hero!.y + hero!.height).toBeLessThanOrEqual(viewport.height);
      expect(input!.y + input!.height).toBeLessThanOrEqual(viewport.height);
      expect(casesTitle!.y + casesTitle!.height).toBeLessThanOrEqual(
        viewport.height,
      );
      expect(input!.y + input!.height).toBeLessThanOrEqual(cases!.y);
      expect(firstCardBox!.y).toBeLessThanOrEqual(viewport.firstCardTop);
      // Hard first-screen gate: the first card's title must be visible, not
      // only its poster. Baseline (PR #13) + 30px, see plan §P1-2.
      expect(firstCardTitle!.y + firstCardTitle!.height).toBeLessThanOrEqual(
        viewport.height,
      );
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth,
        ),
      ).toBe(true);

      // Hero and showcase share the single 1180 shell; the composer follows
      // the demo and is capped at 860px, centered under that shell.
      const lefts = [hero!.x, showcase!.x];
      const rights = [hero!.x + hero!.width, showcase!.x + showcase!.width];
      expect(Math.max(...lefts) - Math.min(...lefts)).toBeLessThanOrEqual(8);
      expect(Math.max(...rights) - Math.min(...rights)).toBeLessThanOrEqual(8);

      const radius = viewport.width < 640 ? "24px" : "28px";
      const inputBox = page.locator(".glass-input-box");
      await expect(inputBox).toHaveCSS("border-radius", radius);
      await expect(
        inputBox.locator("[data-slot='input-group']").first(),
      ).toHaveCSS("border-radius", radius);
      if (viewport.width === 1440) {
        const inputWidth = (await inputBox.boundingBox())!.width;
        expect(inputWidth).toBeGreaterThanOrEqual(840);
        expect(inputWidth).toBeLessThanOrEqual(880);
        const inputCenter = input!.x + input!.width / 2;
        const shellCenter = hero!.x + hero!.width / 2;
        expect(Math.abs(inputCenter - shellCenter)).toBeLessThanOrEqual(4);
      }
    });
  }

  test("showcase new chats use agent-specific placeholders while other surfaces keep the global one", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    mockGuideAPI(page);
    const textarea = page.locator("textarea[name='message']");

    for (const agentName of [
      "course-video",
      "ppt-master",
      "podcast-studio",
      "creative-studio",
    ] as const) {
      await openGuide(page, agentName);
      await expect(textarea, agentName).toHaveAttribute(
        "placeholder",
        showcaseFor(agentName)!.inputPlaceholder.zh,
      );
      await expect(textarea, agentName).not.toHaveAttribute(
        "placeholder",
        zhCN.inputBox.placeholder,
      );
    }

    await openGuide(page, "co-writer");
    await expect(textarea).toHaveAttribute(
      "placeholder",
      zhCN.inputBox.placeholder,
    );
  });

  test("showcase guide omits verification badge, case note and tips block", async ({
    page,
  }) => {
    mockGuideAPI(page);
    const showcase = showcaseFor("course-video")!;
    await openGuide(page, "course-video");
    await expect(page.getByTestId("agent-guide-tips")).toHaveCount(0);
    await expect(page.getByText("产物已核验", { exact: true })).toHaveCount(0);
    await expect(
      page.getByText(showcase.note.zh, { exact: false }),
    ).toHaveCount(0);
  });

  test("hero uses one fixed character column and drops the agent label across all four agents", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    mockGuideAPI(page);
    const geometry: {
      x: number;
      width: number;
      height: number;
      headingX: number;
    }[] = [];

    for (const agentName of [
      "course-video",
      "ppt-master",
      "podcast-studio",
      "creative-studio",
    ] as const) {
      await openGuide(page, agentName);
      const hero = page.getByTestId("showcase-hero");
      const image = hero.locator("img");
      const heading = hero.getByRole("heading");
      await expect(image).toBeVisible();
      const imageBox = await image.boundingBox();
      const headingBox = await heading.boundingBox();
      expect(imageBox, agentName).not.toBeNull();
      expect(headingBox, agentName).not.toBeNull();
      geometry.push({
        x: imageBox!.x,
        width: imageBox!.width,
        height: imageBox!.height,
        headingX: headingBox!.x,
      });
      // The eyebrow label duplicated the agent name above the headline.
      await expect(
        hero.getByText(showcaseFor(agentName)!.label.zh, { exact: true }),
      ).toHaveCount(0);
    }

    for (const item of geometry) {
      expect(item.x).toBeCloseTo(geometry[0]!.x, 1);
      expect(item.width).toBeCloseTo(geometry[0]!.width, 1);
      expect(item.height).toBeCloseTo(geometry[0]!.height, 1);
      expect(item.headingX).toBeCloseTo(geometry[0]!.headingX, 1);
    }
    expect(geometry[0]!.width).toBe(180);
  });

  test("all four agents share one showcase accent palette and creative-studio is named course poster", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    mockGuideAPI(page);
    const palettes: { color: string; background: string }[] = [];

    for (const agentName of [
      "course-video",
      "ppt-master",
      "podcast-studio",
      "creative-studio",
    ] as const) {
      await openGuide(page, agentName);
      const tag = page
        .locator("[data-testid='agent-guide-cases'] .showcase-tag")
        .first();
      await expect(tag, agentName).toBeVisible();
      palettes.push(
        await tag.evaluate((element) => {
          const style = getComputedStyle(element);
          return { color: style.color, background: style.backgroundColor };
        }),
      );
    }

    for (const palette of palettes) {
      expect(palette.color).toBe("rgb(63, 79, 209)");
      expect(palette.background).toBe("rgb(238, 241, 255)");
    }

    await openGuide(page, "creative-studio");
    await expect(agentHeader(page)).toContainText("课程海报");
    await expect(agentHeader(page)).not.toContainText("视觉设计");
  });

  test("visual filters keep counts in sync and toggle back to all", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    mockGuideAPI(page);
    await openGuide(page, "creative-studio");
    const cards = page.locator("[data-testid^='showcase-case-']");
    const caseHeading = page.getByRole("heading", { name: /^真实作品/ });

    await expect(cards).toHaveCount(10);
    await expect(caseHeading).toContainText("10 个可核验案例");
    await expect(page.getByText("10 件", { exact: true })).toBeVisible();

    for (const filter of [
      "poster",
      "card",
      "comic",
      "infographic",
      "diagram",
    ] as const) {
      await page.getByTestId(`showcase-filter-${filter}`).click();
      await expect(cards, filter).toHaveCount(2);
      await expect(
        page.getByTestId(`showcase-filter-${filter}`),
      ).toHaveAttribute("aria-pressed", "true");
      // Filtered cards must fill columns instead of stacking in column 1.
      const filteredColumns = await cards.evaluateAll((elements) =>
        elements.map((element) =>
          Math.round(element.getBoundingClientRect().x),
        ),
      );
      expect(new Set(filteredColumns).size, filter).toBe(2);
      await expect(caseHeading, filter).toContainText("2 个可核验案例");
      await expect(page.getByText("2 / 10 件", { exact: true })).toBeVisible();
    }

    await expect(
      page.locator("[data-testid^='showcase-case-'] h3").first(),
    ).toHaveText("AI Agent 技术栈");

    // Re-clicking the active category returns to the full list.
    await page.getByTestId("showcase-filter-diagram").click();
    await expect(cards).toHaveCount(10);
    await expect(page.getByTestId("showcase-filter-all")).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await expect(caseHeading).toContainText("10 个可核验案例");
    await expect(page.getByText("10 件", { exact: true })).toBeVisible();
  });

  test("390 long showcase placeholder wraps inside the composer without toolbar overlap", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    mockGuideAPI(page);
    await openGuide(page, "course-video");
    const textarea = page.locator("textarea[name='message']");
    await expect(textarea).toHaveAttribute(
      "placeholder",
      showcaseFor("course-video")!.inputPlaceholder.zh,
    );

    const inputBox = page.locator(".glass-input-box");
    const inputBoxBox = await inputBox.boundingBox();
    const textareaBox = await textarea.boundingBox();
    const toolbar = inputBox.locator("[data-slot='input-group-addon']").first();
    const toolbarBox = await toolbar.boundingBox();
    expect(inputBoxBox).not.toBeNull();
    expect(textareaBox).not.toBeNull();
    expect(toolbarBox).not.toBeNull();
    // Wrapping the long placeholder may add one text line, never an oversized
    // composer; the text must stay above the toolbar.
    expect(inputBoxBox!.height).toBeLessThanOrEqual(132);
    expect(textareaBox!.y + textareaBox!.height).toBeLessThanOrEqual(
      toolbarBox!.y + 0.5,
    );
    expect(
      await textarea.evaluate(
        (element) => element.scrollHeight <= element.clientHeight + 1,
      ),
    ).toBe(true);
  });

  test("showcase cards keep the P1 type scale", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    mockGuideAPI(page);
    const fontSize = async (locator: Locator) =>
      Number.parseFloat(
        await locator.evaluate((element) => getComputedStyle(element).fontSize),
      );

    await openGuide(page, "course-video");
    const videoCard = page.getByTestId("showcase-case-video-hallucination");
    expect(await fontSize(videoCard.locator("h3"))).toBeGreaterThanOrEqual(18);
    expect(
      await fontSize(videoCard.locator("p").first()),
    ).toBeGreaterThanOrEqual(13);
    expect(
      await fontSize(videoCard.locator(".showcase-tag")),
    ).toBeGreaterThanOrEqual(10.5);
    await expect(videoCard).toHaveCSS("background-color", /.+/);
    expect(
      await videoCard.evaluate(
        (element) => getComputedStyle(element).backgroundColor,
      ),
    ).not.toBe("rgba(0, 0, 0, 0)");

    await openGuide(page, "ppt-master");
    const deckCard = page.getByTestId("showcase-case-ppt-quick");
    expect(await fontSize(deckCard.locator("h3"))).toBeGreaterThanOrEqual(19);
    expect(
      await fontSize(deckCard.locator("p").first()),
    ).toBeGreaterThanOrEqual(13);
    expect(
      await fontSize(deckCard.locator(".showcase-tag")),
    ).toBeGreaterThanOrEqual(10.5);

    await openGuide(page, "creative-studio");
    const visualCard = page.getByTestId("showcase-case-visual-poster-agent");
    expect(await fontSize(visualCard.locator("h3"))).toBeGreaterThanOrEqual(15);
    expect(
      await fontSize(visualCard.locator(".showcase-tag")),
    ).toBeGreaterThanOrEqual(10.5);

    await openGuide(page, "podcast-studio");
    const podcastCard = page.getByTestId("showcase-case-podcast-context");
    expect(await fontSize(podcastCard.locator("h3"))).toBeGreaterThanOrEqual(
      15,
    );
    expect(
      await fontSize(podcastCard.locator("p").first()),
    ).toBeGreaterThanOrEqual(13);
  });

  test("shared keyboard opens and closes media then refills the real composer", async ({
    page,
  }) => {
    mockGuideAPI(page);
    const calls = trackSending(page);
    await openGuide(page, "course-video");
    const firstMedia = page.getByTestId("showcase-open-video-hallucination");
    await firstMedia.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("dialog")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(firstMedia).toBeFocused();

    const first = showcaseFor("course-video")!.cases[0]!;
    const prompt = page.getByTestId(`showcase-prompt-${first.id}`);
    await prompt.focus();
    await page.keyboard.press("Enter");
    const textarea = page.locator("textarea[name='message']");
    await expect(textarea).toHaveValue(first.prompt);
    await expect(textarea).toBeFocused();
    expect(calls).toEqual({ threads: 0, runs: 0 });
  });

  test("English showcase keeps long hero and UI English while prompts stay Chinese", async ({
    page,
  }) => {
    await pinEnglishLocale(page);
    mockGuideAPI(page);
    const showcase = showcaseFor("ppt-master")!;
    await openGuide(page, "ppt-master");

    await expect(
      page.getByRole("heading", {
        name: showcase.heroLine.en.join(" "),
        exact: true,
      }),
    ).toBeVisible();
    await expect(page.locator("textarea[name='message']")).toHaveAttribute(
      "placeholder",
      showcase.inputPlaceholder.en,
    );
    const guide = page.getByTestId("agent-new-chat-guide");
    expect(await guide.innerText()).not.toMatch(/[一-鿿]/);
    await expect(
      page.getByRole("button", { name: /^Use this prompt:/ }),
    ).toHaveCount(3);
    await page.getByTestId("showcase-prompt-ppt-standard").click();
    const prompt = showcase.cases[1]!.prompt;
    await expect(page.locator("textarea[name='message']")).toHaveValue(prompt);
    expect(prompt).toContain("Default");
    expect(prompt).toMatch(/[一-鿿]/);
  });

  test("creative-studio cold-cache initial media stays within budget without errors or deferred originals", async ({
    page,
  }) => {
    const mediaBytes: { url: string; bytes: number }[] = [];
    const failedMedia: string[] = [];
    const consoleErrors: string[] = [];
    const pending: Promise<void>[] = [];
    page.on("console", (message) => {
      if (message.type() === "error")
        consoleErrors.push(`${message.text()} @ ${message.location().url}`);
    });
    page.on("response", (response) => {
      if (!response.url().includes("/agent-showcase/")) return;
      if (response.status() >= 400) failedMedia.push(response.url());
      pending.push(
        response.body().then((body) => {
          mediaBytes.push({ url: response.url(), bytes: body.length });
        }),
      );
    });
    mockGuideAPI(page);
    await openGuide(page, "creative-studio");
    await page.waitForLoadState("networkidle");
    await Promise.all(pending);

    const bytes = mediaBytes.reduce((total, item) => total + item.bytes, 0);
    expect(bytes).toBeLessThanOrEqual(1_500_000);
    expect(mediaBytes.map((item) => item.url)).not.toEqual(
      expect.arrayContaining([
        expect.stringMatching(/\/podcast\/|\/originals\/|\.pptx(?:$|\?)/),
      ]),
    );
    expect(failedMedia).toEqual([]);
    expect(consoleErrors).toEqual([]);
  });

  for (const agentName of [
    "course-video",
    "ppt-master",
    "podcast-studio",
  ] as const) {
    test(`${agentName} initially avoids video, audio, originals and PPTX requests`, async ({
      page,
    }) => {
      const requests: string[] = [];
      page.on("request", (request) => {
        if (request.url().includes("/agent-showcase/"))
          requests.push(request.url());
      });
      mockGuideAPI(page);
      await openGuide(page, agentName);
      await page.waitForLoadState("networkidle");
      expect(
        requests.filter((url) =>
          /\/videos\/v\d|\/podcast\/|\/originals\/|\.pptx(?:$|\?)/.test(url),
        ),
      ).toEqual([]);
    });
  }
});
