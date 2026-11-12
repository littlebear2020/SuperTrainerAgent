import { expect, test } from "@playwright/test";

import { pinEnglishLocale } from "./utils/locale";
import { MOCK_THREAD_ID, mockLangGraphAPI } from "./utils/mock-api";

const MOCK_AGENTS = [
  {
    name: "test-agent",
    description: "A test agent for E2E tests",
    system_prompt: "You are a test agent.",
  },
];

test.describe("Agent chat", () => {
  test.beforeEach(async ({ page }) => {
    await pinEnglishLocale(page);
  });

  test("agent gallery page loads and shows agents", async ({ page }) => {
    mockLangGraphAPI(page, { agents: MOCK_AGENTS });

    await page.goto("/workspace/agents");

    // The agent card should appear with the agent name
    await expect(page.getByText("test-agent")).toBeVisible({
      timeout: 15_000,
    });
  });

  test("agent chat page loads with input box", async ({ page }) => {
    mockLangGraphAPI(page, { agents: MOCK_AGENTS });

    await page.goto("/workspace/agents/test-agent/chats/new");

    // The prompt input textarea should be visible
    const textarea = page.getByPlaceholder(/how can i assist you/i);
    await expect(textarea).toBeVisible({ timeout: 15_000 });
  });

  test("agent chat page shows agent badge", async ({ page }) => {
    mockLangGraphAPI(page, { agents: MOCK_AGENTS });

    await page.goto("/workspace/agents/test-agent/chats/new");

    // The agent badge should display in the header (scoped to header to avoid
    // matching the welcome area which also shows the agent name)
    await expect(
      page.locator("header span", { hasText: "test-agent" }),
    ).toBeVisible({ timeout: 15_000 });
  });

  test("agent reply reaches ready without requesting follow-up suggestions", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { agents: MOCK_AGENTS });
    const suggestionPosts: string[] = [];
    await page.route("**/api/threads/*/suggestions", (route) => {
      if (route.request().method() === "POST") {
        suggestionPosts.push(route.request().url());
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ suggestions: ["Hidden agent follow-up"] }),
      });
    });

    await page.goto("/workspace/agents/test-agent/chats/new");
    const textarea = page.getByPlaceholder(/how can i assist you/i);
    await expect(textarea).toBeVisible({ timeout: 15_000 });
    await textarea.fill("Hello agent");
    await textarea.press("Enter");

    await expect(page.getByText("Hello from DeerFlow!")).toBeVisible({
      timeout: 10_000,
    });
    await expect(page).toHaveURL(
      new RegExp(`/workspace/agents/test-agent/chats/${MOCK_THREAD_ID}$`),
    );
    await expect(
      page.locator("button[aria-label='Submit'] svg").first(),
    ).toHaveClass(/lucide-arrow-up/, { timeout: 10_000 });
    expect(suggestionPosts).toEqual([]);
    await expect(page.getByText("Hidden agent follow-up")).toHaveCount(0);
  });
});
