/**
 * PR1（2026-11-24 前端可靠性批）：video-gen 编辑安全的行为验收（mock API，零付费）。
 *
 * - E5：ai_image / ai_video / ai_visual 帧面板在画面提示词 / 视觉来源未保存时，
 *   「重生画面」禁用且零 regenerate 请求；与旁白未保存（只挡「重生配音」）互不误伤。
 * - E7：切换视觉来源 / 改版式 / 一键关闭图文镜失败后回滚并只提示一次。
 * - E4：cancelled 终态后任务详情轮询停止。
 * - 非目标：ViMax 详情页不出现帧面板（原按钮规则不变）。
 *
 * 需 DEER_FLOW_AUTH_DISABLED=1（playwright.config.ts 的 webServer 已带）。
 */

import { expect, test, type Locator, type Page } from "@playwright/test";

import { mockVideoAPI, type MockStoryboard } from "./utils/mock-video-api";

const GUARDED_TYPES = ["ai_image", "ai_video", "ai_visual"] as const;

const SAVED_PROMPT = "已保存的提示词";
const SAVED_NARRATION = "已保存的旁白";

function storyboardFor(taskType: string): MockStoryboard {
  return {
    title: "编辑安全分镜",
    config: { video_type: taskType, width: 1080, height: 1920 },
    status: "ready",
    frames: [
      {
        index: 0,
        narration: SAVED_NARRATION,
        image_prompt: SAVED_PROMPT,
        duration: 4,
        visual_source: "ai_image",
        visual_url: null,
        local_visual_path: null,
      },
      {
        index: 1,
        narration: "第二帧旁白",
        image_prompt: "second frame",
        duration: 4,
        visual_source: "ai_image",
      },
    ],
    total_duration: 8,
    audio_ready: true,
  };
}

async function openFramePanel(page: Page, taskId: string) {
  await page.goto(`/workspace/video-gen/${taskId}`);
  const strip = page.getByTestId("storyboard-strip-section");
  await expect(strip).toBeVisible({ timeout: 10_000 });
  // 帧条目 = dnd-kit 属性的 div（ready 态不可拖拽 → aria-disabled），只认点击处理器
  const frameOne = strip.getByRole("button", { name: /^1 / });
  await expect(frameOne).toBeVisible();
  // SSR 首屏就带帧条，但 onClick 要等 React 水合；force 点击跳过可操作性检查，
  // 先等到 React 把 props 挂到该节点（__reactProps$*）再点，否则点击会丢。
  await frameOne.evaluate(
    (el) =>
      new Promise<void>((resolve) => {
        const hydrated = () =>
          Object.keys(el).some((key) => key.startsWith("__reactProps"));
        if (hydrated()) {
          resolve();
          return;
        }
        const startedAt = Date.now();
        const timer = setInterval(() => {
          if (hydrated() || Date.now() - startedAt > 10_000) {
            clearInterval(timer);
            resolve();
          }
        }, 50);
      }),
  );
  await frameOne.click({ force: true });
  const regenVisual = page.getByTestId("frame-regen-visual");
  await expect(regenVisual).toBeVisible();
  // 面板右栏（提示句的父节点）内：[0] 旁白、[1] 画面提示词；切换器也在这一栏
  const rightColumn = page
    .getByText("切换视觉来源后需点「重生画面」出新画面")
    .locator("xpath=..");
  return {
    regenVisual,
    regenAudio: page.getByRole("button", { name: "重生配音" }),
    narration: rightColumn.getByRole("textbox").nth(0),
    prompt: rightColumn.getByRole("textbox").nth(1),
    sourceSwitcher: rightColumn.getByRole("combobox"),
  };
}

async function pickOption(page: Page, switcher: Locator, name: string) {
  await switcher.click();
  await page.getByRole("option", { name }).click();
}

for (const taskType of GUARDED_TYPES) {
  test(`${taskType}：帧面板未保存提示词 / 视觉来源时「重生画面」零付费请求`, async ({
    page,
  }) => {
    const api = mockVideoAPI(page);
    const taskId = `vg-guard-${taskType}`;
    api.seedTask({
      id: taskId,
      status: "ready",
      progress: 100,
      task_type: taskType,
    });
    api.upsertStoryboard(taskId, storyboardFor(taskType));

    const panel = await openFramePanel(page, taskId);
    await expect(panel.regenVisual).toBeEnabled();

    // 画面提示词未保存 → 禁用 + 提示先保存
    await panel.prompt.fill("改了还没保存的提示词");
    await expect(panel.regenVisual).toBeDisabled();
    await expect(panel.regenVisual).toHaveAttribute("title", /请先保存/);

    // 视觉来源也未保存 → 仍禁用
    await pickOption(page, panel.sourceSwitcher, "AI 生视频");
    await expect(panel.regenVisual).toBeDisabled();
    await expect(panel.regenVisual).toHaveAttribute("title", /请先保存/);

    // 提示词改回原值，来源仍未保存 → 仍禁用
    await panel.prompt.fill(SAVED_PROMPT);
    await expect(panel.regenVisual).toBeDisabled();

    // 旁白独立：只改旁白 → 「重生画面」可点、「重生配音」禁用（T12）
    await pickOption(page, panel.sourceSwitcher, "AI 生图");
    await expect(panel.regenVisual).toBeEnabled();
    await panel.narration.fill("改了还没保存的旁白");
    await expect(panel.regenVisual).toBeEnabled();
    await expect(panel.regenAudio).toBeDisabled();
    await expect(panel.regenAudio).toHaveAttribute("title", /请先保存旁白/);

    // 只留提示词未保存 → 「重生配音」可点（visualDirty 不误挡配音）
    await panel.narration.fill(SAVED_NARRATION);
    await panel.prompt.fill("又改了提示词");
    await expect(panel.regenAudio).toBeEnabled();
    await expect(panel.regenVisual).toBeDisabled();

    expect(api.regenerateRequests).toHaveLength(0);

    // 对照：全部恢复后点击才发付费请求
    await panel.prompt.fill(SAVED_PROMPT);
    await expect(panel.regenVisual).toBeEnabled();
    await panel.regenVisual.click();
    await expect
      .poll(() => api.regenerateRequests.length, { timeout: 5_000 })
      .toBe(1);
    expect(api.regenerateRequests[0]).toMatchObject({
      method: "POST",
      url: `/api/video/tasks/${taskId}/frames/0/regenerate`,
      body: { mode: "visual_only" },
    });
  });
}

test("vimax：详情页没有帧面板与「重生画面」（非目标行为不变）", async ({
  page,
}) => {
  const api = mockVideoAPI(page);
  api.seedTask({
    id: "vg-vimax-nontarget",
    status: "ready",
    progress: 100,
    task_type: "vimax",
    title: "非目标 ViMax 任务",
  });
  api.upsertStoryboard("vg-vimax-nontarget", storyboardFor("vimax"));

  await page.goto("/workspace/video-gen/vg-vimax-nontarget");
  // ViMax 详情页仍是它自己的分镜区（不出现 AI 管线的帧条与「重生画面」）
  await expect(
    page.getByRole("heading", { name: "ViMax 分镜（可逐镜重做）" }),
  ).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("storyboard-strip-section")).toHaveCount(0);
  await expect(page.getByTestId("frame-regen-visual")).toHaveCount(0);
  expect(api.regenerateRequests).toHaveLength(0);
});

test("cancelled 终态后任务详情轮询停止", async ({ page }) => {
  const api = mockVideoAPI(page);
  const taskId = "vg-cancelled-poll";
  api.seedTask({ id: taskId, status: "running", progress: 40 });
  api.upsertStoryboard(taskId, storyboardFor("ai_image"));

  await page.goto(`/workspace/video-gen/${taskId}`);
  await expect(page.getByRole("heading", { name: "任务详情" })).toBeVisible({
    timeout: 10_000,
  });
  // running 态 2s 一轮：先确认轮询确实在跑
  await expect
    .poll(() => api.taskDetailRequests.length, { timeout: 10_000 })
    .toBeGreaterThanOrEqual(2);

  api.setTaskStatus(taskId, "cancelled", { progress: 0 });
  // 等到某次轮询读到 cancelled——之后不应再有任务详情请求
  await expect
    .poll(() => api.taskDetailRequests.some((r) => r.status === "cancelled"), {
      timeout: 10_000,
    })
    .toBe(true);
  await page.waitForTimeout(500);
  const settled = api.taskDetailRequests.length;
  await page.waitForTimeout(5_000);
  expect(api.taskDetailRequests.length).toBe(settled);
  // 页面在 cancelled 终态下仍正常渲染
  await expect(page.getByRole("heading", { name: "任务详情" })).toBeVisible();
});

test.describe("乐观编辑失败：回滚并只提示一次", () => {
  test("切换视觉来源失败", async ({ page }) => {
    const api = mockVideoAPI(page);
    const taskId = "vg-rollback-source";
    api.seedTask({ id: taskId, status: "draft", progress: 100 });
    api.upsertStoryboard(taskId, storyboardFor("ai_image"));

    await page.goto(`/workspace/video-gen/${taskId}/storyboard`);
    await expect(page.getByText("分镜编辑器")).toBeVisible({
      timeout: 10_000,
    });
    const card = page.locator("#frame-card-0");
    const switcher = card.getByRole("combobox").first();
    await expect(switcher).toHaveText(/AI 生图/);

    api.failNextFrameUpdate(500, "视觉来源写盘失败");
    await pickOption(page, switcher, "AI 生视频");

    const toast = page.getByText("切换视觉来源失败：视觉来源写盘失败");
    await expect(toast).toBeVisible({ timeout: 5_000 });
    await expect(toast).toHaveCount(1);
    // 夹具自证：请求确实带着新来源打到了 mock，且失败由夹具下发
    expect(api.frameUpdateRequests).toHaveLength(1);
    expect(api.frameUpdateRequests[0]?.body).toEqual({
      visual_source: "ai_video",
    });
    // 回滚：切换器弹回原值
    await expect(switcher).toHaveText(/AI 生图/);
  });

  test("一键关闭图文镜失败", async ({ page }) => {
    const api = mockVideoAPI(page);
    const taskId = "vg-rollback-bulk";
    api.seedTask({ id: taskId, status: "draft", progress: 100 });
    const storyboard = storyboardFor("ai_image");
    storyboard.frames = storyboard.frames?.map((frame, i) =>
      i === 0 ? { ...frame, visual_source: "graphic" } : frame,
    );
    api.upsertStoryboard(taskId, storyboard);

    await page.goto(`/workspace/video-gen/${taskId}/storyboard`);
    await expect(page.getByText("分镜编辑器")).toBeVisible({
      timeout: 10_000,
    });
    await expect(page.getByText(/图文镜：1 帧/)).toBeVisible();

    api.failNextBulkDisableGraphic(409, "任务正在合成");
    await page.getByRole("button", { name: "一键关闭" }).click();

    const toast = page.getByText("停用图形镜失败：任务正在合成");
    await expect(toast).toBeVisible({ timeout: 5_000 });
    await expect(toast).toHaveCount(1);
    expect(api.bulkDisableRequests).toHaveLength(1);
    // 回滚：图文镜计数没被乐观翻转清掉
    await expect(page.getByText(/图文镜：1 帧/)).toBeVisible();
  });

  test("remotion_course 改版式失败", async ({ page }) => {
    const api = mockVideoAPI(page);
    const taskId = "vg-rollback-layout";
    api.seedTask({
      id: taskId,
      status: "draft",
      progress: 100,
      task_type: "remotion_course",
    });
    api.upsertStoryboard(taskId, storyboardFor("remotion_course"));

    await page.goto(`/workspace/video-gen/${taskId}/storyboard`);
    // remotion_course 的编辑器标题是分镜标题、帧卡也不是 #frame-card-N；以首个版式切换器为准
    const switcher = page
      .getByText("画面版式", { exact: true })
      .first()
      .locator("xpath=..")
      .getByRole("combobox");
    await expect(switcher).toBeVisible({ timeout: 10_000 });
    await expect(switcher).toHaveText(/自动（推荐）/);

    api.failNextFrameUpdate(500, "版式写盘失败");
    await pickOption(page, switcher, "课程封面");

    const toast = page.getByText("版式保存失败：版式写盘失败");
    await expect(toast).toBeVisible({ timeout: 5_000 });
    await expect(toast).toHaveCount(1);
    expect(api.frameUpdateRequests).toHaveLength(1);
    expect(api.frameUpdateRequests[0]?.body).toEqual({ layout: "CoverPage" });
    // 回滚：版式弹回「自动」
    await expect(switcher).toHaveText(/自动（推荐）/);
  });
});
