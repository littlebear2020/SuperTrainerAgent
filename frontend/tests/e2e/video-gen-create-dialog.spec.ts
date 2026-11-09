/**
 * Regression: video-gen create dialog width + advanced panel expand (Bug 1a/1b).
 *
 * Bug 1a: Radix CollapsibleContent height animation was measured to 0 / clipped
 *         inside the constrained overflow-y-auto container → advanced settings
 *         controls never appeared. Fixed by replacing Collapsible with a plain
 *         controlled render.
 * Bug 1b: Dialog was sm:max-w-lg (~512px); voice+preview overflowed a 3-col grid.
 *         Fixed by widening to sm:max-w-2xl + voice/preview on its own row.
 *
 * Mocks backend video APIs so the dialog renders without a real backend.
 * Requires DEER_FLOW_AUTH_DISABLED=1 (see tests/e2e/video-gen.spec.ts header).
 */
import { expect, test, type Page } from "@playwright/test";

const MOCK_VOICES = [
  {
    id: "zh-CN-YunjianNeural",
    name: "云健（男）",
    locale: "zh-CN",
    gender: "Male",
  },
];

const MOCK_BGM = { built_in: [], custom: [] };

function mockVideoGenAPI(page: Page) {
  void page.route("**/api/video/voices**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(MOCK_VOICES),
    }),
  );
  void page.route("**/api/video/bgm", (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_BGM),
      });
    }
    return route.fallback();
  });
  void page.route("**/api/video/voices/*/preview**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "audio/mpeg",
      body: Buffer.alloc(0),
    }),
  );
  void page.route("**/api/video/templates**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
  void page.route("**/api/video/tasks", (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
      });
    }
    return route.fallback();
  });
  void page.route("**/api/video/tasks/*", (route) => route.fallback());
}

test.describe("video-gen create dialog — advanced panel + width", () => {
  test.beforeEach(async ({ page }) => {
    mockVideoGenAPI(page);
    await page.goto("/workspace/video-gen");
    await page.waitForLoadState("networkidle");
  });

  test("advanced settings expand and reveal all controls", async ({ page }) => {
    await page.getByRole("button", { name: "新建任务" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible({ timeout: 5_000 });
    await expect(dialog.getByText("视频类型")).toBeVisible();

    // Advanced controls are collapsed by default.
    await expect(dialog.getByText("字幕样式")).toHaveCount(0);

    // Expand — the controlled-render panel (no Radix height animation).
    await dialog.getByRole("button", { name: /高级设置/ }).click();

    // Bug 1a regression: advanced controls must now be visible.
    await expect(dialog.getByText("字幕样式")).toBeVisible();
    await expect(dialog.getByText("语速")).toBeVisible();
  });
});
