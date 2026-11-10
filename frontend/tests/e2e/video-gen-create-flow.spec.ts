import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";
import { mockVideoAPI, type MockStoryboard } from "./utils/mock-video-api";

const STORYBOARD: MockStoryboard = {
  title: "新员工培训课程",
  config: { video_type: "ai_image", width: 1080, height: 1920 },
  status: "ready",
  progress: 100,
  frames: [
    {
      index: 0,
      narration: "欢迎加入团队，我们先了解课程结构。",
      image_prompt: "corporate welcome scene",
      duration: 4,
      visual_url: "https://example.com/frame-0.mp4",
      preview_state: "done",
    },
    {
      index: 1,
      narration: "第一天重点是安全与合规。",
      image_prompt: "safety compliance scene",
      duration: 5,
      visual_url: null,
      preview_state: "done",
    },
  ],
  total_duration: 9,
  audio_ready: true,
};

test("video creation navigates to storyboard and polls until frames land", async ({
  page,
}) => {
  mockLangGraphAPI(page);
  const api = mockVideoAPI(page);

  await page.goto("/workspace");
  await page.locator("[data-course-entry='ai_image']").click();

  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(
    dialog.getByRole("heading", { name: "创建图片动效" }),
  ).toBeVisible();

  await dialog
    .getByPlaceholder(/每一句对应一个画面或镜头/)
    .fill("欢迎加入团队。第一天重点了解安全与合规。");

  const languageTrigger = dialog.getByRole("combobox").nth(2);
  await languageTrigger.click();
  await page.getByRole("option", { name: "中文" }).click();

  const voiceTrigger = dialog.getByRole("combobox").nth(3);
  await voiceTrigger.click();
  await page.getByRole("option", { name: /云健/ }).click();

  await dialog.getByRole("button", { name: /高级设置/ }).click();
  await dialog.getByRole("button", { name: "选择背景音乐" }).click();
  await page.getByRole("option", { name: /清晨钢琴/ }).click();

  await dialog.getByRole("button", { name: "创建并编辑分镜" }).click();

  await expect(page).toHaveURL(
    /\/workspace\/video-gen\/vg-created-e2e\/storyboard$/,
    { timeout: 10_000 },
  );
  await expect(page.getByText("分镜生成中，请稍候…")).toBeVisible({
    timeout: 10_000,
  });

  expect(api.createRequests).toHaveLength(1);
  expect(api.createRequests[0]?.body).toMatchObject({
    video_type: "ai_image",
    mode: "fixed",
    script: "欢迎加入团队。第一天重点了解安全与合规。",
    lang: "zh",
    voice_id: "zh-CN-YunjianNeural",
    bgm: "morning_piano",
  });

  api.upsertStoryboard("vg-created-e2e", STORYBOARD);
  api.setTaskStatus("vg-created-e2e", "ready", { progress: 100 });

  await expect(page.getByText("分镜编辑器")).toBeVisible({
    timeout: 10_000,
  });
  await expect(page.getByText("分镜 1")).toBeVisible();
  await expect(page.getByText("分镜 2")).toBeVisible();
  await expect(
    page.getByText("欢迎加入团队，我们先了解课程结构。"),
  ).toBeVisible();
});
