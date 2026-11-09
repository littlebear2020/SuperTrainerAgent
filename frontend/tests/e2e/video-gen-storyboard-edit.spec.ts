import { expect, test } from "@playwright/test";

import { mockVideoAPI, type MockStoryboard } from "./utils/mock-video-api";

const TASK_ID = "vg-edit-e2e";

const STORYBOARD: MockStoryboard = {
  title: "安全培训分镜",
  config: { video_type: "ai_image", width: 1080, height: 1920 },
  status: "draft",
  frames: [
    {
      index: 0,
      narration: "原始旁白内容",
      image_prompt: "office safety scene",
      duration: 4,
      visual_url: null,
      local_visual_path: null,
    },
    {
      index: 1,
      narration: "第二帧旁白内容",
      image_prompt: "compliance scene",
      duration: 5,
      visual_url: "https://example.com/frame-1.mp4",
      local_visual_path: null,
    },
  ],
  total_duration: 9,
  audio_ready: true,
};

test("storyboard edit and upload flow renders to a playable video", async ({
  page,
}) => {
  const api = mockVideoAPI(page);
  api.seedTask({ id: TASK_ID, status: "draft", progress: 100 });
  api.upsertStoryboard(TASK_ID, STORYBOARD);

  await page.goto(`/workspace/video-gen/${TASK_ID}/storyboard`);
  await expect(page.getByText("分镜编辑器")).toBeVisible({
    timeout: 10_000,
  });

  const firstFrame = page.locator("#frame-card-0");
  await expect(firstFrame.getByText("原始旁白内容")).toBeVisible();

  await firstFrame
    .getByText("旁白")
    .locator("xpath=..")
    .getByRole("button")
    .click();
  await firstFrame.getByRole("textbox").fill("修改后的安全培训旁白");
  await page.getByText("分镜编辑器").click();

  await expect
    .poll(() => api.frameUpdateRequests.length, { timeout: 5_000 })
    .toBe(1);
  expect(api.frameUpdateRequests[0]).toMatchObject({
    method: "PUT",
    url: `/api/video/tasks/${TASK_ID}/storyboard/frames/0`,
    body: { narration: "修改后的安全培训旁白" },
  });
  await expect(firstFrame.getByText("修改后的安全培训旁白")).toBeVisible();

  await Promise.all([
    page.waitForRequest((request) =>
      request.url().endsWith(`/tasks/${TASK_ID}/frames/0/upload-visual`),
    ),
    firstFrame.locator('input[type="file"]').setInputFiles({
      name: "replacement.png",
      mimeType: "image/png",
      buffer: Buffer.from(
        "89504e470d0a1a0a0000000d4948445200000001000000010806000000",
        "hex",
      ),
    }),
  ]);
  await expect
    .poll(() => api.uploadVisualRequests.length, { timeout: 10_000 })
    .toBe(1);
  expect(api.uploadVisualRequests[0]?.url).toBe(
    `/api/video/tasks/${TASK_ID}/frames/0/upload-visual`,
  );
  await expect(firstFrame.getByText("本地素材", { exact: true })).toBeVisible({
    timeout: 10_000,
  });

  await page.getByRole("button", { name: "开始渲染" }).click();
  await page.getByRole("button", { name: "确认渲染" }).click();

  await expect(page).toHaveURL(new RegExp(`/workspace/video-gen/${TASK_ID}$`));
  await expect(page.getByText("视频合成中")).toBeVisible({
    timeout: 10_000,
  });
  await expect(page.getByText("35%")).toBeVisible();

  expect(api.renderRequests).toHaveLength(1);
  expect(api.renderRequests[0]).toMatchObject({
    method: "POST",
    url: `/api/video/tasks/${TASK_ID}/render`,
    body: { frame_edits: {} },
  });

  api.setTaskStatus(TASK_ID, "ready", { progress: 100 });
  const video = page.locator("video");
  await expect(video).toBeVisible({ timeout: 10_000 });
  await expect
    .poll(() => api.downloadRequests.length, { timeout: 10_000 })
    .toBeGreaterThan(0);
});
