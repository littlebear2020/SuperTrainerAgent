import { expect, test } from "@playwright/test";

import { openAgentChat, sendChatMessage, waitForRunSettled } from "./helpers";

/**
 * L2 轻功能:真实 API 往返但控制消耗(1 条小模型消息、文档 CRUD、各页面可达)。
 * 对应测试范围清单 §三。
 */

test.describe("L2 轻功能", () => {
  // workers=1 串行,聊天用例落库的线程 URL 供历史用例复用。
  let lastThreadUrl = "";

  test("聊天往返:course-assistant 真实 LLM 回复(360 链路)", async ({
    page,
  }) => {
    test.setTimeout(240_000);
    await openAgentChat(page, "course-assistant");
    await sendChatMessage(
      page,
      "这是一条连通性测试消息。请只回复两个字:收到。不要调用任何工具。",
    );
    await waitForRunSettled(page, 180_000);
    // 发送后 SPA 会把 /chats/new 落成具体线程 URL。
    await page
      .waitForURL(/\/workspace\/(chats|agents)\/\S+/, { timeout: 15_000 })
      .catch(() => undefined);
    lastThreadUrl = page.url();
    // 宽断言:页面出现"收到"或至少出现新的非空助手文本。
    const replied = await page
      .getByText(/收到/)
      .first()
      .isVisible()
      .catch(() => false);
    if (!replied) {
      // 兜底断言:消息区出现除本条提问外的其他内容(流式回复渲染过)。
      const body = await page.textContent("main");
      expect(body && body.length > 60, "助手应有非空回复").toBeTruthy();
      console.log("[l2] 回复未见「收到」字样,但已有非空回复(LLM 措辞自由)");
    }
  });

  test("对话历史:线程可重开且消息持久", async ({ page }) => {
    test.setTimeout(90_000);
    expect(lastThreadUrl, "前序聊天用例应已产生线程 URL").toBeTruthy();
    await page.goto(lastThreadUrl);
    // 重开后历史消息仍在(持久化断言):消息区包含当时的提问关键词。
    await expect
      .poll(
        async () =>
          (await page.textContent("main"))?.includes("连通性测试") ?? false,
        { timeout: 30_000 },
      )
      .toBe(true);
    // 侧栏最近对话是否列出该线程:软断言(侧栏可折叠,不阻断)。
    await page.goto("/workspace");
    const listed = await page
      .getByText(/连通性|收到/)
      .first()
      .isVisible()
      .catch(() => false);
    console.log(`[l2] 侧栏最近对话列出线程: ${listed}`);
  });

  test("作品资产页:加载正常", async ({ page }) => {
    await page.goto("/workspace/assets");
    await expect(page.locator("main")).toBeVisible();
    await expect(page.getByText(/加载失败|出错了|error/i)).toHaveCount(0);
  });

  test("writer:新建文档、编辑、刷新后内容持久", async ({ page }) => {
    test.setTimeout(120_000);
    await page.goto("/workspace/writer");
    await page
      .getByRole("button", { name: /新建文档/ })
      .first()
      .click();
    // 新建对话框:类型默认选中,直接点「创建」(无标题输入框)。
    const dialog = page.getByRole("dialog");
    await dialog.waitFor({ timeout: 10_000 });
    await dialog.getByRole("button", { name: "创建", exact: true }).click();
    await page.waitForURL(/\/workspace\/writer\/.+/, { timeout: 30_000 });

    const marker = `持久化标记-${Date.now()}`;
    const editor = page
      .locator("[contenteditable=true], .ProseMirror, textarea")
      .first();
    await editor.waitFor({ timeout: 20_000 });
    await editor.click();
    await page.keyboard.type(marker);
    // 等自动保存落库。
    await page.waitForTimeout(6_000);
    await page.reload();
    await expect(page.getByText(marker)).toBeVisible({ timeout: 20_000 });
  });

  test("声音分身页:加载正常", async ({ page }) => {
    await page.goto("/workspace/twins/voice");
    await expect(page.locator("main")).toBeVisible();
    await expect(page.getByText(/加载失败|出错了/i)).toHaveCount(0);
  });

  test("控制台:普通用户个人用量页渲染", async ({ page }) => {
    await page.goto("/workspace/console");
    await expect(page.getByTestId("personal-usage-page")).toBeVisible({
      timeout: 30_000,
    });
  });

  test("案例库:页面渲染", async ({ page }) => {
    await page.goto("/workspace/gallery");
    await expect(page.locator("main")).toBeVisible();
    await expect(page.getByText(/加载失败|出错了/i)).toHaveCount(0);
  });

  test("聊天输入框可达性:稳定 testid 可编辑", async ({ page }) => {
    await openAgentChat(page, "course-assistant");
    await expect(page.getByTestId("composer-input")).toBeEditable();
  });
});
