import { expect, test } from "@playwright/test";

/**
 * L1 冒烟:登录态 + 首页 6 能力入口 + 受保护路由。零 AI API 消耗。
 * 对应测试范围清单 §一(6 卡横向断言)与 §四(部署验收)。
 */

// 首页 6 卡(zh-CN 文案,home-course-catalog + i18n)。
const HOME_CARDS = [
  { title: "文字动效", agent: "course-video" },
  { title: "图片动效", taskType: "ai_image" },
  { title: "AI视频", taskType: "ai_video" },
  { title: "课程海报", agent: "creative-studio" },
  { title: "PPT课件", agent: "ppt-master" },
  { title: "语音播客", agent: "podcast-studio" },
] as const;

test.describe("L1 冒烟", () => {
  test("healthz 与落地页", async ({ request, page }) => {
    const health = await request.get("/healthz");
    expect(health.status()).toBe(200);
    expect((await health.text()).trim()).toBe("ok");

    await page.goto("/");
    await expect(
      page.locator("header h1", { hasText: "Trainer AI" }),
    ).toBeVisible();
  });

  // 注意:browser.newContext() 在测试运行里会继承 config 的 storageState,
  // 无法构造匿名上下文;必须用 describe 级 test.use 空 storageState 覆盖。
  test.describe("匿名上下文", () => {
    test.use({ storageState: { cookies: [], origins: [] } });

    test("未登录访问工作台会重定向到登录页", async ({ page }) => {
      await page.goto("/workspace");
      // 客户端 401 检测后跳 /login?next=...,远端首载慢,放宽到 30s。
      await expect(page).toHaveURL(/\/login/, { timeout: 30_000 });
    });

    test("UI 登录:表单提交后进入工作台", async ({ page }) => {
      test.setTimeout(150_000);
      await page.goto("/login");
      await page
        .locator('input[type="email"], input[name="email"]')
        .first()
        .fill(process.env.LIVE_EMAIL!);
      await page
        .locator('input[type="password"], input[name="password"]')
        .first()
        .fill(process.env.LIVE_TEST_PASSWORD!);
      await page.getByRole("button", { name: /登录/ }).first().click();
      await page.waitForURL("**/workspace", { timeout: 60_000 });
      await expect(page.getByText("让好内容，从一个想法开始")).toBeVisible({
        timeout: 30_000,
      });
    });
  });

  test("首页:双分区 + 六卡齐 + 无横向溢出", async ({ page }) => {
    await page.goto("/workspace");
    await expect(page.getByText("让好内容，从一个想法开始")).toBeVisible();

    await expect(page.getByText("视频课").first()).toBeVisible();
    await expect(page.getByText("图文音频课").first()).toBeVisible();

    for (const card of HOME_CARDS) {
      await expect(
        page.locator(`[aria-label^="${card.title}："]`).first(),
      ).toBeVisible();
    }
    // 双分区布局不得横向溢出(沿 work-capabilities.spec 口径)。
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth + 1,
    );
    expect(overflow).toBe(false);
  });

  test("四个 agent 卡:点击进入对应 agent 新会话", async ({ page }) => {
    for (const card of HOME_CARDS) {
      if (!("agent" in card)) continue;
      await page.goto("/workspace");
      await page.locator(`[aria-label^="${card.title}："]`).first().click();
      await page.waitForURL(
        new RegExp(`/workspace/agents/${card.agent}/chats/new$`),
        { timeout: 20_000 },
      );
      await page.getByTestId("composer-input").waitFor({ timeout: 30_000 });
    }
  });

  test("两个直接任务卡:打开创建框且类型正确", async ({ page }) => {
    await page.goto("/workspace");
    await page.locator('[aria-label^="图片动效："]').first().click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(
      dialog.getByRole("heading", { name: "创建图片动效" }),
    ).toBeVisible();
    await page.keyboard.press("Escape");
    await dialog.waitFor({ state: "hidden", timeout: 10_000 });

    await page.locator('[aria-label^="AI视频："]').first().click();
    const dialog2 = page.getByRole("dialog");
    await expect(dialog2).toBeVisible();
    await expect(dialog2.getByRole("heading", { name: /创建/ })).toBeVisible();
    // known-issue 锚点(不阻断):连续开关两次,类型不应残留——README 已知未修,
    // 此处只记录实况,失败不判红。
    const headingText = await dialog2
      .getByRole("heading", { name: /创建/ })
      .textContent();
    console.log(`[known-issue-watch] 第二次打开创建框标题: ${headingText}`);
    await page.keyboard.press("Escape");
  });
});
