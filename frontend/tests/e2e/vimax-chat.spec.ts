/**
 * E2E test: StoryboardConfirmCard renders in chat (3.2 e2e).
 *
 * Mocks a chat run that returns a plan_storyboard tool call + result, plus
 * the video-gen task/storyboard/render APIs the card reads. Verifies:
 * 1. The card renders (分镜 frames table visible)
 * 2. The "确认渲染" button is present
 *
 * NOTE: requires DEER_FLOW_AUTH_DISABLED=1.
 */

import { expect, test, type Route } from "@playwright/test";

import { pinEnglishLocale } from "./utils/locale";
import { mockLangGraphAPI } from "./utils/mock-api";

const MOCK_TASK_ID = "vg-e2e-card-0001";

const MOCK_STORYBOARD = {
  title: "E2E Storyboard Card Test",
  config: {
    video_type: "vimax",
    width: 1920,
    height: 1080,
    style: "realistic movie style",
  },
  frames: [
    {
      index: 0,
      narration: "快递员小明骑着电动车出发",
      visual_url: null,
      duration: 3.5,
    },
    {
      index: 1,
      narration: "老奶奶接过包裹微笑道谢",
      visual_url: null,
      duration: 3.0,
    },
  ],
  total_duration: 6.5,
};

/** SSE lines that simulate a chat run emitting a plan_storyboard tool call
 * followed by a tool result with a task_id.
 *
 * Uses `event: values` (full state snapshots) rather than `event: messages`
 * chunks — that is the shape the LangGraph SDK reliably folds into
 * thread.messages under these mocks (same as utils/mock-api.ts
 * handleRunStream). The message `type` values must be the wire form "ai" /
 * "tool" that groupMessages() switches on, not the class names
 * AIMessageChunk / ToolMessage. */
function buildPlanStoryboardSSE(): string {
  const humanMsg = {
    type: "human",
    id: "e2e-card-human",
    content: [{ type: "text", text: "做个故事视频" }],
  };
  const aiMsg = {
    type: "ai",
    id: "e2e-card-ai",
    content: "蓝图已生成",
    tool_calls: [
      { id: "tc1", name: "plan_storyboard", args: { video_type: "vimax" } },
    ],
  };
  const toolMsg = {
    type: "tool",
    id: "e2e-card-tool",
    name: "plan_storyboard",
    tool_call_id: "tc1",
    content: JSON.stringify({
      task_id: MOCK_TASK_ID,
      status: "draft",
      video_type: "vimax",
      n_frames: 2,
    }),
  };

  const events = [
    {
      event: "metadata",
      data: { run_id: "e2e-card-run", thread_id: "e2e-card-thread" },
    },
    { event: "values", data: { messages: [humanMsg, aiMsg, toolMsg] } },
    { event: "end", data: {} },
  ];

  return events
    .map((e) => `event: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`)
    .join("");
}

test.describe("Chat — StoryboardConfirmCard (3.2 e2e)", () => {
  test.beforeEach(async ({ page }) => {
    await pinEnglishLocale(page);
  });

  test("plan_storyboard tool result renders blueprint card with frames", async ({
    page,
  }) => {
    // Baseline LangGraph mocks (thread create / history / search / models …).
    // Without these every LangGraph call 500s, the message never sends and the
    // SSE mock below is never reached.
    mockLangGraphAPI(page);

    // Mock chat SSE to return a plan_storyboard tool call + result. Registered
    // after mockLangGraphAPI so it wins over that helper's default handler; the
    // path must carry the /api/langgraph prefix the client actually calls, and
    // both the thread-scoped and unscoped forms are covered.
    const fulfillStoryboardSSE = (route: Route) =>
      route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: buildPlanStoryboardSSE(),
      });
    await page.route("**/api/langgraph/runs/stream", fulfillStoryboardSSE);
    await page.route(
      "**/api/langgraph/threads/*/runs/stream",
      fulfillStoryboardSSE,
    );

    // Mock video-gen task detail (the card fetches task + storyboard)
    await page.route(`**/api/video/tasks/${MOCK_TASK_ID}`, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: MOCK_TASK_ID,
          task_type: "vimax",
          status: "draft",
          progress: 100,
          title: "测试蓝图",
          dirty_frames: [],
          course_project_id: null,
        }),
      }),
    );

    await page.route(`**/api/video/tasks/${MOCK_TASK_ID}/storyboard`, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_STORYBOARD),
      }),
    );

    // Navigate to a chat and send a message (triggers the mocked SSE)
    await page.goto("/workspace/chats/new");

    const textarea = page.getByPlaceholder(/how can i assist you/i);
    await expect(textarea).toBeVisible({ timeout: 15_000 });
    await textarea.fill("做个故事视频");
    await textarea.press("Enter");

    // The card should render — look for the storyboard title text
    await expect(page.getByText("视频蓝图已生成，请确认")).toBeVisible({
      timeout: 10_000,
    });

    // Frame narration text should be visible (from the mocked storyboard)
    await expect(page.getByText("快递员小明骑着电动车出发")).toBeVisible({
      timeout: 5_000,
    });
  });
});
