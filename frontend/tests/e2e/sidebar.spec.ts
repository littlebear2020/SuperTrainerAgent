import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

test.describe("Sidebar navigation", () => {
  test("shows only home, works, console, recent chats, and settings", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      threads: [{ thread_id: "sidebar-thread", title: "近期对话样例" }],
    });
    await page.goto("/workspace");

    const sidebar = page.locator("[data-sidebar='sidebar']");
    const home = sidebar.locator("a[href='/workspace']");
    await expect(home).toBeVisible({ timeout: 15_000 });
    await expect(home).toHaveText("首页");
    await expect(
      sidebar.locator("a[href='/workspace/video-gen']"),
    ).toContainText("我的作品");
    await expect(sidebar.locator("a[href='/workspace/console']")).toBeVisible();

    for (const href of [
      "/workspace/chats",
      "/workspace/agents",
      "/workspace/gallery",
      "/workspace/twins/voice",
      "/workspace/writer",
      "/workspace/assets",
    ]) {
      await expect(sidebar.locator(`a[href='${href}']`)).toHaveCount(0);
    }
    await expect(
      sidebar.getByText("最近的对话", { exact: true }),
    ).toBeVisible();
    await expect(
      sidebar.getByRole("button", { name: "设置", exact: true }),
    ).toBeVisible();
    await expect(sidebar.getByText("设置和更多", { exact: true })).toHaveCount(0);
  });
});
