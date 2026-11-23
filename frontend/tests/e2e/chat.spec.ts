import { expect, test, type Locator, type Page } from "@playwright/test";

import { startHeldOpenStreamServer } from "./utils/held-open-stream";
import { pinEnglishLocale } from "./utils/locale";
import {
  createTokenUsageRunStreamHandler,
  handleRunStream,
  MOCK_THREAD_ID,
  mockLangGraphAPI,
} from "./utils/mock-api";

test.describe("Chat workspace", () => {
  test.beforeEach(async ({ page }) => {
    await pinEnglishLocale(page);
    mockLangGraphAPI(page);
  });

  test("new chat page loads with input box", async ({ page }) => {
    await page.goto("/workspace/chats/new");

    const textarea = page.getByPlaceholder(/how can i assist you/i);
    await expect(textarea).toBeVisible({ timeout: 15_000 });
  });

  test("can type a message in the input box", async ({ page }) => {
    await page.goto("/workspace/chats/new");

    const textarea = page.getByPlaceholder(/how can i assist you/i);
    await expect(textarea).toBeVisible({ timeout: 15_000 });

    await textarea.fill("Hello, DeerFlow!");
    await expect(textarea).toHaveValue("Hello, DeerFlow!");
  });

  test("sending a message triggers API call and shows response", async ({
    page,
  }) => {
    let streamCalled = false;
    await page.route("**/runs/stream", (route) => {
      streamCalled = true;
      return handleRunStream(route);
    });

    await page.goto("/workspace/chats/new");

    const textarea = page.getByPlaceholder(/how can i assist you/i);
    await expect(textarea).toBeVisible({ timeout: 15_000 });

    await textarea.fill("Hello");
    await textarea.press("Enter");

    await expect.poll(() => streamCalled, { timeout: 10_000 }).toBeTruthy();

    // The AI response should appear in the chat
    await expect(page.getByText("Hello from DeerFlow!")).toBeVisible({
      timeout: 10_000,
    });
  });

  test("first reply reaches ready without requesting follow-up suggestions", async ({
    page,
  }) => {
    const suggestionPosts: string[] = [];
    const galleryCategoryRequests: string[] = [];
    await page.route("**/api/gallery/categories", (route) => {
      galleryCategoryRequests.push(route.request().url());
      return route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Unauthorized" }),
      });
    });
    await page.route("**/api/threads/*/suggestions", (route) => {
      if (route.request().method() === "POST") {
        suggestionPosts.push(route.request().url());
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ suggestions: ["Hidden follow-up"] }),
      });
    });

    await page.goto("/workspace/chats/new");
    const textarea = page.getByPlaceholder(/how can i assist you/i);
    await expect(textarea).toBeVisible({ timeout: 15_000 });
    await textarea.fill("Hello");
    await textarea.press("Enter");

    await expect(page.getByText("Hello from DeerFlow!")).toBeVisible({
      timeout: 10_000,
    });
    await expect(page).toHaveURL(new RegExp(`${MOCK_THREAD_ID}$`));
    await expect(
      page.locator("button[aria-label='Submit'] svg").first(),
    ).toHaveClass(/lucide-arrow-up/, { timeout: 10_000 });
    expect(suggestionPosts).toEqual([]);
    expect(galleryCategoryRequests).toEqual([]);
    await expect(page.getByTitle("分享到灵感库")).toHaveCount(0);
    await expect(page.getByText("Hidden follow-up")).toHaveCount(0);
  });
});

type TokenTestMessage = Record<string, unknown>;

type TokenUsageFixture = {
  input: number;
  output: number;
  total: number;
};

function tokenUsage(input: number, output: number, total: number) {
  return { input, output, total };
}

function humanMessage(id: string, text: string): TokenTestMessage {
  return {
    type: "human",
    id,
    content: [{ type: "text", text }],
  };
}

function aiMessage(
  id: string,
  content: string,
  usage?: TokenUsageFixture,
  toolCalls: TokenTestMessage[] = [],
): TokenTestMessage {
  return {
    type: "ai",
    id,
    content,
    ...(toolCalls.length > 0 ? { tool_calls: toolCalls } : {}),
    ...(usage
      ? {
          usage_metadata: {
            input_tokens: usage.input,
            output_tokens: usage.output,
            total_tokens: usage.total,
          },
        }
      : {}),
  };
}

function toolCall(
  id: string,
  name: string,
  args: TokenTestMessage = {},
): TokenTestMessage {
  return { id, name, args };
}

function toolResult(
  id: string,
  toolCallId: string,
  name = "search",
): TokenTestMessage {
  return {
    type: "tool",
    id,
    tool_call_id: toolCallId,
    name,
    content: "Tool result",
  };
}

function buildThirtyTwoTokenTurns() {
  return Array.from({ length: 32 }, (_, index): TokenTestMessage[] => {
    const turn = index + 1;
    return [
      humanMessage(`token-long-human-${turn}`, `Long question ${turn}`),
      aiMessage(`token-long-processing-${turn}`, "", tokenUsage(1, 2, 3), [
        toolCall(`token-long-call-${turn}`, "search"),
      ]),
      toolResult(`token-long-tool-${turn}`, `token-long-call-${turn}`),
      aiMessage(
        `token-long-answer-${turn}`,
        `Long answer ${turn}`,
        tokenUsage(4, 5, 9),
      ),
    ];
  });
}

function messageGroups(page: Page) {
  return page.locator("[data-message-group-index]");
}

function tokenUsageRows(page: Page) {
  return messageGroups(page).getByTestId("turn-token-usage");
}

function tokenUnavailableMarkers(page: Page) {
  return messageGroups(page).getByText(/^No usage returned/);
}

async function openNewTokenChat(page: Page) {
  await page.goto("/workspace/chats/new");
  const textarea = page.getByPlaceholder(/how can i assist you/i);
  await expect(textarea).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("button", { name: /^Tokens/ })).toBeVisible();
}

async function sendMessage(page: Page, text: string) {
  const textarea = page.getByPlaceholder(/how can i assist you/i);
  await textarea.fill(text);
  await textarea.press("Enter");
}

async function waitForReady(page: Page) {
  await expect(
    page.locator("button[aria-label='Submit'] svg").first(),
  ).toHaveClass(/lucide-arrow-up/, { timeout: 10_000 });
}

async function expectSummaryBelowToolbar(group: Locator) {
  const summary = group.getByTestId("turn-token-usage");
  await expect(summary).toHaveCount(1);
  const toolbar = group
    .locator("button:has(svg.lucide-copy)")
    .locator("..")
    .locator("..");
  const summaryBox = await summary.boundingBox();
  const toolbarBox = await toolbar.boundingBox();
  expect(summaryBox).not.toBeNull();
  expect(toolbarBox).not.toBeNull();
  expect(summaryBox!.y).toBeGreaterThanOrEqual(
    toolbarBox!.y + toolbarBox!.height,
  );
}

function sseFrame(event: string, data: unknown) {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

test.describe("Chat token usage by turn", () => {
  test.beforeEach(async ({ page }) => {
    await pinEnglishLocale(page);
    mockLangGraphAPI(page, { tokenUsageEnabled: true });
  });

  test("token summary conserves processing, present-files and final usage below the toolbar", async ({
    page,
  }) => {
    await page.route(
      "**/runs/stream",
      createTokenUsageRunStreamHandler([
        [
          humanMessage("token-main-human", "Summarize one turn"),
          aiMessage("token-main-ai-1", "", tokenUsage(100, 20, 120), [
            toolCall("token-main-call-1", "search"),
          ]),
          toolResult("token-main-tool-1", "token-main-call-1"),
          aiMessage("token-main-ai-2", "", tokenUsage(200, 30, 230), [
            toolCall("token-main-call-2", "search"),
          ]),
          toolResult("token-main-tool-2", "token-main-call-2"),
          aiMessage("token-main-ai-3", "", tokenUsage(300, 40, 340), [
            toolCall("token-main-present", "present_files", {
              filepaths: ["report.md"],
            }),
          ]),
          aiMessage(
            "token-main-final",
            "Final answer",
            tokenUsage(400, 50, 450),
          ),
          {
            type: "tool",
            id: "token-main-empty-clarification",
            tool_call_id: "token-main-empty-clarification-call",
            name: "ask_clarification",
            content: "",
          },
        ],
      ]),
    );

    await openNewTokenChat(page);
    await sendMessage(page, "Summarize one turn");
    await expect(page.getByText("Final answer", { exact: true })).toBeVisible();
    await expect(page.getByText("report.md", { exact: true })).toBeVisible();
    await waitForReady(page);

    await expect(tokenUsageRows(page)).toHaveCount(1);
    const summary = tokenUsageRows(page);
    await expect(summary).toContainText("Input: 1,000");
    await expect(summary).toContainText("Output: 140");
    await expect(summary).toContainText("Total: 1,140");
    await expect(page.getByRole("button", { name: /^Tokens/ })).toContainText(
      "987.7K",
    );

    const finalGroup = page.locator('[data-message-group-index="3"]');
    await expectSummaryBelowToolbar(finalGroup);
  });

  test("token rows stay hidden on a held-open stream and do not duplicate after stop", async ({
    page,
  }) => {
    const server = await startHeldOpenStreamServer();
    try {
      await page.route("**/runs/stream", async (route) => {
        await route.continue({ url: server.url });
        await new Promise((resolve) => setTimeout(resolve, 100));
        server.writeFrame(
          sseFrame("metadata", {
            run_id: "token-held-open-run",
            thread_id: MOCK_THREAD_ID,
          }),
        );
        server.writeFrame(
          sseFrame("values", {
            messages: [
              humanMessage("token-held-human", "Held-open token turn"),
              aiMessage("token-held-processing", "", tokenUsage(1, 2, 3), [
                toolCall("token-held-call", "search"),
              ]),
              toolResult("token-held-tool", "token-held-call"),
              aiMessage(
                "token-held-final",
                "Held-open answer",
                tokenUsage(4, 5, 9),
              ),
            ],
          }),
        );
      });
      await page.route("**/runs/*/cancel", (route) =>
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: "{}",
        }),
      );
      await openNewTokenChat(page);
      await sendMessage(page, "Held-open token turn");

      await expect(
        page.getByText("Held-open answer", { exact: true }),
      ).toBeVisible();
      await expect(
        page.locator("button[aria-label='Submit'] svg").first(),
      ).toHaveClass(/lucide-square/);
      await expect(tokenUsageRows(page)).toHaveCount(0);

      await page.locator("button[aria-label='Submit']").click();
      await waitForReady(page);
      await expect(tokenUsageRows(page)).toHaveCount(1);
      await expect(tokenUsageRows(page)).toContainText("Total: 12");
    } finally {
      await server.close();
    }
  });

  test("token summaries remain one per turn across two cumulative runs", async ({
    page,
  }) => {
    await page.route(
      "**/runs/stream",
      createTokenUsageRunStreamHandler([
        [
          humanMessage("token-two-human-1", "First token turn"),
          aiMessage("token-two-processing-1", "", tokenUsage(1, 2, 3), [
            toolCall("token-two-call-1", "search"),
          ]),
          toolResult("token-two-tool-1", "token-two-call-1"),
          aiMessage(
            "token-two-final-1",
            "First token answer",
            tokenUsage(4, 5, 9),
          ),
        ],
        [
          humanMessage("token-two-human-2", "Second token turn"),
          aiMessage(
            "token-two-final-2",
            "Second token answer",
            tokenUsage(10, 20, 30),
          ),
        ],
      ]),
    );

    await openNewTokenChat(page);
    await sendMessage(page, "First token turn");
    await expect(
      page.getByText("First token answer", { exact: true }),
    ).toBeVisible();
    await waitForReady(page);
    await expect(tokenUsageRows(page)).toHaveCount(1);
    await expect(tokenUsageRows(page)).toContainText("Total: 12");

    await sendMessage(page, "Second token turn");
    await expect(
      page.getByText("Second token answer", { exact: true }),
    ).toBeVisible();
    await waitForReady(page);
    await expect(tokenUsageRows(page)).toHaveCount(2);
    await expect(tokenUsageRows(page).nth(0)).toContainText("Input: 5");
    await expect(tokenUsageRows(page).nth(0)).toContainText("Output: 7");
    await expect(tokenUsageRows(page).nth(1)).toContainText("Total: 30");
  });

  test("token summary reports the number of messages missing usage", async ({
    page,
  }) => {
    await page.route(
      "**/runs/stream",
      createTokenUsageRunStreamHandler([
        [
          humanMessage("token-missing-human", "Missing usage turn"),
          aiMessage(
            "token-missing-ai-1",
            "Missing usage answer 1",
            tokenUsage(5, 7, 12),
          ),
          aiMessage("token-missing-ai-2", "Missing usage answer 2"),
          aiMessage("token-missing-ai-3", "Missing usage answer 3"),
        ],
      ]),
    );

    await openNewTokenChat(page);
    await sendMessage(page, "Missing usage turn");
    await expect(
      page.getByText("Missing usage answer 3", { exact: true }),
    ).toBeVisible();
    await waitForReady(page);

    await expect(tokenUnavailableMarkers(page)).toHaveCount(1);
    await expect(tokenUnavailableMarkers(page)).toHaveText(
      "No usage returned ×2",
    );
    await expect(tokenUsageRows(page)).toHaveCount(1);
    const summary = tokenUsageRows(page);
    await expect(summary).toContainText("Input: 5");
    await expect(summary).toContainText("Output: 7");
    await expect(summary).toContainText("Total: 12");
  });

  test("token summary counts distinct messages with the same id across multiple artifact groups", async ({
    page,
  }) => {
    await page.route(
      "**/runs/stream",
      createTokenUsageRunStreamHandler([
        [
          humanMessage("token-shared-human", "Shared id turn"),
          aiMessage("token-shared-ai", "", tokenUsage(2, 3, 5), [
            toolCall("token-shared-search", "search"),
          ]),
          toolResult("token-shared-tool", "token-shared-search"),
          aiMessage("token-shared-ai", "", tokenUsage(7, 11, 18), [
            toolCall("token-shared-present-1", "present_files", {
              filepaths: ["first.md"],
            }),
          ]),
          aiMessage("token-shared-ai", "", tokenUsage(13, 17, 30), [
            toolCall("token-shared-present-2", "present_files", {
              filepaths: ["second.md"],
            }),
          ]),
          aiMessage(
            "token-shared-final",
            "Shared id answer",
            tokenUsage(19, 23, 42),
          ),
        ],
      ]),
    );

    await openNewTokenChat(page);
    await sendMessage(page, "Shared id turn");
    await expect(
      page.getByText("Shared id answer", { exact: true }),
    ).toBeVisible();
    await expect(page.getByText("first.md", { exact: true })).toBeVisible();
    await expect(page.getByText("second.md", { exact: true })).toBeVisible();
    await waitForReady(page);

    await expect(tokenUsageRows(page)).toHaveCount(1);
    const summary = tokenUsageRows(page);
    await expect(summary).toContainText("Input: 41");
    await expect(summary).toContainText("Output: 54");
    await expect(summary).toContainText("Total: 95");
  });

  test("token summary keeps completed messages unique after an error event", async ({
    page,
  }) => {
    await page.route(
      "**/runs/stream",
      createTokenUsageRunStreamHandler(
        [
          [
            humanMessage("token-error-human", "Error token turn"),
            aiMessage(
              "token-error-ai-1",
              "Error answer 1",
              tokenUsage(1, 2, 3),
            ),
            aiMessage(
              "token-error-ai-2",
              "Error answer 2",
              tokenUsage(4, 5, 9),
            ),
          ],
        ],
        { terminalEvent: "error" },
      ),
    );

    await openNewTokenChat(page);
    await sendMessage(page, "Error token turn");
    await expect(
      page.getByText("Error answer 2", { exact: true }),
    ).toBeVisible();
    await expect(
      page.locator("button[aria-label='Submit'] svg").first(),
    ).toHaveClass(/lucide-x/, { timeout: 10_000 });
    await expect(tokenUsageRows(page)).toHaveCount(1);
    await expect(tokenUsageRows(page)).toContainText("Total: 12");
  });

  test("token summaries use one row per turn in the 96-group virtual window", async ({
    page,
  }) => {
    const turns = buildThirtyTwoTokenTurns();
    await page.route(
      "**/runs/stream",
      createTokenUsageRunStreamHandler([turns.flat()]),
    );

    await openNewTokenChat(page);
    await sendMessage(page, "Long question 32");
    await expect(
      page.getByText("Long answer 32", { exact: true }),
    ).toBeVisible();
    await waitForReady(page);

    expect(await messageGroups(page).count()).toBeLessThan(96);
    const lastTurnRows = page.locator(
      '[data-message-group-index="94"], [data-message-group-index="95"]',
    );
    await expect(lastTurnRows.getByTestId("turn-token-usage")).toHaveCount(1);
    await expect(lastTurnRows.getByTestId("turn-token-usage")).toContainText(
      "Total: 12",
    );

    const perTurnCounts = await messageGroups(page).evaluateAll((rows) => {
      const counts = new Map<number, number>();
      for (const row of rows) {
        const groupIndex = Number(row.getAttribute("data-message-group-index"));
        const turn = Math.floor(groupIndex / 3);
        const usageRows = row.querySelectorAll(
          '[data-testid="turn-token-usage"]',
        ).length;
        counts.set(turn, (counts.get(turn) ?? 0) + usageRows);
      }
      return Array.from(counts.values());
    });
    expect(perTurnCounts.length).toBeGreaterThan(0);
    expect(perTurnCounts.every((count) => count <= 1)).toBe(true);
    await expectSummaryBelowToolbar(
      page.locator('[data-message-group-index="95"]'),
    );
  });

  test("token summaries preserve shifted group indices and per-turn totals after real run-history prepend", async ({
    page,
  }) => {
    const latestMessages = buildThirtyTwoTokenTurns().flat();
    const olderMessages = [
      humanMessage("token-older-human", "Older question"),
      aiMessage("token-older-processing", "", tokenUsage(20, 30, 50), [
        toolCall("token-older-call", "search"),
      ]),
      toolResult("token-older-tool", "token-older-call"),
      aiMessage("token-older-final", "Older answer", tokenUsage(40, 50, 90)),
    ];
    const runs = [
      {
        run_id: "token-history-older-run",
        thread_id: MOCK_THREAD_ID,
        assistant_id: "lead_agent",
        created_at: "2025-01-01T00:00:00Z",
        updated_at: "2025-01-01T00:01:00Z",
        status: "success",
        metadata: {},
        multitask_strategy: "reject",
      },
      {
        run_id: "token-history-latest-run",
        thread_id: MOCK_THREAD_ID,
        assistant_id: "lead_agent",
        created_at: "2025-01-02T00:00:00Z",
        updated_at: "2025-01-02T00:01:00Z",
        status: "success",
        metadata: {},
        multitask_strategy: "reject",
      },
    ];
    await page.route(/\/api\/langgraph\/threads\/[^/]+\/runs(\?|$)/, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(runs),
      }),
    );

    await page.route(
      /\/api\/threads\/[^/]+\/runs\/[^/]+\/messages\?/,
      async (route) => {
        const isOlder = route
          .request()
          .url()
          .includes("token-history-older-run");
        const messages = isOlder ? olderMessages : latestMessages;
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: messages.map((content, index) => ({
              run_id: isOlder
                ? "token-history-older-run"
                : "token-history-latest-run",
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

    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
    await expect(page.getByText("Long answer 32", { exact: true })).toBeVisible(
      {
        timeout: 15_000,
      },
    );
    await page.getByRole("button", { name: "Load more" }).click();
    await expect(page.getByText("Older answer", { exact: true })).toBeVisible();

    const latestFirstTurn = page.locator(
      '[data-message-group-index="4"], [data-message-group-index="5"]',
    );
    await expect(latestFirstTurn.getByTestId("turn-token-usage")).toHaveCount(
      1,
    );
    await expect(latestFirstTurn.getByTestId("turn-token-usage")).toContainText(
      "Total: 12",
    );
    const olderTurn = page.locator(
      '[data-message-group-index="1"], [data-message-group-index="2"]',
    );
    await expect(olderTurn.getByTestId("turn-token-usage")).toHaveCount(1);
    await expect(olderTurn.getByTestId("turn-token-usage")).toContainText(
      "Total: 140",
    );
  });
});
