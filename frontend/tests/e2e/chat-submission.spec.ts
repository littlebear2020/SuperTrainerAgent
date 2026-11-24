/**
 * PR2（2026-11-24 前端可靠性批）：聊天提交受理的行为验收（mock API）。
 *
 * `sendMessage` 以 SDK onCreated（= 线程与 run 已创建）为受理点：受理前停止 / 失败
 * 保留输入并回退地址栏，受理后即使 SSE 仍开着输入也已清空；纯附件可发；上传可取消
 * 且不跨线程污染；每个失败只提示一次；全程无未处理 rejection。
 *
 * - 线程创建与上传的时机由用例控制（放行 / 失败 / 挂起）
 * - 「受理后长流」用真实 SSE 服务保持连接，并在响应头回显 Content-Location
 *   （SDK 只凭它触发 onRunCreated）
 */

import { expect, test, type Page, type Route } from "@playwright/test";

import { startHeldOpenStreamServer } from "./utils/held-open-stream";
import { pinEnglishLocale } from "./utils/locale";
import { mockLangGraphAPI, runStreamThreadId } from "./utils/mock-api";

const COMPOSER = "[data-testid='composer-input']";
const SUBMIT = "button[aria-label='Submit']";
const FILE_INPUT = "input[aria-label='Upload files']";
const THREAD_B = "00000000-0000-0000-0000-0000000000b";
const NEW_CHAT_URL = /\/workspace\/chats\/[0-9a-f-]{36}$/;

const composer = (page: Page) => page.locator(COMPOSER);
/** 流式进行中时提交按钮显示方块（停止）图标。 */
const stopIcon = (page: Page) => page.locator(`${SUBMIT} svg.lucide-square`);
const toasts = (page: Page) => page.locator("[data-sonner-toast]");

function sseFrame(event: string, data: unknown) {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

function trackPageErrors(page: Page) {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  return errors;
}

async function fulfillCreate(route: Route, threadId: string) {
  try {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        thread_id: threadId,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        metadata: {},
        status: "idle",
        values: {},
      }),
    });
  } catch {
    // 被停止中止的请求：SDK 已 abort，fulfill 会抛——忽略
  }
}

/**
 * 拦截线程创建（注册在共享 mock 之后，优先命中）。handler 拿到第几次请求与
 * 请求体里的 thread_id（页面预生成的 UUID）。
 */
async function interceptThreadCreate(
  page: Page,
  handler: (
    route: Route,
    payload: { thread_id?: string },
    index: number,
  ) => Promise<void>,
) {
  let index = 0;
  await page.route("**/api/langgraph/threads", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    index += 1;
    const payload = JSON.parse(route.request().postData() ?? "{}") as {
      thread_id?: string;
    };
    await handler(route, payload, index);
  });
}

/** 上传接口替身：记录请求对应的线程 id，可由用例挂起后放行。 */
async function interceptUploads(page: Page, options?: { hold?: boolean }) {
  const requests: string[] = [];
  const gate = {
    resolve: null as (() => void) | null,
    promise: null as Promise<void> | null,
  };
  gate.promise = new Promise<void>((resolve) => {
    gate.resolve = resolve;
  });
  await page.route("**/api/threads/*/uploads", async (route) => {
    // /api/threads/{id}/uploads
    const threadId =
      new URL(route.request().url()).pathname.split("/")[3] ?? "";
    requests.push(threadId);
    if (options?.hold) await gate.promise;
    try {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          success: true,
          message: "ok",
          files: [
            {
              filename: "notes.txt",
              size: 11,
              path: `threads/${threadId}/uploads/notes.txt`,
              virtual_path: `/threads/${threadId}/uploads/notes.txt`,
              artifact_url: `/api/threads/${threadId}/artifacts/notes.txt`,
            },
          ],
        }),
      });
    } catch {
      // 被取消的上传：XHR 已 abort，fulfill 会抛——忽略
    }
  });
  return { requests, release: () => gate.resolve?.() };
}

async function attachFile(page: Page) {
  await page.locator(FILE_INPUT).setInputFiles({
    name: "notes.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("hello notes"),
  });
  await expect(page.getByText("notes.txt").first()).toBeVisible();
}

test.describe("Chat submission acceptance", () => {
  test.beforeEach(async ({ page }) => {
    await pinEnglishLocale(page);
  });

  test("stop before acceptance keeps the draft and rolls the URL back; a later retry succeeds", async ({
    page,
  }) => {
    mockLangGraphAPI(page);
    const pageErrors = trackPageErrors(page);
    const creates: string[] = [];
    const createGate = {
      resolve: null as (() => void) | null,
      promise: null as Promise<void> | null,
    };
    createGate.promise = new Promise<void>((resolve) => {
      createGate.resolve = resolve;
    });
    await interceptThreadCreate(page, async (route, payload) => {
      creates.push(payload.thread_id ?? "");
      await createGate.promise;
      await fulfillCreate(route, payload.thread_id ?? "fallback");
    });

    await page.goto("/workspace/chats/new");
    await composer(page).fill("停止前发出的消息");
    await composer(page).press("Enter");

    // 提交后地址栏先落到预生成 UUID；线程创建被挂起，此时流处于 loading
    await expect(page).toHaveURL(NEW_CHAT_URL);
    await expect.poll(() => creates.length).toBe(1);
    await expect(stopIcon(page)).toBeVisible({ timeout: 10_000 });
    // 受理前：输入还在
    await expect(composer(page)).toHaveValue("停止前发出的消息");

    await page.locator(SUBMIT).click();
    await expect(page).toHaveURL("/workspace/chats/new");
    await expect(composer(page)).toHaveValue("停止前发出的消息");
    await expect(toasts(page)).toHaveCount(0);

    // 放行被中止的创建请求，等 SDK 空闲后重试
    createGate.resolve?.();
    await expect(stopIcon(page)).toHaveCount(0, { timeout: 10_000 });
    await composer(page).press("Enter");
    await expect(page.getByText("Hello from DeerFlow!")).toBeVisible({
      timeout: 10_000,
    });
    expect(creates).toHaveLength(2);
    expect(creates[1]).not.toBe("");
    await expect(page).toHaveURL(`/workspace/chats/${creates[1]}`);
    await expect(toasts(page)).toHaveCount(0);
    expect(pageErrors).toEqual([]);
  });

  test("once accepted the composer clears even while the SSE stream stays open", async ({
    page,
  }) => {
    mockLangGraphAPI(page);
    const pageErrors = trackPageErrors(page);
    const server = await startHeldOpenStreamServer({
      echoContentLocation: true,
    });
    let serverClosed = false;
    try {
      await page.route("**/api/langgraph/threads/*/runs/stream", (route) => {
        const threadId = runStreamThreadId(route);
        // 路径带上线程与 run id，服务端据此回显 Content-Location（SDK 的 onCreated）
        return route.continue({
          url: `${server.url}/threads/${threadId}/runs/run-held`,
        });
      });

      await page.goto("/workspace/chats/new");
      await composer(page).fill("受理后应当清空");
      await composer(page).press("Enter");

      // onCreated → 页面翻到新线程地址；流保持打开（没有 values / end 帧）
      await expect(page).toHaveURL(NEW_CHAT_URL, { timeout: 10_000 });
      await expect(composer(page)).toHaveValue("");
      await expect(stopIcon(page)).toBeVisible();

      // 流式期间写的新草稿不归这条已受理的提交管：流结束时不得再次清空输入框
      await composer(page).fill("流式期间的新草稿");
      server.writeFrame(sseFrame("values", { messages: [] }));
      server.writeFrame(sseFrame("end", {}));
      // SDK 以响应体结束为准收尾流：关掉连接
      await server.close();
      serverClosed = true;
      await expect(stopIcon(page)).toHaveCount(0, { timeout: 10_000 });
      await expect(composer(page)).toHaveValue("流式期间的新草稿");
      expect(pageErrors).toEqual([]);
    } finally {
      if (!serverClosed) await server.close();
    }
  });

  test("pre-creation failure toasts exactly once, keeps the draft and rolls back; retry succeeds", async ({
    page,
  }) => {
    mockLangGraphAPI(page);
    const pageErrors = trackPageErrors(page);
    // SDK 的 AsyncCaller 对 5xx 会退避重试；这里用不会被重试的 422，并在第一次
    // 失败被观察到之前持续失败，随后放行
    let failing = true;
    let failures = 0;
    await interceptThreadCreate(page, async (route, payload) => {
      if (failing) {
        failures += 1;
        return route.fulfill({
          status: 422,
          contentType: "application/json",
          body: JSON.stringify({ detail: "Thread create failed (mock)" }),
        });
      }
      return fulfillCreate(route, payload.thread_id ?? "fallback");
    });

    await page.goto("/workspace/chats/new");
    await composer(page).fill("第一次会失败的消息");
    await composer(page).press("Enter");

    await expect(page.getByText(/Thread create failed \(mock\)/)).toHaveCount(
      1,
      {
        timeout: 10_000,
      },
    );
    await expect(page).toHaveURL("/workspace/chats/new");
    await expect(composer(page)).toHaveValue("第一次会失败的消息");
    await expect(toasts(page)).toHaveCount(1);
    expect(failures).toBe(1);

    failing = false;
    await composer(page).press("Enter");
    await expect(page.getByText("Hello from DeerFlow!")).toBeVisible({
      timeout: 10_000,
    });
    await expect(page).toHaveURL(NEW_CHAT_URL);
    await expect(composer(page)).toHaveValue("");
    // 失败提示只有最初那一次
    await expect(page.getByText(/Thread create failed \(mock\)/)).toHaveCount(
      1,
    );
    expect(pageErrors).toEqual([]);
  });

  test("an attachment-only message is sent and a new chat's first message carries it", async ({
    page,
  }) => {
    const api = mockLangGraphAPI(page);
    const pageErrors = trackPageErrors(page);
    const uploads = await interceptUploads(page);

    await page.goto("/workspace/chats/new");
    await expect(composer(page)).toHaveValue("");
    await attachFile(page);
    await page.locator(SUBMIT).click();

    await expect(page.getByText("Hello from DeerFlow!")).toBeVisible({
      timeout: 10_000,
    });
    expect(uploads.requests).toHaveLength(1);
    expect(api.createdThreadIds[0]).toBeTruthy();
    // 上传用的就是页面预生成、随后被 create 回显的线程 id
    expect(uploads.requests[0]).toBe(api.createdThreadIds[0]);
    await expect(page).toHaveURL(`/workspace/chats/${api.createdThreadIds[0]}`);
    await expect(composer(page)).toHaveValue("");
    await expect(page.getByText("notes.txt")).toHaveCount(0);
    expect(pageErrors).toEqual([]);
  });

  test("switching threads mid-upload aborts the old upload and leaves the new thread clean", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      threads: [{ thread_id: THREAD_B, title: "Upload target thread" }],
    });
    const pageErrors = trackPageErrors(page);
    const uploads = await interceptUploads(page, { hold: true });
    const aborted: string[] = [];
    page.on("requestfailed", (request) => {
      if (request.url().includes("/uploads")) {
        aborted.push(request.failure()?.errorText ?? "failed");
      }
    });

    await page.goto("/workspace/chats/new");
    const targetThread = page.getByText("Upload target thread");
    await expect(targetThread).toBeVisible({ timeout: 15_000 });
    await attachFile(page);
    await page.locator(SUBMIT).click();
    await expect.poll(() => uploads.requests.length).toBe(1);
    // 上传挂着：onSend 要等上传完成才触发，地址栏此刻还在 /chats/new
    await expect(page).toHaveURL("/workspace/chats/new");

    await targetThread.click();
    await expect(page).toHaveURL(`/workspace/chats/${THREAD_B}`);
    await expect.poll(() => aborted.length, { timeout: 10_000 }).toBe(1);
    // B 的输入框干净：没有上传态、没有 A 的 optimistic 上传消息
    await expect(composer(page)).toHaveValue("");
    await expect(page.getByText(/Uploading/i)).toHaveCount(0);
    await expect(stopIcon(page)).toHaveCount(0);

    uploads.release();
    // B 正常发送不受影响：既有线程不再 create，直接起 run（响应 200 即受理）
    await composer(page).fill("B 线程的消息");
    const runStarted = page.waitForResponse((response) =>
      response.url().includes(`/api/langgraph/threads/${THREAD_B}/runs/stream`),
    );
    await composer(page).press("Enter");
    expect((await runStarted).status()).toBe(200);
    // 受理后输入已清空；B 没有再触发上传
    await expect(composer(page)).toHaveValue("");
    await expect(stopIcon(page)).toHaveCount(0, { timeout: 10_000 });
    await expect(page).toHaveURL(`/workspace/chats/${THREAD_B}`);
    expect(uploads.requests).toHaveLength(1);
    expect(pageErrors).toEqual([]);
  });
});
