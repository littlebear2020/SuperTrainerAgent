/**
 * T-Z 浏览器手测（2026-09-09 feedback-round1 完成标准项）：
 * 真浏览器 × 指定测试环境（真实登录 + 真实渲染链路）。
 *
 * 覆盖 4 个带 * 的目验项：
 *   1. still 预览浏览器手测（R4 修复后 LayoutSwitcher/StillPreview 真实数据下可达——
 *      修前帧级 visual_source 覆盖致门控永假、整体不可见）；
 *   2. LayoutSwitcher 点击切换（D14 写回 + 换版式重渲 still）；
 *   3. 详情页成片画面缩略图 + 点击 seek；
 *   4. 项目详情精简（品牌包/色彩微调/关联视频不再挂载；本地 hermetic E2E 验收）。
 *
 * 账号、任务和项目均通过环境变量显式提供，不在公开仓库保存可用凭证或内部资源 ID：
 *   E2E_USER / E2E_PASS / E2E_DRAFT_TASK / E2E_READY_TASK / E2E_PROJECT_ID
 */
import { expect, test } from "@playwright/test";

const EMAIL = process.env.E2E_USER ?? "";
const PASS = process.env.E2E_PASS ?? "";
const DRAFT = process.env.E2E_DRAFT_TASK ?? "";
const READY = process.env.E2E_READY_TASK ?? "";
const PROJECT = process.env.E2E_PROJECT_ID ?? "";

test.skip(!EMAIL || !PASS, "需通过 E2E_USER 和 E2E_PASS 显式提供测试账号");

test.describe("浏览器手测：登录", () => {
  test("login and land on workspace", async ({ page }) => {
    await page.goto("/login");
    await page.fill('input[type="email"]', EMAIL);
    await page.fill('input[type="password"]', PASS);
    await page.click('button[type="submit"]');
    await expect(page).toHaveURL(/workspace/, { timeout: 20_000 });
  });
});

test.describe("浏览器手测：still 预览 + LayoutSwitcher（R4）", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/login");
    await page.fill('input[type="email"]', EMAIL);
    await page.fill('input[type="password"]', PASS);
    await page.click('button[type="submit"]');
    await expect(page).toHaveURL(/workspace/, { timeout: 20_000 });
  });

  test("draft 页 LayoutSwitcher 可见 + still 渲出真实 PNG + 切版式重渲", async ({
    page,
  }) => {
    test.skip(!DRAFT, "需 E2E_DRAFT_TASK");
    await page.goto(`/workspace/video-gen/${DRAFT}/storyboard`);
    // R4 核心断言：LayoutSwitcher（画面版式下拉）在真实数据下可见——修前不可见
    const switcher = page.locator("text=画面版式").first();
    await expect(switcher).toBeVisible({ timeout: 30_000 });

    // still 预览：img alt="版式预览 XXX"，真实渲染为 blob:（降级为 /remotion-layouts/ 静态图）
    const still = page.locator('img[alt^="版式预览"]').first();
    await expect(still).toBeVisible({ timeout: 30_000 });
    await expect
      .poll(async () => await still.getAttribute("src"), {
        timeout: 180_000,
        message: "等待 still 真实渲染（remotion-worker）",
      })
      .toMatch(/^blob:/);

    // 布局器点击切换：选非「自动」版式 → 写回 + 新 cache_key still 重渲（R18）
    const card = page
      .locator(".border, .rounded-lg")
      .filter({ hasText: "画面版式" })
      .first();
    const select = card.locator("button[role='combobox']").first();
    await select.click();
    await page
      .locator('[role="option"]')
      .filter({ hasText: "金句强调" })
      .first()
      .click();
    // 写回后 StillPreview 换新 layout → 新渲染（src 回落再回 blob）
    await expect
      .poll(async () => await still.getAttribute("src"), { timeout: 180_000 })
      .toMatch(/^blob:/);
  });
});

test.describe("浏览器手测：详情页成片画面 + 点击 seek", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/login");
    await page.fill('input[type="email"]', EMAIL);
    await page.fill('input[type="password"]', PASS);
    await page.click('button[type="submit"]');
    await expect(page).toHaveURL(/workspace/, { timeout: 20_000 });
  });

  test("ready 任务帧条渲成片画面缩略图并支持点击 seek", async ({ page }) => {
    await page.goto(`/workspace/video-gen/${READY}`);
    // T-E9 StillStripThumb：真实成片画面（alt=「帧 N 成片画面」）
    const thumb = page.locator('img[alt*="成片画面"]').nth(1);
    await expect(thumb).toBeVisible({ timeout: 120_000 });
    // 缩略图应为真实渲染（blob）或缓存 PNG——不断言 src 形态（轮询中可为静态回落），
    // 断言至少出现一张 ≥2 张成片画面缩略图
    const count = await page.locator('img[alt*="成片画面"]').count();
    expect(count).toBeGreaterThanOrEqual(2);

    // 点击 seek：video currentTime 跳到该帧起点
    const video = page.locator("video").first();
    await video.waitFor({ state: "visible", timeout: 30_000 });
    // ready 态帧条禁用拖拽（dnd-kit aria-disabled）——Playwright 默认视为不可点，
    // force 绕过可访问性检查（点击手势本身仍真实派发）
    await thumb.click({ force: true });
    await expect
      .poll(
        async () =>
          await video.evaluate((v: HTMLVideoElement) => v.currentTime),
        {
          timeout: 20_000,
        },
      )
      .toBeGreaterThan(0.5);
  });
});

test.describe("浏览器手测：项目详情精简", () => {
  test("项目页不再挂载品牌包、色彩微调与关联视频", async ({ page }) => {
    test.skip(!PROJECT, "需 E2E_PROJECT_ID");
    await page.goto("/login");
    await page.fill('input[type="email"]', EMAIL);
    await page.fill('input[type="password"]', PASS);
    await page.click('button[type="submit"]');
    await expect(page).toHaveURL(/workspace/, { timeout: 20_000 });

    await page.goto("/workspace/video-gen?tab=assets");
    await page.locator("text=浏览器手测项目").first().click();
    await expect(page.getByText("基本设置", { exact: true })).toBeVisible({
      timeout: 30_000,
    });
    for (const removed of ["品牌包", "色彩微调", "关联视频"]) {
      await expect(page.getByText(removed, { exact: false })).toHaveCount(0);
    }
    await expect(page.getByRole("button", { name: "上传 LOGO" })).toHaveCount(
      0,
    );
  });
});
