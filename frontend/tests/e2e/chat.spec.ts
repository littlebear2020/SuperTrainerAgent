import { expect, test } from "@playwright/test";

import { pinEnglishLocale } from "./utils/locale";
import {
  handleRunStream,
  MOCK_THREAD_ID,
  mockLangGraphAPI,
} from "./utils/mock-api";

test.describe("Chat workspace", () => {
  test.beforeEach(async ({ page }) => {
    await pinEnglishLocale(page);
    mockLangGraphAPI(page);
  });

  test("new chat page loads with input box", async ({ page }) => {
    await page.goto("/workspace/chats/new");

    const textarea = page.getByPlaceholder(/how can i assist you/i);
    await expect(textarea).toBeVisible({ timeout: 15_000 });
  });

  test("can type a message in the input box", async ({ page }) => {
    await page.goto("/workspace/chats/new");

    const textarea = page.getByPlaceholder(/how can i assist you/i);
    await expect(textarea).toBeVisible({ timeout: 15_000 });

    await textarea.fill("Hello, DeerFlow!");
    await expect(textarea).toHaveValue("Hello, DeerFlow!");
  });

  test("sending a message triggers API call and shows response", async ({
    page,
  }) => {
    let streamCalled = false;
    await page.route("**/runs/stream", (route) => {
      streamCalled = true;
      return handleRunStream(route);
    });

    await page.goto("/workspace/chats/new");

    const textarea = page.getByPlaceholder(/how can i assist you/i);
    await expect(textarea).toBeVisible({ timeout: 15_000 });

    await textarea.fill("Hello");
    await textarea.press("Enter");

    await expect.poll(() => streamCalled, { timeout: 10_000 }).toBeTruthy();

    // The AI response should appear in the chat
    await expect(page.getByText("Hello from DeerFlow!")).toBeVisible({
      timeout: 10_000,
    });
  });

  test("first reply reaches ready without requesting follow-up suggestions", async ({
    page,
  }) => {
    const suggestionPosts: string[] = [];
    const galleryCategoryRequests: string[] = [];
    await page.route("**/api/gallery/categories", (route) => {
      galleryCategoryRequests.push(route.request().url());
      return route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Unauthorized" }),
      });
    });
    await page.route("**/api/threads/*/suggestions", (route) => {
      if (route.request().method() === "POST") {
        suggestionPosts.push(route.request().url());
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ suggestions: ["Hidden follow-up"] }),
      });
    });

    await page.goto("/workspace/chats/new");
    const textarea = page.getByPlaceholder(/how can i assist you/i);
    await expect(textarea).toBeVisible({ timeout: 15_000 });
    await textarea.fill("Hello");
    await textarea.press("Enter");

    await expect(page.getByText("Hello from DeerFlow!")).toBeVisible({
      timeout: 10_000,
    });
    await expect(page).toHaveURL(new RegExp(`${MOCK_THREAD_ID}$`));
    await expect(
      page.locator("button[aria-label='Submit'] svg").first(),
    ).toHaveClass(/lucide-arrow-up/, { timeout: 10_000 });
    expect(suggestionPosts).toEqual([]);
    expect(galleryCategoryRequests).toEqual([]);
    await expect(page.getByTitle("分享到灵感库")).toHaveCount(0);
    await expect(page.getByText("Hidden follow-up")).toHaveCount(0);
  });
});
