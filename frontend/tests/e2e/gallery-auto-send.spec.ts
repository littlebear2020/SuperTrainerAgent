import { expect, test, type Page } from "@playwright/test";

import { pinEnglishLocale } from "./utils/locale";
import { handleRunStream, mockLangGraphAPI } from "./utils/mock-api";

/**
 * 负向断言的确定性锚（2026-10-05 基建批，替换盲等 3000ms）：
 * 1. /api/models 是 chat 页 mount 必发的启动请求（useModels）——其返回证明
 *    mount effect 波已走完启动 await；
 * 2. 300ms 覆盖同 effect 内「await 后才发送」的微任务窗口。
 * 残余盲区（>300ms 的定时器延迟发送）与旧 3s 固定窗同类——本套件守的
 * SEC-H11 bug 形态是 mount-effect 即时发送，锚点足以捕获。
 */
async function expectMountSettled(page: Page, modelsReady: Promise<unknown>) {
  await modelsReady;
  await page.waitForTimeout(300);
}

test.describe("Gallery auto-send (方案 A: chat page mount effect)", () => {
  test.beforeEach(async ({ page }) => {
    await pinEnglishLocale(page);
    mockLangGraphAPI(page);
  });

  // 安全加固批次 1 项 1.4（SEC-H11）：URL autosend 通道已移除——?msg=（含
  // autosend=1 参数）只预填输入框，绝不自动发送；自动发送唯一通道是
  // sessionStorage pending prompt（gallery handleMakeSame 全量改走该路径）。
  test("URL ?msg=&autosend=1 prefills but does NOT send (SEC-H11)", async ({
    page,
  }) => {
    let streamCallCount = 0;
    await page.route("**/api/langgraph/runs/stream", (route) => {
      streamCallCount++;
      return handleRunStream(route);
    });
    await page.route("**/api/langgraph/threads/*/runs/stream", (route) => {
      streamCallCount++;
      return handleRunStream(route);
    });

    const modelsReady = page.waitForResponse("**/api/models");
    await page.goto("/workspace/chats/new?msg=复刻案例&autosend=1");

    // 输入框被填入（layout initialInput 只认 msg）——mount effect 已执行的直接产物
    const textarea = page.getByPlaceholder(/how can i assist you/i);
    await expect(textarea).toBeVisible({ timeout: 15_000 });
    await expect(textarea).toHaveValue("复刻案例");

    // autosend=1 参数被忽略——不自动发送（mount 波确定性收口，见 expectMountSettled）
    await expectMountSettled(page, modelsReady);
    expect(streamCallCount).toBe(0);
  });

  test("long prompt via sessionStorage auto-sends (>500 chars)", async ({
    page,
  }) => {
    test.skip(
      true,
      "Gallery 入口在当前版本隐藏；保留深链自动发送回归，入口恢复时重新启用",
    );
    let streamCallCount = 0;
    await page.route("**/api/langgraph/runs/stream", (route) => {
      streamCallCount++;
      return handleRunStream(route);
    });
    await page.route("**/api/langgraph/threads/*/runs/stream", (route) => {
      streamCallCount++;
      return handleRunStream(route);
    });

    const longPrompt = "复刻一个很长的案例方法论。".repeat(40); // >500 字
    // 模拟 gallery handleMakeSame 长 prompt 路径：写 sessionStorage 后跳转
    await page.addInitScript((prompt) => {
      sessionStorage.setItem(
        "deerflow_pending_prompt",
        JSON.stringify({ prompt, autoSend: true }),
      );
    }, longPrompt);

    await page.goto("/workspace/chats/new");

    await expect
      .poll(() => streamCallCount, { timeout: 10_000 })
      .toBeGreaterThanOrEqual(1);

    // 先等流式完成并渲染（确定性终态），再断言恰好一次——重复自动发送
    // 会在首轮流完成/渲染前打到 /runs/stream，此时计数已定型。
    await expect(page.getByText("Hello from DeerFlow!")).toBeVisible({
      timeout: 10_000,
    });
    expect(streamCallCount).toBe(1);
  });

  test("?msg= without autosend fills input but does NOT send", async ({
    page,
  }) => {
    let streamCallCount = 0;
    await page.route("**/api/langgraph/runs/stream", (route) => {
      streamCallCount++;
      return handleRunStream(route);
    });
    await page.route("**/api/langgraph/threads/*/runs/stream", (route) => {
      streamCallCount++;
      return handleRunStream(route);
    });

    const modelsReady = page.waitForResponse("**/api/models");
    await page.goto("/workspace/chats/new?msg=只填不发");

    // 输入框应被填入（layout initialInput 路径）
    const textarea = page.getByPlaceholder(/how can i assist you/i);
    await expect(textarea).toBeVisible({ timeout: 15_000 });
    await expect(textarea).toHaveValue("只填不发");

    // 但不应自动发送（mount 波确定性收口）
    await expectMountSettled(page, modelsReady);
    expect(streamCallCount).toBe(0);
  });

  test("plain new chat (no msg/autosend) does NOT send", async ({ page }) => {
    let streamCallCount = 0;
    await page.route("**/api/langgraph/runs/stream", (route) => {
      streamCallCount++;
      return handleRunStream(route);
    });
    await page.route("**/api/langgraph/threads/*/runs/stream", (route) => {
      streamCallCount++;
      return handleRunStream(route);
    });

    const modelsReady = page.waitForResponse("**/api/models");
    await page.goto("/workspace/chats/new");

    // mount 波确定性收口后不应有任何发送
    await expectMountSettled(page, modelsReady);
    expect(streamCallCount).toBe(0);
  });
});
