import { expect, test, type Locator, type Page } from "@playwright/test";

import type { AgentNewChatGuideId } from "../../src/core/agents/new-chat";
import { showcaseFor } from "../../src/core/agents/showcase";
import { zhCN } from "../../src/core/i18n/locales";

import { pinEnglishLocale } from "./utils/locale";
import { mockLangGraphAPI } from "./utils/mock-api";

// Style-workflow plan: a case lends its creative recipe, never its content.
// The card CTA and the preview-dialog CTA both only fill the composer.
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

// One case per agent and the test id of its preview trigger.
const CASES = [
  {
    agent: "course-video",
    caseId: "video-hallucination",
    preview: "showcase-open-video-hallucination",
  },
  {
    agent: "ppt-master",
    caseId: "ppt-standard",
    preview: "showcase-open-ppt-standard",
  },
  {
    agent: "creative-studio",
    caseId: "visual-card-rag",
    preview: "showcase-open-visual-card-rag",
  },
  {
    agent: "podcast-studio",
    caseId: "podcast-embodied",
    preview: "showcase-transcript-podcast-embodied",
  },
] as const;

// A fill-in placeholder of a template; section headers such as 【我的内容】
// share the brackets but are never selected.
const PLACEHOLDER = /【(?:请[^【】\n]*|可选填写[^【】\n]*)】/;

let pageErrors: string[];

test.describe("Agent showcase style workflow", () => {
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

  async function openGuide(page: Page, agentName: AgentNewChatGuideId) {
    await page.goto(`/workspace/agents/${agentName}/chats/new`);
    await expect(page.locator("textarea[name='message']")).toBeVisible();
  }

  async function addOneAttachment(page: Page): Promise<Locator> {
    const frame = page.getByTestId("agent-guide-input");
    await frame.locator("input[type='file']").setInputFiles({
      name: "my-notes.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("my own notes"),
    });
    const removeAttachment = frame.getByLabel("Remove attachment");
    await expect(removeAttachment).toBeVisible();
    await expect(
      frame.getByText("my-notes.txt", { exact: true }),
    ).toBeVisible();
    return removeAttachment;
  }

  async function selectionOf(textarea: Locator) {
    return textarea.evaluate((element: HTMLTextAreaElement) => ({
      start: element.selectionStart,
      end: element.selectionEnd,
      text: element.value.slice(element.selectionStart, element.selectionEnd),
    }));
  }

  for (const entry of CASES) {
    const item = showcaseFor(entry.agent)!.cases.find(
      (value) => value.id === entry.caseId,
    )!;

    test(`${entry.agent}: the preview CTA closes the preview, fills the template, keeps attachments and sends nothing`, async ({
      page,
    }) => {
      mockGuideAPI(page);
      const calls = trackSending(page);
      await openGuide(page, entry.agent);
      const removeAttachment = await addOneAttachment(page);

      const trigger = page.getByTestId(entry.preview);
      await trigger.click();
      await expect(page.getByRole("dialog")).toBeVisible();
      const cta = page.getByTestId("showcase-use-style");
      await expect(cta).toHaveText(item.actionLabel!.zh);
      await expect(cta).toHaveAccessibleName(
        `${item.actionLabel!.zh}: ${item.title.zh}`,
      );
      await cta.click();

      // The preview is gone and nothing was submitted.
      await expect(page.getByRole("dialog")).toHaveCount(0);
      await expect(page.locator("video")).toHaveCount(0);
      const textarea = page.locator("textarea[name='message']");
      await expect(textarea).toHaveValue(item.prompt);
      // The user's own attachment is untouched and the focus moved on.
      await expect(removeAttachment).toBeVisible();
      await expect(textarea).toBeFocused();
      // The first fill-in placeholder is selected; a template without one
      // (a fixed-script video) leaves the caret at the end.
      const selection = await selectionOf(textarea);
      const placeholder = PLACEHOLDER.exec(item.prompt);
      if (placeholder) {
        expect(selection.text).toBe(placeholder[0]);
        expect(selection.start).toBe(placeholder.index);
      } else {
        expect(selection.start).toBe(item.prompt.length);
      }
      expect(calls).toEqual({ threads: 0, runs: 0 });
    });

    test(`${entry.agent}: card and preview CTAs fill the same template through the draft confirmation`, async ({
      page,
    }) => {
      mockGuideAPI(page);
      const calls = trackSending(page);
      await openGuide(page, entry.agent);
      const textarea = page.locator("textarea[name='message']");
      const cardCta = page.getByTestId(`showcase-prompt-${entry.caseId}`);
      const confirm = page.getByRole("dialog");
      const cancelButton = page.getByRole("button", {
        name: zhCN.common.cancel,
        exact: true,
      });
      const appendButton = page.getByRole("button", {
        name: zhCN.agents.newChatGuide.draftConfirmAppend,
        exact: true,
      });
      const replaceButton = page.getByRole("button", {
        name: zhCN.agents.newChatGuide.draftConfirmReplace,
        exact: true,
      });

      await textarea.fill("我的草稿");
      // Card CTA: cancel keeps the draft as it was.
      await cardCta.click();
      await expect(confirm).toContainText(
        zhCN.agents.newChatGuide.draftConfirmTitle,
      );
      await cancelButton.click();
      await expect(textarea).toHaveValue("我的草稿");

      // Preview CTA: the preview closes first, so only the confirmation is open.
      await page.getByTestId(entry.preview).click();
      await expect(page.getByRole("dialog")).toBeVisible();
      await page.getByTestId("showcase-use-style").click();
      await expect(page.getByRole("dialog")).toHaveCount(1);
      await expect(confirm).toContainText(
        zhCN.agents.newChatGuide.draftConfirmTitle,
      );
      await appendButton.click();
      await expect(textarea).toHaveValue(`我的草稿\n${item.prompt}`);

      // Replace yields exactly the template, the same as the card CTA.
      await cardCta.click();
      await replaceButton.click();
      await expect(textarea).toHaveValue(item.prompt);
      expect(calls).toEqual({ threads: 0, runs: 0 });
    });

    test(`${entry.agent}: the preview footer and its CTA fit a 390px dialog`, async ({
      page,
    }) => {
      await page.setViewportSize({ width: 390, height: 844 });
      mockGuideAPI(page);
      await openGuide(page, entry.agent);
      await page.getByTestId(entry.preview).click();
      const dialog = page.getByRole("dialog");
      await expect(dialog).toBeVisible();
      const cta = page.getByTestId("showcase-use-style");
      // The footer sits at the end of the scrollable dialog content, so a long
      // transcript needs a scroll before the CTA is reachable.
      await cta.scrollIntoViewIfNeeded();
      await expect(cta).toBeInViewport();
      // The note wraps above the button instead of widening the dialog.
      const dialogBox = (await dialog.boundingBox())!;
      const ctaBox = (await cta.boundingBox())!;
      const noteBox = (await page
        .getByTestId("showcase-recipe")
        .boundingBox())!;
      expect(ctaBox.x).toBeGreaterThanOrEqual(dialogBox.x);
      expect(ctaBox.x + ctaBox.width).toBeLessThanOrEqual(
        dialogBox.x + dialogBox.width + 0.5,
      );
      expect(noteBox.x + noteBox.width).toBeLessThanOrEqual(
        dialogBox.x + dialogBox.width + 0.5,
      );
      expect(ctaBox.y).toBeGreaterThanOrEqual(noteBox.y);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth,
        ),
      ).toBe(true);
    });
  }

  test("layout baseline at 1440: three video cards in a row, stacked deck cards, a 2×2 podcast grid", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    mockGuideAPI(page);
    const cardBoxes = async (agentName: AgentNewChatGuideId) => {
      await openGuide(page, agentName);
      return page
        .locator("[data-testid^='showcase-case-']")
        .evaluateAll((cards) =>
          cards.map((card) => {
            const rect = card.getBoundingClientRect();
            return {
              x: Math.round(rect.x),
              y: Math.round(rect.y),
              w: Math.round(rect.width),
            };
          }),
        );
    };
    const distinct = (values: number[]) => new Set(values).size;

    const video = await cardBoxes("course-video");
    expect(video).toHaveLength(3);
    expect(distinct(video.map((box) => box.y))).toBe(1);
    expect(distinct(video.map((box) => box.x))).toBe(3);

    const decks = await cardBoxes("ppt-master");
    expect(decks).toHaveLength(3);
    expect(distinct(decks.map((box) => box.x))).toBe(1);
    expect(distinct(decks.map((box) => box.w))).toBe(1);
    expect(distinct(decks.map((box) => box.y))).toBe(3);

    const podcasts = await cardBoxes("podcast-studio");
    expect(podcasts).toHaveLength(4);
    expect(distinct(podcasts.map((box) => box.x))).toBe(2);
    expect(distinct(podcasts.map((box) => box.y))).toBe(2);
  });

  test("English UI carries the new copy without Chinese while prompts stay Chinese", async ({
    page,
  }) => {
    await pinEnglishLocale(page);
    mockGuideAPI(page);
    const escape = (value: string) =>
      value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

    for (const entry of CASES) {
      const item = showcaseFor(entry.agent)!.cases.find(
        (value) => value.id === entry.caseId,
      )!;
      await openGuide(page, entry.agent);
      const guide = page.getByTestId("agent-new-chat-guide");
      expect(await guide.innerText(), entry.agent).not.toMatch(/[一-鿿]/);
      await expect(
        page.getByTestId(`showcase-prompt-${entry.caseId}`),
      ).toHaveText(item.actionLabel!.en);
      await page.getByTestId(entry.preview).click();
      const recipe = page.getByTestId("showcase-recipe");
      expect(await recipe.innerText(), entry.agent).not.toMatch(/[一-鿿]/);
      const cta = page.getByTestId("showcase-use-style");
      await expect(cta).toHaveText(item.actionLabel!.en);
      await expect(cta).toHaveAccessibleName(
        new RegExp(`^${escape(item.actionLabel!.en)}: `),
      );
      await cta.click();
      await expect(page.getByRole("dialog")).toHaveCount(0);
      await expect(page.locator("textarea[name='message']")).toHaveValue(
        item.prompt,
      );
      expect(item.prompt).toMatch(/[一-鿿]/);
    }

    await openGuide(page, "creative-studio");
    await expect(page.getByTestId("showcase-filter-card")).toHaveText(
      "Knowledge graphics",
    );
    await expect(page.locator("[data-testid^='showcase-filter-']")).toHaveText([
      "All",
      "Course posters",
      "Knowledge graphics",
      "Knowledge comics",
      "Diagrams",
    ]);
  });
});
