/**
 * E2E tests for Video Gen frontend UX features.
 *
 * Mocks all backend video-gen API endpoints via page.route().
 * Tests: language selector, BGM dynamic list, timeline, storyboard frame upload,
 * and detail page frame operations.
 *
 * NOTE: These tests require DEER_FLOW_AUTH_DISABLED=1 to bypass login.
 * If a dev server is already running on port 3000 without this flag,
 * run:  DEER_FLOW_AUTH_DISABLED=1 pnpm build && DEER_FLOW_AUTH_DISABLED=1 pnpm start
 * Then: npx playwright test tests/e2e/video-gen.spec.ts
 */

import { expect, test, type Page } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

// ── Mock data ──────────────────────────────────────────────────────────

const MOCK_TASK_ID = "vg-e2e-0001";
const MOCK_TASK_ID_READY = "vg-e2e-0002";

const MOCK_VOICES = [
  {
    id: "zh-CN-YunjianNeural",
    name: "云健（男）",
    locale: "zh-CN",
    gender: "Male",
  },
  {
    id: "zh-CN-XiaoxiaoNeural",
    name: "晓晓（女）",
    locale: "zh-CN",
    gender: "Female",
  },
  {
    id: "en-US-ChristopherNeural",
    name: "Christopher (Male)",
    locale: "en-US",
    gender: "Male",
  },
];

const MOCK_BGM = {
  built_in: [
    {
      id: "morning_piano",
      name: "清晨钢琴",
      preview_url: "/api/video/bgm/morning_piano/preview",
      source: "built-in",
      tags: ["钢琴", "清晨", "轻快"],
      duration: 179,
    },
    {
      id: "strive",
      name: "奋进",
      preview_url: "/api/video/bgm/strive/preview",
      source: "built-in",
      tags: ["励志", "纪录片", "企业"],
      duration: 80,
    },
  ],
  custom: [],
};

const MOCK_STORYBOARD = {
  title: "E2E Test Storyboard",
  config: { video_type: "stock", width: 1080, height: 1920 },
  frames: [
    {
      index: 0,
      narration: "第一帧旁白内容",
      visual_url: "https://example.com/0.mp4",
      local_visual_path: null,
      duration: 3.5,
    },
    {
      index: 1,
      narration: "第二帧旁白内容",
      visual_url: null,
      local_visual_path: null,
      duration: 4.0,
    },
    {
      index: 2,
      narration: "第三帧旁白内容",
      visual_url: "https://example.com/2.mp4",
      local_visual_path: "visuals/frame_002.jpg",
      duration: 3.0,
    },
  ],
  total_duration: 10.5,
};

const MOCK_TIMELINE = {
  totalDuration: 10.5,
  tracks: [
    {
      id: "visual",
      type: "visual",
      clips: [
        {
          frameIndex: 0,
          startTime: 0,
          duration: 3.5,
          label: "第一帧旁白内容",
          hasVisual: true,
        },
        {
          frameIndex: 1,
          startTime: 3.5,
          duration: 4.0,
          label: "第二帧旁白内容",
          hasVisual: false,
        },
        {
          frameIndex: 2,
          startTime: 7.5,
          duration: 3.0,
          label: "第三帧旁白内容",
          hasVisual: true,
        },
      ],
    },
    {
      id: "audio",
      type: "audio",
      clips: [
        { frameIndex: 0, startTime: 0, duration: 3.5, label: "第一帧旁白内容" },
        {
          frameIndex: 1,
          startTime: 3.5,
          duration: 4.0,
          label: "第二帧旁白内容",
        },
        {
          frameIndex: 2,
          startTime: 7.5,
          duration: 3.0,
          label: "第三帧旁白内容",
        },
      ],
    },
    {
      id: "subtitle",
      type: "subtitle",
      clips: [
        { frameIndex: 0, startTime: 0, duration: 3.5, label: "第一帧旁白内容" },
        {
          frameIndex: 1,
          startTime: 3.5,
          duration: 4.0,
          label: "第二帧旁白内容",
        },
        {
          frameIndex: 2,
          startTime: 7.5,
          duration: 3.0,
          label: "第三帧旁白内容",
        },
      ],
    },
  ],
};

// ── Mock helper ─────────────────────────────────────────────────────────

function mockVideoGenAPI(page: Page) {
  // Voices
  void page.route("**/api/video/voices**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(MOCK_VOICES),
    }),
  );

  // BGM list
  void page.route("**/api/video/bgm", (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_BGM),
      });
    }
    return route.fallback();
  });

  // BGM/voice preview
  void page.route("**/api/video/bgm/*/preview", (route) =>
    route.fulfill({
      status: 200,
      contentType: "audio/mpeg",
      body: Buffer.alloc(0),
    }),
  );
  void page.route("**/api/video/voices/*/preview**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "audio/mpeg",
      body: Buffer.alloc(0),
    }),
  );

  // Templates
  void page.route("**/api/video/templates**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );

  void page.route("**/api/video/projects", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
  void page.route("**/api/video/providers", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ image: [], video: [] }),
    }),
  );

  // Task list
  void page.route("**/api/video/tasks", (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            id: MOCK_TASK_ID,
            user_id: "test",
            domain: "video_gen",
            task_type: "stock",
            status: "draft",
            progress: 0,
            error_message: null,
            retry_count: 0,
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
            thumbnail_url: null,
          },
          {
            id: MOCK_TASK_ID_READY,
            user_id: "test",
            domain: "video_gen",
            task_type: "stock",
            status: "ready",
            progress: 100,
            error_message: null,
            retry_count: 0,
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
            thumbnail_url: null,
          },
        ]),
      });
    }
    return route.fallback();
  });

  // Task detail — match specific task IDs
  void page.route("**/api/video/tasks/*", (route) => {
    const url = route.request().url();
    const method = route.request().method();

    if (method === "GET") {
      // Sub-routes — let them fall through to more specific handlers
      if (
        url.includes("/storyboard") ||
        url.includes("/timeline") ||
        url.includes("/download") ||
        url.includes("/thumbnail")
      ) {
        return route.fallback();
      }
      const id = /\/api\/video\/tasks\/([^/?]+)/.exec(url)?.[1];
      // Keep these in sync with the backend TaskDetail/TaskBrief schema
      // (backend/app/gateway/_shared/task_framework/schemas.py). dirty_frames
      // in particular defaults to [] server-side and is read unguarded by
      // video-gen-detail.tsx — omitting it here crashed the page under test.
      const task =
        id === MOCK_TASK_ID_READY
          ? {
              id,
              user_id: "test",
              domain: "video_gen",
              task_type: "stock",
              status: "ready",
              progress: 100,
              title: null,
              error_message: null,
              retry_count: 0,
              dirty_frames: [],
              created_at: new Date().toISOString(),
              updated_at: new Date().toISOString(),
              thumbnail_url: null,
              payload: {},
              result: {},
            }
          : {
              id,
              user_id: "test",
              domain: "video_gen",
              task_type: "stock",
              status: "draft",
              progress: 0,
              title: null,
              error_message: null,
              retry_count: 0,
              dirty_frames: [],
              created_at: new Date().toISOString(),
              updated_at: new Date().toISOString(),
              thumbnail_url: null,
              payload: {},
              result: {},
            };
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(task),
      });
    }
    return route.fallback();
  });

  // Storyboard
  void page.route("**/api/video/tasks/*/storyboard", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(MOCK_STORYBOARD),
    }),
  );

  // Timeline
  void page.route("**/api/video/tasks/*/timeline", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(MOCK_TIMELINE),
    }),
  );

  // Download — return empty video
  void page.route("**/api/video/tasks/*/download", (route) =>
    route.fulfill({
      status: 200,
      contentType: "video/mp4",
      body: Buffer.alloc(0),
    }),
  );
}

// ── Tests ───────────────────────────────────────────────────────────────

test.describe("Video Gen — Create Task Dialog", () => {
  test.beforeEach(async ({ page }) => {
    mockLangGraphAPI(page);
    mockVideoGenAPI(page);
    await page.goto("/workspace");
    // Wait for page to hydrate
    await page.waitForLoadState("networkidle");
  });

  test("language selector shows Chinese and English options", async ({
    page,
  }) => {
    await page.locator("[data-course-entry='ai_image']").click();
    await expect(page.getByText("视频类型")).toBeVisible({ timeout: 5_000 });

    // Language label should be visible
    await expect(page.getByText("语言")).toBeVisible();
  });

  test("BGM dropdown shows built-in tracks in advanced settings", async ({
    page,
  }) => {
    await page.locator("[data-course-entry='ai_image']").click();
    await expect(page.getByText("视频类型")).toBeVisible({ timeout: 5_000 });

    // Open advanced settings
    await page.getByText("高级设置").click();

    // Should show BGM label
    await expect(page.getByText("背景音乐")).toBeVisible();

    // 打开 BGM 选择器。2026-07-31 C4 起 BGM 从 Select 换成 Popover + Button
    // trigger（原 grid-cols-3 窄列里 4 个控件溢出重叠），已无 combobox role；
    // 按 trigger 的稳定 aria-label 定位，不随选中曲目变化。
    await page.getByRole("button", { name: "选择背景音乐" }).click();

    // Should show built-in group and tracks
    await expect(page.getByText(/内置\s*\d+/)).toBeVisible();
    await expect(page.getByText("清晨钢琴")).toBeVisible();
    await expect(page.getByText("奋进")).toBeVisible();
  });

  // Task 7（2026-08-01 反馈 2）：曲目「可见」但一首都「点不中」——
  // dismissable-layer 被 pnpm 装成两份副本，Dialog 内的 Popover 拿不到
  // pointer-events:auto。上一条用例只用 toBeVisible 断言，而 toBeVisible
  // **不检查 pointer-events**，所以这个 bug 一路绿灯上线。
  // 本用例真的点下去并验证回填，是守「能选中」的唯一护栏。
  test("BGM track is actually clickable inside the dialog (not just visible)", async ({
    page,
  }) => {
    await page.locator("[data-course-entry='ai_image']").click();
    await expect(page.getByText("视频类型")).toBeVisible({ timeout: 5_000 });
    await page.getByText("高级设置").click();

    const trigger = page.getByRole("button", { name: "选择背景音乐" });
    await trigger.click();

    // 真实点击某一首。修复前这里会因 pointer-events:none 而超时。
    await page
      .getByRole("option", { name: /清晨钢琴/ })
      .click({ timeout: 5_000 });

    // 选中后 trigger 应回填曲名 —— 证明 onChange 真的跑了，而不只是弹层可见
    await expect(trigger).toContainText("清晨钢琴");
  });
});

test.describe("Video Gen — Detail Page", () => {
  test.beforeEach(async ({ page }) => {
    mockVideoGenAPI(page);
  });

  // F7 replaced the old fake three-track timeline + frame-operation grid with
  // a 分镜 (storyboard) strip of numbered frame buttons plus a selected-frame
  // panel (see video-gen-detail.tsx "F7：替代旧假三轨时间线与帧操作网格").
  // These assertions track the current UI; the old 时间线 / 帧操作 / 帧 N
  // labels no longer exist.
  test("ready task shows the storyboard strip with total duration", async ({
    page,
  }) => {
    await page.goto(`/workspace/video-gen/${MOCK_TASK_ID_READY}`);
    await page.waitForLoadState("networkidle");

    const strip = page.getByTestId("storyboard-strip-section");
    await expect(strip).toBeVisible({ timeout: 10_000 });
    await expect(strip.getByRole("heading", { name: "分镜" })).toBeVisible();
    // MOCK_TIMELINE.totalDuration
    await expect(strip.getByText("10.5s")).toBeVisible();
  });

  test("ready task shows one strip button per frame", async ({ page }) => {
    await page.goto(`/workspace/video-gen/${MOCK_TASK_ID_READY}`);
    await page.waitForLoadState("networkidle");

    const strip = page.getByTestId("storyboard-strip-section");
    await expect(strip).toBeVisible({ timeout: 10_000 });

    // MOCK_STORYBOARD has 3 frames, rendered as buttons labelled 1..3.
    for (const label of ["1", "2", "3"]) {
      await expect(strip.getByRole("button", { name: label })).toBeVisible();
    }
  });

  test("selecting a frame opens the frame operations panel", async ({
    page,
  }) => {
    await page.goto(`/workspace/video-gen/${MOCK_TASK_ID_READY}`);
    await page.waitForLoadState("networkidle");

    const strip = page.getByTestId("storyboard-strip-section");
    await expect(strip).toBeVisible({ timeout: 10_000 });

    // Frames carry a tooltip with index, duration and narration — this is the
    // per-frame metadata the old "帧 N" cards used to surface.
    await expect(strip.getByRole("button", { name: "1" })).toHaveAttribute(
      "title",
      /第一帧旁白内容/,
    );
  });

  // NOTE: the former "frame cards show visual status indicators" case lived
  // here but asserted 在线素材 / 待生成 / 本地素材 — labels that only ever
  // render in the storyboard editor (video-gen-storyboard.tsx), not on the
  // detail page. It is covered by "storyboard shows visual status for each
  // frame" below, so it is not duplicated here.
});

test.describe("Video Gen — Storyboard Editor", () => {
  test.beforeEach(async ({ page }) => {
    mockVideoGenAPI(page);
  });

  test("storyboard page shows frame cards with upload buttons", async ({
    page,
  }) => {
    await page.goto(`/workspace/video-gen/${MOCK_TASK_ID}/storyboard`);
    await page.waitForLoadState("networkidle");

    await expect(page.getByText("分镜编辑器")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText("分镜 1")).toBeVisible();
    await expect(page.getByText("分镜 2")).toBeVisible();

    // Upload buttons per frame
    const uploadButtons = page.getByRole("button", { name: "替换素材" });
    await expect(uploadButtons).toHaveCount(3);
  });

  test("storyboard shows visual status for each frame", async ({ page }) => {
    await page.goto(`/workspace/video-gen/${MOCK_TASK_ID}/storyboard`);
    await page.waitForLoadState("networkidle");

    await expect(page.getByText("分镜编辑器")).toBeVisible({ timeout: 10_000 });

    // exact:true — "待生成" also appears inside the bulk-preview button label
    // ("一键生成全部预览（1 帧待生成）"), which made a substring match ambiguous.
    await expect(page.getByText("在线素材", { exact: true })).toBeVisible();
    await expect(page.getByText("待生成", { exact: true })).toBeVisible();
    await expect(page.getByText("本地素材", { exact: true })).toBeVisible();
  });
});
