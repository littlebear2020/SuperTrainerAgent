/**
 * Render-loop regression guard for the chat page (React error #185,
 * "Maximum update depth exceeded" — client-side exception / white screen
 * observed on the server after the 2026-07-24 deploy).
 *
 * The production trigger was a thread containing `task` tool calls (subagent
 * work). MessageList feeds thread.messages into useSyncSubtaskStatus, whose
 * effect wrote a freshly-allocated object into the subtask store on every
 * render — so the store changed identity forever and React aborted at 50
 * nested updates.
 *
 * Unit tests pin the merge/identity contract, but only a real render can prove
 * the loop is gone: this drives the actual page and fails on any uncaught
 * page error (#185 surfaces as one) while asserting the subtask UI renders.
 */
import { expect, test } from "@playwright/test";

import { pinEnglishLocale } from "./utils/locale";
import { mockLangGraphAPI } from "./utils/mock-api";

const SUBTASK_THREAD_ID = "00000000-0000-0000-0000-0000000000aa";
const TASK_CALL_ID = "call_subtask_1";

/**
 * History for a thread where the assistant delegated to a subagent: an AI
 * message carrying a `task` tool call plus its tool result. This is what
 * deriveSubtasks() reads, so it exercises the exact loop path.
 */
function subtaskHistoryPayload(completed: boolean) {
  const messages: unknown[] = [
    {
      type: "human",
      id: "msg-human-subtask",
      content: [{ type: "text", text: "Research something for me" }],
    },
    {
      type: "ai",
      id: "msg-ai-subtask",
      content: "Delegating to a subagent.",
      tool_calls: [
        {
          id: TASK_CALL_ID,
          name: "task",
          args: {
            subagent_type: "general-purpose",
            description: "Investigate the topic",
            prompt: "Look into the topic and report back",
          },
        },
      ],
    },
  ];

  if (completed) {
    messages.push({
      type: "tool",
      id: "msg-tool-subtask",
      tool_call_id: TASK_CALL_ID,
      content: "Finished investigating the topic.",
    });
  }

  return [
    {
      values: { title: "Subtask thread", messages },
      next: [],
      metadata: {},
      created_at: "2025-01-01T00:00:00Z",
      parent_config: null,
    },
  ];
}

test.describe("Chat render-loop regression (React #185)", () => {
  test.beforeEach(async ({ page }) => {
    await pinEnglishLocale(page);
  });

  for (const completed of [false, true]) {
    const label = completed ? "completed subtask" : "in-progress subtask";

    test(`renders a thread with a ${label} without exceeding update depth`, async ({
      page,
    }) => {
      const pageErrors: string[] = [];
      page.on("pageerror", (err) => pageErrors.push(err.message));

      mockLangGraphAPI(page, {
        threads: [{ thread_id: SUBTASK_THREAD_ID, title: "Subtask thread" }],
      });

      // Override history/state with a subtask-bearing payload (registered after
      // mockLangGraphAPI so these take precedence for this thread).
      const payload = subtaskHistoryPayload(completed);
      await page.route(`**/threads/${SUBTASK_THREAD_ID}/history`, (route) =>
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(payload),
        }),
      );
      await page.route(`**/threads/${SUBTASK_THREAD_ID}/state`, (route) =>
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            ...payload[0],
            created_at: "2025-01-01T00:00:00Z",
          }),
        }),
      );

      await page.goto(`/workspace/chats/${SUBTASK_THREAD_ID}`);

      // The page must actually render (a #185 throw unmounts the tree, so the
      // input box never appears — this catches the white screen directly).
      await expect(page.getByPlaceholder(/how can i assist you/i)).toBeVisible({
        timeout: 15_000,
      });

      // The subtask card derived from the `task` tool call should render.
      await expect(page.getByText("Investigate the topic")).toBeVisible({
        timeout: 15_000,
      });

      // Let any runaway loop accumulate deterministically — 50 nested updates
      // throws almost immediately, so two clean animation frames (render
      // pipeline quiesced) are decisive; a busy loop starves the rAF pair
      // until the test timeout, which is red either way.
      // (2026-10-05 基建批：rAF 双帧替换固定 1s 盲等——同判定力，无墙钟依赖。)
      await page.evaluate(
        () =>
          new Promise<void>((resolve) => {
            requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
          }),
      );

      expect(
        pageErrors.filter((m) =>
          /Minified React error #185|Maximum update depth/i.test(m),
        ),
      ).toEqual([]);
      expect(pageErrors).toEqual([]);
    });
  }
});
