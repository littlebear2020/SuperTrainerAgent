import { expect, test, type Page } from "@playwright/test";

import {
  AGENT_NEW_CHAT_GUIDE_IDS,
  type AgentNewChatGuideId,
} from "../../src/core/agents/new-chat";
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

  async function openGuide(page: Page, agentName: AgentNewChatGuideId) {
    await page.goto(`/workspace/agents/${agentName}/chats/new`);
    await expect(page.locator("textarea[name='message']")).toBeVisible();
  }

  for (const agentName of AGENT_NEW_CHAT_GUIDE_IDS) {
    test(`${agentName} renders four cases and prefills each without sending`, async ({
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

  test("podcast case only prefills and explicit submit creates one run with podcast assistant", async ({
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
    const item = zhCN.agents.newChatGuides["podcast-studio"].cases[0];
    await page.getByRole("button", { name: item.title, exact: true }).click();
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

  test("URL handoff wins over case clicks and never auto-sends", async ({
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

  test("sessionStorage handoff is consumed once and not overwritten", async ({
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

  test("English prompts come from the locale and preserve Co-writer markers", async ({
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

  test("only explicit submission creates one thread and stage markers stay hidden", async ({
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

  test("existing target threads keep their messages and do not show a guide", async ({
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
  });

  test("retired aliases resolve to their approved guide", async ({ page }) => {
    mockGuideAPI(page);
    for (const [alias, agentName] of [
      ["card-poster", "creative-studio"],
      ["course-ppt", "ppt-master"],
    ] as const) {
      await page.goto(`/workspace/agents/${alias}/chats/new`);
      await expect(
        page.getByRole("heading", {
          name: zhCN.agents.newChatGuides[agentName].title,
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

  test("manual retry, not automatic query retry, recovers an agent failure", async ({
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
        name: zhCN.agents.newChatGuides["course-video"].title,
      }),
    ).toBeVisible();
  });

  test("loading disables submission and reveals cases only after agent success", async ({
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
        name: zhCN.agents.newChatGuides["course-video"].title,
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
  });

  test("desktop and mobile keep regions separate and keyboard usable", async ({
    page,
  }) => {
    mockGuideAPI(page);
    await openGuide(page, "course-video");

    async function expectNoOverlap() {
      const cases = await page.getByTestId("agent-guide-cases").boundingBox();
      const input = await page.getByTestId("agent-guide-input").boundingBox();
      const tips = await page.getByTestId("agent-guide-tips").boundingBox();
      expect(cases).not.toBeNull();
      expect(input).not.toBeNull();
      expect(tips).not.toBeNull();
      expect(cases!.y + cases!.height).toBeLessThanOrEqual(input!.y);
      expect(input!.y + input!.height).toBeLessThanOrEqual(tips!.y);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth,
        ),
      ).toBe(true);
    }

    await page.setViewportSize({ width: 1440, height: 900 });
    await expectNoOverlap();
    await page.setViewportSize({ width: 390, height: 844 });
    await expectNoOverlap();

    const textarea = page.locator("textarea[name='message']");
    await expect(textarea).not.toBeFocused();
    const first = zhCN.agents.newChatGuides["course-video"].cases[0];
    const firstCase = page.getByRole("button", {
      name: first.title,
      exact: true,
    });
    await firstCase.focus();
    await page.keyboard.press("Enter");
    await expect(textarea).toHaveValue(first.prompt);
    await expect(textarea).toBeFocused();
  });
});
