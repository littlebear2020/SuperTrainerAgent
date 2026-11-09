import { expect, test, type Page } from "@playwright/test";

import { mockVideoAPI, type MockStoryboard } from "./utils/mock-video-api";

const STORYBOARD: MockStoryboard = {
  title: "失败任务分镜",
  config: { video_type: "ai_image", width: 1080, height: 1920 },
  status: "draft",
  frames: [
    {
      index: 0,
      narration: "已生成的旁白",
      image_prompt: "rendered frame",
      duration: 4,
      visual_url: "https://example.com/frame.mp4",
    },
  ],
  total_duration: 4,
  audio_ready: true,
};

async function openCreateDialog(page: Page) {
  await page.goto("/workspace");
  await page.locator("[data-course-entry='ai_image']").click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText("视频类型")).toBeVisible();
  return dialog;
}

test.describe("video-gen error states", () => {
  test("create 422 surfaces field error and stays on the dialog", async ({
    page,
  }) => {
    const api = mockVideoAPI(page);
    const dialog = await openCreateDialog(page);

    await dialog
      .getByPlaceholder(/每句话对应一个画面/)
      .fill("会被后端 schema 拒绝的脚本");
    api.failNextCreate(422, [
      { loc: ["body", "topic"], msg: "topic 字段非法" },
    ]);

    await dialog.getByRole("button", { name: "生成视频" }).click();

    await expect(page.getByText("topic 字段非法")).toBeVisible({
      timeout: 5_000,
    });
    await expect(dialog).toBeVisible();
    await expect(page).toHaveURL(/\/workspace$/);
  });

  test("create 503 reports the remotion pool outage and stays on the dialog", async ({
    page,
  }) => {
    const api = mockVideoAPI(page);
    const dialog = await openCreateDialog(page);

    await dialog.getByRole("combobox").first().click();
    await page.getByRole("option", { name: "文字动效" }).click();
    await dialog.getByPlaceholder(/粘贴\/上传课程脚本/).fill("课程脚本内容");
    api.failNextCreate(503, "Remotion 渲染池不可用");

    await dialog.getByRole("button", { name: "生成视频" }).click();

    await expect(page.getByText("Remotion 渲染池不可用")).toBeVisible({
      timeout: 5_000,
    });
    await expect(dialog).toBeVisible();
    await expect(page).toHaveURL(/\/workspace$/);
  });

  test("storyboard 404 shows the load error state", async ({ page }) => {
    const api = mockVideoAPI(page);
    api.seedTask({
      id: "vg-missing-storyboard",
      status: "failed",
      error_message: "Storyboard not found",
    });

    await page.goto("/workspace/video-gen/vg-missing-storyboard/storyboard");

    await expect(page.getByText("视频任务生成失败")).toBeVisible({
      timeout: 10_000,
    });
    await expect(page.getByRole("button", { name: "返回" })).toBeVisible();
  });

  test("terminal render failure keeps a retry entry", async ({ page }) => {
    const api = mockVideoAPI(page);
    api.seedTask({
      id: "vg-render-failed",
      status: "failed",
      progress: 55,
      error_message: "渲染中断，请重试合成",
    });
    api.upsertStoryboard("vg-render-failed", STORYBOARD);

    await page.goto("/workspace/video-gen/vg-render-failed");

    await expect(page.getByText("渲染中断，请重试合成")).toBeVisible({
      timeout: 10_000,
    });
    await expect(page.getByRole("button", { name: /重试合成/ })).toBeVisible();
  });
});
