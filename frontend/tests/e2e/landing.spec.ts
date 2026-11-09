import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

// 现役 6 agent 卡片名（2026-09-22 工作台恢复，R1 僵尸卡守卫）。
const ACTIVE_AGENT_CARDS = [
  "视频课程专家",
  "VIMAX 视频大师",
  "PPT Master",
  "创意工作",
  "协同写作专家",
  "课程助理",
];

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

    // 2026-09-22 工作台恢复：/workspace 直接渲染九宫格工作台首页（hero +
    // 专家 Agent 网格 + 近期项目），不再 307 跳转 course-assistant。URL 断言
    // 必须整串精确——后缀式断言（/\/workspace$/）会被 /login?next=%2Fworkspace
    // 假匹配。
    await page.waitForURL("**/workspace");
    await expect(page).toHaveURL("/workspace");
    await expect(page.getByText("想创造什么？")).toBeVisible();

    // R1 守卫：恰好现役 6 张 agent 卡，无退役 agent 僵尸卡。
    for (const cardName of ACTIVE_AGENT_CARDS) {
      await expect(page.getByRole("heading", { name: cardName })).toBeVisible();
    }
  });
});
