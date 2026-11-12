import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

const HOME_VIDEO_ENTRIES = ["course-video", "ai_image", "ai_video"];
const HOME_TEXT_ENTRIES = ["creative-studio", "ppt-master", "podcast-studio"];

// DSN-003b（2026-09-14）：默认语言改中文——Playwright 全新 context 无
// localStorage 语言偏好，走默认 zh-CN，CTA 显示「进入工作台」。正则取
// 中英并集，语言默认再翻转时不断言漂移。
const ENTER_WORKSPACE_CTA = /进入工作台|enter workspace/i;

test.describe("Landing page", () => {
  test("renders the header and hero section", async ({ page }) => {
    await page.goto("/");

    // Header brand name (renamed DeerFlow -> Trainer AI)
    await expect(
      page.locator("header h1", { hasText: "Trainer AI" }),
    ).toBeVisible();

    // Hero call-to-action (renamed "Get Started" -> "Enter Workspace")
    await expect(
      page.getByRole("link", { name: ENTER_WORKSPACE_CTA }),
    ).toBeVisible();
  });

  test("hero CTA navigates to the workspace", async ({ page }) => {
    // 2026-10-17 并发健壮性（Lane 2 全量 5-worker 负载暴露，规划侧复核 41/42 复现）：
    // ① 超时放宽到 60s——负载下 /workspace 首渲慢，30s 会被导航+卡片断言耗尽；
    // ② waitForURL 与 click 并发注册——顺序 await 会在「导航先于监听注册」时
    // 永久等待（SPA 路由不再触发新 navigation 事件）。
    test.setTimeout(60_000);
    mockLangGraphAPI(page);

    await page.goto("/");

    const enterWorkspace = page.getByRole("link", {
      name: ENTER_WORKSPACE_CTA,
    });
    await expect(enterWorkspace).toBeVisible();
    await Promise.all([
      page.waitForURL("**/workspace"),
      enterWorkspace.click(),
    ]);

    // /workspace 直接渲染六能力首页；URL 必须精确，防止把登录跳转误判为成功。
    await page.waitForURL("**/workspace");
    await expect(page).toHaveURL("/workspace");
    await expect(page.getByText("让好内容，从一个想法开始")).toBeVisible();

    for (const id of [...HOME_VIDEO_ENTRIES, ...HOME_TEXT_ENTRIES]) {
      await expect(page.locator(`[data-course-entry='${id}']`)).toBeVisible();
    }
  });
});
