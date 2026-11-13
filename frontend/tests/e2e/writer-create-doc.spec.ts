/**
 * E2E tests for Writer "new document" creation flow.
 *
 * Regression coverage for the bug where clicking 创建 did nothing: the create
 * mutation had no onError, so a failed POST /api/writer/docs (e.g. 403 CSRF in
 * no-auth mode) was swallowed by React Query — no toast, no navigation, "stuck".
 *
 * Mocks the writer + csrf-token endpoints via page.route(). Requires
 * DEER_FLOW_AUTH_DISABLED=1 (set in playwright.config.ts webServer env) to
 * bypass login, mirroring tests/e2e/video-gen.spec.ts.
 *
 * 当前版本隐藏新建文档入口，本组暂时跳过；深链实现保留，恢复入口时重新启用。
 */

import { expect, test, type Page } from "@playwright/test";

const MOCK_DOC_ID = "doc_e2e_0001";

const MOCK_DOC = {
  id: MOCK_DOC_ID,
  user_id: "default",
  title: "未命名文档",
  content_json: "# 标题\n\n",
  doc_type: "general",
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

/** Mock writer + csrf-token endpoints. POST create behavior is overridable. */
function mockWriterAPI(
  page: Page,
  createStatus: { status: number; body: unknown },
) {
  // csrf-token bootstrap (called on AuthProvider mount) — always 200.
  void page.route("**/api/v1/auth/csrf-token", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ token: "mock-csrf-token" }),
    }),
  );

  // List docs — empty so the "新建文档" empty-state button is shown.
  void page.route("**/api/writer/docs", (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
      });
    }
    if (route.request().method() === "POST") {
      return route.fulfill({
        status: createStatus.status,
        contentType: "application/json",
        body: JSON.stringify(createStatus.body),
      });
    }
    return route.fallback();
  });

  // Get a single doc — needed after navigation to the editor route.
  void page.route("**/api/writer/docs/*", (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_DOC),
      });
    }
    return route.fallback();
  });
}

test.describe("Writer — New Document creation", () => {
  test.beforeEach(() => {
    test.skip(
      true,
      "Writer 新建文档入口在当前版本隐藏；保留深链回归，入口恢复时重新启用",
    );
  });

  test("success navigates to the editor route", async ({ page }) => {
    mockWriterAPI(page, { status: 200, body: MOCK_DOC });

    await page.goto("/workspace/writer");
    await page.waitForLoadState("networkidle");

    // Open the new-doc dialog (empty-state button).
    await page.getByRole("button", { name: "新建文档" }).first().click();
    await expect(page.getByText("选择内容类型，开始创作")).toBeVisible();

    // Select a non-default type then create.
    await page.getByRole("button", { name: /公众号文章/ }).click();
    await page.getByRole("button", { name: "创建" }).click();

    // Must navigate to the new doc's editor route.
    await expect(page).toHaveURL(
      new RegExp(`/workspace/writer/${MOCK_DOC_ID}$`),
      {
        timeout: 10_000,
      },
    );
  });

  test("failure shows an error toast and does not navigate", async ({
    page,
  }) => {
    mockWriterAPI(page, {
      status: 403,
      body: { detail: "CSRF token missing. Include X-CSRF-Token header." },
    });

    await page.goto("/workspace/writer");
    await page.waitForLoadState("networkidle");

    await page.getByRole("button", { name: "新建文档" }).first().click();
    await expect(page.getByText("选择内容类型，开始创作")).toBeVisible();

    await page.getByRole("button", { name: "通用写作" }).click();
    await page.getByRole("button", { name: "创建" }).click();

    // Error toast must surface the backend detail — the core of the fix.
    await expect(page.getByText(/创建失败/)).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText(/CSRF token missing/)).toBeVisible({
      timeout: 5_000,
    });

    // And the page must NOT have navigated to a doc route.
    await expect(page).toHaveURL(/\/workspace\/writer$/);
  });
});
