import { expect, test } from "@playwright/test";

import { csrfHeaders, downloadAndProbe } from "./helpers";

/**
 * L3 图片动效(ai_image):首页能力 #2。真实链路 = 360 生图(seedream-360)+
 * ffmpeg 池合成。API 提交(UI 入口已由 L1 覆盖),产物 mp4 用 ffprobe 验证。
 * POST 需带 X-CSRF-Token(Double Submit)。
 */
test("图片动效:提交→渲染 ready→下载 mp4 可解码", async ({ page }) => {
  test.setTimeout(15 * 60_000);

  const createResp = await page.request.post("/api/video/create", {
    headers: await csrfHeaders(page),
    data: {
      video_type: "ai_image",
      mode: "fixed",
      title: "L3-图片动效冒烟",
      script: "复利让财富随时间生长。越早开始,收获越多。",
      n_scenes: 2,
      seconds_per_frame: 3,
    },
  });
  expect(createResp.ok(), await createResp.text()).toBeTruthy();
  const [first] = (await createResp.json()) as Array<{
    id: string;
    status: string;
  }>;
  const task = first;
  if (!task) throw new Error("create 返回空任务列表");
  expect(task.id).toBeTruthy();
  console.log(`[l3-ai-image] task=${task.id} status=${task.status}`);

  const deadline = Date.now() + 13 * 60_000;
  let status = task.status;
  let error: string | null = null;
  while (Date.now() < deadline) {
    await page.waitForTimeout(10_000);
    const detail = await page.request.get(`/api/video/tasks/${task.id}`);
    if (detail.ok()) {
      const body = (await detail.json()) as {
        status: string;
        progress: number;
        error_message?: string;
      };
      status = body.status;
      error = body.error_message ?? null;
      console.log(`[l3-ai-image] ${status} ${body.progress}%`);
      if (status === "ready" || status === "failed") break;
    }
  }
  expect(status, `任务未在时限内完成: ${error ?? "无错误信息"}`).toBe("ready");

  const dl = await page.request.get(`/api/video/tasks/${task.id}/download`);
  expect(dl.ok(), `下载失败 ${dl.status()}`).toBeTruthy();
  const { file, duration } = await downloadAndProbe(
    page,
    `/api/video/tasks/${task.id}/download`,
    `l3-ai-image-${task.id}.mp4`,
    "video",
  );
  console.log(`[l3-ai-image] PASS 产物=${file} 时长=${duration.toFixed(1)}s`);
  expect(duration).toBeGreaterThan(2);
});
