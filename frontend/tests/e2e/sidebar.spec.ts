import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

test.describe("Sidebar navigation", () => {
  test("sidebar contains Chats link and the Agents entry", async ({ page }) => {
    mockLangGraphAPI(page);

    await page.goto("/workspace/chats/new");

    // Sidebar uses data-sidebar="menu-button" with asChild rendering on <Link>
    const sidebar = page.locator("[data-sidebar='sidebar']");
    await expect(sidebar.locator("a[href='/workspace/chats']")).toBeVisible({
      timeout: 15_000,
    });
    // 2026-09-22 工作台恢复：侧边栏加回「智能体」入口（b68f3a7e4 反向，
    // 经 nav-routes 单一事实源）。/workspace/agents 列表页路由一直保留。
    await expect(sidebar.locator("a[href='/workspace/agents']")).toBeVisible();
  });
});
