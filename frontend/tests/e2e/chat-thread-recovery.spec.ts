/**
 * PR3（2026-11-24 前端可靠性批）：长 Agent 会话恢复的行为验收（mock API）。
 *
 * - 历史：真实分页（首页最新、before_seq 向前）、按 run 接续、同 run 在 onFinish 后重取
 * - 恢复：运行中刷新只 rejoin 一次、停止后同步为已取消、断线后手动重连、重开已中断的 run
 * - 滚动：首次水合落底、prepend 像素锚定（既有实现实测偏移 190.5px）、切线程重置、
 *   阈值两侧加载旧页都不跳底
 *
 * 不改 live 套件；全部走 page.route() mock。
 */

import { expect, test, type Page } from "@playwright/test";

import {
  startHeldOpenStreamServer,
  type HeldOpenStreamServer,
} from "./utils/held-open-stream";
import {
  MOCK_RUN_ID,
  MOCK_THREAD_ID,
  mockLangGraphAPI,
  runStreamThreadId,
} from "./utils/mock-api";

const THREAD_B = "00000000-0000-0000-0000-0000000000b2";
const COMPOSER = "[data-testid='composer-input']";
const SUBMIT = "button[aria-label='Submit']";

const composer = (page: Page) => page.locator(COMPOSER);
/** 流式进行中时提交按钮显示方块（停止）图标。 */
const stopIcon = (page: Page) => page.locator(`${SUBMIT} svg.lucide-square`);
const log = (page: Page) => page.getByRole("log");
/** StickToBottom 的滚动容器（log 的第一个子 div）。 */
const scroller = (page: Page) => log(page).locator(":scope > div").first();

function sseFrame(event: string, data: unknown) {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

function trackPageErrors(page: Page) {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  return errors;
}

type Msg = Record<string, unknown>;

const human = (id: string, text: string): Msg => ({
  type: "human",
  id,
  content: [{ type: "text", text }],
});
const ai = (id: string, text: string, extra: Msg = {}): Msg => ({
  type: "ai",
  id,
  content: text,
  ...extra,
});

/** n 轮问答，正文长度按 i 轮换（不同高度的组）。 */
function turns(prefix: string, count: number): Msg[] {
  const out: Msg[] = [];
  for (let i = 0; i < count; i += 1) {
    out.push(human(`${prefix}-q-${i}`, `${prefix} question ${i}`));
    const paragraphs = 1 + (i % 5);
    out.push(
      ai(
        `${prefix}-a-${i}`,
        Array.from(
          { length: paragraphs },
          (_, k) => `${prefix} answer ${i} paragraph ${k}`,
        ).join("\n\n"),
      ),
    );
  }
  return out;
}

/** 距底部的像素距离。 */
function distanceToBottom(page: Page): Promise<number> {
  return scroller(page).evaluate(
    (element) =>
      element.scrollHeight - element.scrollTop - element.clientHeight,
  );
}

async function scrollToTop(page: Page) {
  await scroller(page).evaluate((element) => {
    element.scrollTo({ top: 0, behavior: "instant" });
  });
}

/** 等首次定位的双帧结束：到底且稳定（紧跟着的 scrollTo 才不会被开场定位拉回去）。 */
async function waitSettledAtBottom(page: Page) {
  await expect
    .poll(() => distanceToBottom(page), { timeout: 15_000 })
    .toBeLessThanOrEqual(2);
  await page.waitForTimeout(300);
}

function runMessagesUrl(threadId: string, runId: string) {
  return `/api/threads/${threadId}/runs/${runId}/messages`;
}

/** 记录 run-messages 请求（按 run 与游标）。 */
function trackRunMessages(page: Page) {
  const calls: Array<{ runId: string; query: string }> = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    const match = /\/api\/threads\/[^/]+\/runs\/([^/?#]+)\/messages/.exec(
      url.pathname,
    );
    if (match) calls.push({ runId: match[1]!, query: url.search });
  });
  return calls;
}

/** 线程的 SDK state history：只有标题、没有消息（消息全部来自 run 历史）。 */
async function mockStateHistoryWithoutMessages(page: Page) {
  await page.route("**/api/langgraph/threads/*/history", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        {
          values: { title: "Recovery thread", messages: [] },
          next: [],
          metadata: {},
          created_at: "2025-01-01T00:00:00Z",
          parent_config: null,
        },
      ]),
    }),
  );
}

/** 把 join 请求桥接到保持连接的 SSE 服务（`/threads/{id}/runs/{run}`）。 */
/**
 * 把 join 请求桥接到保持连接的 SSE 服务（`/threads/{id}/runs/{run}`）。
 * SDK 的 join URL 带查询串（cancel_on_disconnect / stream_mode），glob 末尾要放开。
 */
const JOIN_URL_PATTERN = (runId: string) =>
  `**/api/langgraph/threads/*/runs/${runId}/stream**`;

async function bridgeJoins(
  page: Page,
  server: HeldOpenStreamServer,
  runId: string,
) {
  await page.route(JOIN_URL_PATTERN(runId), (route) =>
    route.continue({
      url: `${server.url}/threads/${runStreamThreadId(route)}/runs/${runId}`,
    }),
  );
}

/**
 * 扣住某个 run 的历史响应直到 release()：用例要在 prepend 发生之前量好锚点。
 * 释放后交还给共享 mock 的分页路由。
 */
function holdRunMessages(page: Page, runId: string) {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  void page.route(
    `**/api/threads/*/runs/${runId}/messages?*`,
    async (route) => {
      await gate;
      await route.fallback();
    },
  );
  return () => release();
}

/** 取消请求受理；返回收到的 cancel 请求数。 */
function mockCancel(page: Page, threadId: string, runId: string) {
  const calls: Array<{ wait: string | null }> = [];
  void page.route(
    `**/api/langgraph/threads/${threadId}/runs/${runId}/cancel**`,
    (route) => {
      const url = new URL(route.request().url());
      calls.push({
        wait:
          url.searchParams.get("wait") ??
          route
            .request()
            .postData()
            ?.match(/"wait":\s*(\w+)/)?.[1] ??
          null,
      });
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({}),
      });
    },
  );
  return calls;
}

test.describe("Chat thread recovery: history", () => {
  test("real pagination: the newest page first, older pages via before_seq, first prompt kept", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      runs: [{ run_id: "run-long", status: "success" }],
      runMessages: { "run-long": turns("long", 226) as never[] },
    });
    const calls = trackRunMessages(page);
    const pageErrors = trackPageErrors(page);

    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
    await expect(page.getByText("long answer 225 paragraph 0")).toBeVisible({
      timeout: 15_000,
    });

    expect(calls.filter((call) => call.runId === "run-long")).toEqual([
      { runId: "run-long", query: "?limit=200" },
      { runId: "run-long", query: "?limit=200&before_seq=253" },
      { runId: "run-long", query: "?limit=200&before_seq=53" },
    ]);
    // 452 条都加载了：等开场定位稳定后翻到顶，能看到第一条提示词
    await waitSettledAtBottom(page);
    await scrollToTop(page);
    await expect(page.getByText("long question 0")).toBeInViewport();
    await expect(page.getByRole("button", { name: "加载更多" })).toHaveCount(0);
    expect(pageErrors).toEqual([]);
  });

  test("a new run continues after the older one once it is loaded", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      runs: [
        { run_id: "run-2", status: "success" },
        { run_id: "run-1", status: "success" },
      ],
      runMessages: {
        "run-1": [human("q1", "older question"), ai("a1", "older answer")],
        "run-2": [human("q2", "latest question"), ai("a2", "latest answer")],
      } as never,
    });

    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
    await expect(page.getByText("latest answer")).toBeVisible({
      timeout: 15_000,
    });
    // 短线程里「加载更多」的哨兵一开始就在视口附近：旧 run 自动接续加载
    await expect(page.getByText("older answer")).toBeVisible({
      timeout: 15_000,
    });
    const row = (index: number) =>
      page.locator(`[data-message-group-index="${index}"]`);
    await expect(row(0)).toContainText("older question");
    await expect(row(1)).toContainText("older answer");
    await expect(row(2)).toContainText("latest question");
    await expect(row(3)).toContainText("latest answer");
  });

  test("onFinish refetches the finished run so the streamed turn is in history", async ({
    page,
  }) => {
    mockLangGraphAPI(page);
    await mockStateHistoryWithoutMessages(page);
    let sent = false;
    await page.route(/\/api\/langgraph\/threads\/[^/]+\/runs(\?|$)/, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(
          sent
            ? [
                { run_id: MOCK_RUN_ID, status: "success" },
                { run_id: "run-1", status: "success" },
              ]
            : [{ run_id: "run-1", status: "success" }],
        ),
      }),
    );
    await page.route(
      /\/api\/threads\/[^/]+\/runs\/[^/?#]+\/messages\?/,
      (route) => {
        const runId = /\/runs\/([^/?#]+)\/messages/.exec(
          new URL(route.request().url()).pathname,
        )?.[1];
        const data =
          runId === MOCK_RUN_ID
            ? [
                human("live-q", "hello there"),
                ai("live-a", "Hello from DeerFlow!"),
              ]
            : [human("h1", "earlier question"), ai("a1", "earlier answer")];
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: data.map((content, index) => ({
              run_id: runId,
              seq: index + 1,
              content,
              metadata: { caller: "user" },
              created_at: "2025-01-01T00:00:00Z",
            })),
            has_more: false,
          }),
        });
      },
    );
    const calls = trackRunMessages(page);
    const pageErrors = trackPageErrors(page);

    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
    await expect(page.getByText("earlier answer")).toBeVisible({
      timeout: 15_000,
    });
    sent = true;
    await composer(page).fill("hello there");
    await composer(page).press("Enter");

    // 流结束 → onFinish 按 meta.run_id 重取：新 run 的历史端点被请求一次
    await expect
      .poll(() => calls.filter((call) => call.runId === MOCK_RUN_ID).length)
      .toBe(1);
    await expect(page.getByText("Hello from DeerFlow!")).toBeVisible();
    expect(pageErrors).toEqual([]);
  });
});

test.describe("Chat thread recovery: reconnect, stop and interrupted runs", () => {
  const RUNNING_HISTORY = [
    human("q1", "please research this"),
    ai("task-call", "", {
      tool_calls: [
        {
          id: "call-task-1",
          name: "task",
          args: {
            subagent_type: "general-purpose",
            description: "Research the topic",
            prompt: "Find sources",
          },
        },
      ],
    }),
  ];

  /** 挂载即自动 rejoin：reconnect key + running 的 run + 保持连接的 join 流。 */
  async function mountRejoining(page: Page, server: HeldOpenStreamServer) {
    await page.addInitScript((key) => {
      window.sessionStorage.setItem(key, "run-live");
    }, `lg:stream:${MOCK_THREAD_ID}`);
    let stopped = false;
    await page.route(/\/api\/langgraph\/threads\/[^/]+\/runs(\?|$)/, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          { run_id: "run-live", status: stopped ? "interrupted" : "running" },
        ]),
      }),
    );
    await page.route(
      /\/api\/threads\/[^/]+\/runs\/[^/?#]+\/messages\?/,
      (route) =>
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: RUNNING_HISTORY.map((content, index) => ({
              run_id: "run-live",
              seq: index + 1,
              content,
              metadata: { caller: "user" },
              created_at: "2025-01-01T00:00:00Z",
            })),
            has_more: false,
          }),
        }),
    );
    const joins = trackJoins(page);
    await bridgeJoins(page, server, "run-live");
    return {
      joins,
      markStopped: () => {
        stopped = true;
      },
    };
  }

  function trackJoins(page: Page) {
    const urls: string[] = [];
    page.on("request", (request) => {
      if (request.url().includes("/runs/run-live/stream")) {
        urls.push(request.url());
      }
    });
    return urls;
  }

  test("refresh while running: the SDK rejoins once and the stream is live", async ({
    page,
  }) => {
    mockLangGraphAPI(page);
    await mockStateHistoryWithoutMessages(page);
    const server = await startHeldOpenStreamServer();
    const pageErrors = trackPageErrors(page);
    try {
      const { joins } = await mountRejoining(page, server);
      await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
      await expect(stopIcon(page)).toBeVisible({ timeout: 15_000 });
      await expect(page.getByText("Research the topic")).toBeVisible();

      // 只 join 一次；流保持打开期间没有恢复入口（不是断线）
      await page.waitForTimeout(500);
      expect(joins).toHaveLength(1);
      await expect(page.getByTestId("recovery-banner")).toHaveCount(0);
      expect(pageErrors).toEqual([]);
    } finally {
      await server.close();
    }
  });

  test("stop after a rejoin: the server run becomes interrupted and the task shows cancelled", async ({
    page,
  }) => {
    mockLangGraphAPI(page);
    await mockStateHistoryWithoutMessages(page);
    const server = await startHeldOpenStreamServer();
    const cancels = mockCancel(page, MOCK_THREAD_ID, "run-live");
    const pageErrors = trackPageErrors(page);
    try {
      const { markStopped } = await mountRejoining(page, server);
      await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
      await expect(stopIcon(page)).toBeVisible({ timeout: 15_000 });

      markStopped();
      await stopIcon(page).click();

      // 显式取消 wait=true；取消受理后有界刷新 → run 的终态是 interrupted
      await expect.poll(() => cancels.length).toBeGreaterThan(0);
      // SDK 把 wait 作为查询参数发出（1 = 等服务端终态）
      expect(cancels.some((call) => call.wait === "1")).toBe(true);
      await expect(page.getByText("子任务已取消")).toBeVisible({
        timeout: 10_000,
      });
      await expect(page.getByText("子任务失败")).toHaveCount(0);
      await expect(page.getByTestId("recovery-banner")).toHaveCount(0);
      await expect(stopIcon(page)).toHaveCount(0);
      expect(pageErrors).toEqual([]);
    } finally {
      await server.close();
    }
  });

  test("connection drop: a reconnect entry appears, reconnect joins again and recovers the turn", async ({
    page,
  }) => {
    mockLangGraphAPI(page);
    await mockStateHistoryWithoutMessages(page);
    const first = await startHeldOpenStreamServer();
    const second = await startHeldOpenStreamServer();
    const pageErrors = trackPageErrors(page);
    try {
      const { joins } = await mountRejoining(page, first);
      await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
      await expect(stopIcon(page)).toBeVisible({ timeout: 15_000 });
      expect(joins).toHaveLength(1);

      // 流以连接类错误收尾：run 仍在服务端运行 → 给重连入口
      first.writeFrame(sseFrame("error", { message: "Failed to fetch" }));
      await first.close();
      const banner = page.getByTestId("recovery-banner");
      await expect(banner).toContainText("连接已中断，任务可能仍在运行", {
        timeout: 10_000,
      });
      // 不自动重连
      await page.waitForTimeout(500);
      expect(joins).toHaveLength(1);

      // 用户点「重新连接」：再 join 一次；这次流正常收尾 → 入口收起、补上尾段
      let done = false;
      await page.route(
        /\/api\/langgraph\/threads\/[^/]+\/runs(\?|$)/,
        (route) =>
          route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify([
              { run_id: "run-live", status: done ? "success" : "running" },
            ]),
          }),
      );
      await page.route(
        /\/api\/threads\/[^/]+\/runs\/[^/?#]+\/messages\?/,
        (route) =>
          route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
              data: [
                ...RUNNING_HISTORY,
                ai("tail", "Research finished after reconnecting"),
              ].map((content, index) => ({
                run_id: "run-live",
                seq: index + 1,
                content,
                metadata: { caller: "user" },
                created_at: "2025-01-01T00:00:00Z",
              })),
              has_more: false,
            }),
          }),
      );
      await page.route("**/api/langgraph/threads/*/history", (route) =>
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify([
            {
              values: { title: "Recovery thread", messages: RUNNING_HISTORY },
              next: [],
              metadata: {},
              created_at: "2025-01-01T00:00:00Z",
              parent_config: null,
            },
          ]),
        }),
      );
      await page.unroute(JOIN_URL_PATTERN("run-live"));
      await bridgeJoins(page, second, "run-live");
      await banner.getByRole("button", { name: "重新连接" }).click();
      await expect.poll(() => joins.length).toBe(2);
      done = true;
      second.writeFrame(
        sseFrame("values", {
          messages: [
            ...RUNNING_HISTORY,
            ai("tail", "Research finished after reconnecting"),
          ],
        }),
      );
      second.writeFrame(sseFrame("end", {}));
      await second.close();

      await expect(
        page.getByText("Research finished after reconnecting"),
      ).toBeVisible({
        timeout: 10_000,
      });
      await expect(banner).toHaveCount(0);
      await expect(stopIcon(page)).toHaveCount(0);
      expect(pageErrors).toEqual([]);
    } finally {
      await first.close().catch(() => undefined);
      await second.close().catch(() => undefined);
    }
  });

  test("reopening an interrupted run shows its task as cancelled, with nothing running", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      runs: [{ run_id: "run-1", status: "interrupted" }],
      runMessages: { "run-1": RUNNING_HISTORY as never[] },
    });
    await mockStateHistoryWithoutMessages(page);
    const joins = trackJoins(page);

    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
    await expect(page.getByText("Research the topic")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByText("子任务已取消")).toBeVisible();
    await expect(page.getByText("子任务失败")).toHaveCount(0);
    // 没有在途的东西：不转圈、不重连、可以继续发
    await expect(stopIcon(page)).toHaveCount(0);
    await expect(page.getByTestId("recovery-banner")).toHaveCount(0);
    expect(joins).toHaveLength(0);
  });
});
test.describe("Chat thread recovery: scroll anchoring", () => {
  test("a long thread hydrates at the bottom (within 2px)", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      runs: [{ run_id: "run-latest", status: "success" }],
      runMessages: { "run-latest": turns("latest", 40) as never[] },
    });

    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
    await expect(page.getByText("latest answer 39 paragraph 0")).toBeInViewport(
      { timeout: 15_000 },
    );
    await expect.poll(() => distanceToBottom(page)).toBeLessThanOrEqual(2);
  });

  test("prepending an older run keeps the first visible group in place (within 4px)", async ({
    page,
  }) => {
    // loadMore 从最旧的未加载 run 开始：先扣住 run-older；run-mid 留着，让「加载更多」
    // 的入口不会随这次 prepend 一起消失（它的高度会把后面的组整体上移，与锚定无关）
    mockLangGraphAPI(page, {
      runs: [
        { run_id: "run-latest", status: "success" },
        { run_id: "run-mid", status: "success" },
        { run_id: "run-older", status: "success" },
      ],
      runMessages: {
        "run-latest": turns("latest", 40) as never[],
        "run-mid": turns("mid", 3) as never[],
        "run-older": turns("older", 35) as never[],
      },
    });

    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
    await expect(page.getByText("latest answer 39 paragraph 0")).toBeInViewport(
      { timeout: 15_000 },
    );
    await waitSettledAtBottom(page);
    // 翻到顶时「加载更多」的哨兵进入视口，旧 run 的加载会自动触发：先扣住响应，
    // 量好锚点再放行
    const release = holdRunMessages(page, "run-older");
    await scrollToTop(page);
    const firstBefore = page.getByText("latest question 0", { exact: true });
    await expect(firstBefore).toBeInViewport();
    const topBefore = await firstBefore.evaluate(
      (element) => element.getBoundingClientRect().top,
    );

    const olderResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "GET" &&
        new URL(response.url()).pathname ===
          runMessagesUrl(MOCK_THREAD_ID, "run-older"),
    );
    release();
    expect((await olderResponse).status()).toBe(200);

    // 新插入的 70 行先按估计高度对齐、实测后逐批校正：原来可见的第一组留在原处
    await expect(firstBefore).toBeInViewport({ timeout: 10_000 });
    await expect
      .poll(async () =>
        Math.abs(
          (await firstBefore.evaluate(
            (element) => element.getBoundingClientRect().top,
          )) - topBefore,
        ),
      )
      .toBeLessThanOrEqual(4);
  });

  test("switching from a scrolled-up thread to another one lands at the bottom", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      threads: [
        { thread_id: MOCK_THREAD_ID, title: "Thread alpha" },
        { thread_id: THREAD_B, title: "Thread beta" },
      ],
      runs: [{ run_id: "run-1", status: "success" }],
    });
    const byThread: Record<string, Msg[]> = {
      [MOCK_THREAD_ID]: turns("alpha", 40),
      [THREAD_B]: turns("beta", 40),
    };
    await page.route(
      /\/api\/threads\/[^/]+\/runs\/[^/?#]+\/messages\?/,
      (route) => {
        const threadId = /\/threads\/([^/?#]+)\//.exec(
          new URL(route.request().url()).pathname,
        )?.[1];
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: (byThread[threadId ?? ""] ?? []).map((content, index) => ({
              run_id: "run-1",
              seq: index + 1,
              content,
              metadata: { caller: "user" },
              created_at: "2025-01-01T00:00:00Z",
            })),
            has_more: false,
          }),
        });
      },
    );
    await page.route("**/api/langgraph/threads/*/history", (route) => {
      const url = route.request().url();
      const title = url.includes(THREAD_B) ? "Thread beta" : "Thread alpha";
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            values: { title, messages: [] },
            next: [],
            metadata: {},
            created_at: "2025-01-01T00:00:00Z",
            parent_config: null,
          },
        ]),
      });
    });

    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
    await expect(page.getByText("alpha answer 39 paragraph 0")).toBeInViewport({
      timeout: 15_000,
    });
    await waitSettledAtBottom(page);
    await scrollToTop(page);
    await expect(page.getByText("alpha question 0")).toBeInViewport();

    // 应用内切线程（侧栏）：新线程仍首次落底
    await page.getByText("Thread beta", { exact: true }).first().click();
    await expect(page).toHaveURL(new RegExp(THREAD_B));
    await expect(page.getByText("beta answer 39 paragraph 0")).toBeInViewport({
      timeout: 15_000,
    });
    await expect.poll(() => distanceToBottom(page)).toBeLessThanOrEqual(2);
    await expect(page.getByText("alpha answer 39 paragraph 0")).toHaveCount(0);
  });

  // 静态（<60 组）与虚拟（≥60 组）两侧都验；20+20 这一档在 prepend 后跨过阈值
  // （列表由静态切到虚拟），静态锚点要交给虚拟分支继续校正——两侧都保留阅读位置
  for (const scenario of [
    { latest: 15, older: 10 },
    { latest: 70, older: 70 },
    { latest: 20, older: 20 },
  ] as const) {
    test(`loading an older page in a ${scenario.latest}-turn thread does not jump to the bottom`, async ({
      page,
    }) => {
      mockLangGraphAPI(page, {
        runs: [
          { run_id: "run-latest", status: "success" },
          { run_id: "run-older", status: "success" },
        ],
        runMessages: {
          "run-latest": turns("latest", scenario.latest) as never[],
          "run-older": turns("older", scenario.older) as never[],
        },
      });

      await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
      await expect(
        page.getByText(`latest answer ${scenario.latest - 1} paragraph 0`),
      ).toBeInViewport({ timeout: 15_000 });
      await waitSettledAtBottom(page);
      const release = holdRunMessages(page, "run-older");
      await scrollToTop(page);
      const firstBefore = page.getByText("latest question 0", {
        exact: true,
      });
      await expect(firstBefore).toBeInViewport();

      const olderResponse = page.waitForResponse(
        (response) =>
          response.request().method() === "GET" &&
          new URL(response.url()).pathname ===
            runMessagesUrl(MOCK_THREAD_ID, "run-older"),
      );
      release();
      expect((await olderResponse).status()).toBe(200);

      // 旧页插入在上方：视口停在原来的位置，不跳到底
      await expect(page.getByText("older question 0")).toBeAttached();
      await expect(firstBefore).toBeInViewport({ timeout: 10_000 });
      await expect.poll(() => distanceToBottom(page)).toBeGreaterThan(200);
    });
  }
});
