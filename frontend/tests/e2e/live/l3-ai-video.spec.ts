import { expect, test } from "@playwright/test";

import { csrfHeaders, downloadAndProbe } from "./helpers";

/**
 * L3 AI 视频(ai_video):首页能力 #3。真实链路 = 360 网关 seedance 视频模型
 * + ffmpeg 池合成。POST 需带 X-CSRF-Token(Double Submit)。
 */
test("AI 视频:提交→渲染 ready→下载 mp4 可解码", async ({ page }) => {
  test.setTimeout(20 * 60_000);

  const createResp = await page.request.post("/api/video/create", {
    headers: await csrfHeaders(page),
    data: {
      video_type: "ai_video",
      mode: "fixed",
      title: "L3-AI视频冒烟",
      script: "清晨的城市缓缓苏醒。阳光洒在安静的街道上。",
      n_scenes: 2,
      seconds_per_frame: 4,
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
  console.log(`[l3-ai-video] task=${task.id} status=${task.status}`);

  const deadline = Date.now() + 18 * 60_000;
  let status = task.status;
  let error: string | null = null;
  while (Date.now() < deadline) {
    await page.waitForTimeout(15_000);
    const detail = await page.request.get(`/api/video/tasks/${task.id}`);
    if (detail.ok()) {
      const body = (await detail.json()) as {
        status: string;
        progress: number;
        error_message?: string;
      };
      status = body.status;
      error = body.error_message ?? null;
      console.log(`[l3-ai-video] ${status} ${body.progress}%`);
      if (status === "ready" || status === "failed") break;
    }
  }
  expect(status, `任务未在时限内完成: ${error ?? "无错误信息"}`).toBe("ready");

  const { file, duration } = await downloadAndProbe(
    page,
    `/api/video/tasks/${task.id}/download`,
    `l3-ai-video-${task.id}.mp4`,
    "video",
  );
  console.log(`[l3-ai-video] PASS 产物=${file} 时长=${duration.toFixed(1)}s`);
  expect(duration).toBeGreaterThan(2);
});
