import { test } from "@playwright/test";

import {
  artifactDownloadUrl,
  downloadAndProbe,
  openAgentChat,
  resolveThreadId,
  sendChatMessage,
  threadIdBaseline,
  waitForPresentFiles,
} from "./helpers";

/**
 * L3 课程视频(course-video agent):首页能力 #1,最重的一条。真实链路 =
 * 360 LLM 撰稿 + auto-video-flow 沙箱 Remotion 渲染。agent 工作流带需求
 * 澄清环节,轮询期间自动应答,直到 present_files 交付 mp4。
 */
test("课程视频:agent 全流程交付可解码 mp4", async ({ page }) => {
  // 实测(2026-11-16,4C/7.5G 测试机):沙箱 Remotion 渲染 900 帧约 39 分钟,
  // 36 分钟预算会以 3 分钟之差误报失败——预算放大到 55 分钟。
  test.setTimeout(60 * 60_000);

  await openAgentChat(page, "course-video");
  const baseline = await threadIdBaseline(page);
  await sendChatMessage(
    page,
    [
      "制作一个 30 秒垃圾分类科普短视频,需求已全部明确,直接开始制作,不要向我提问确认:",
      "画幅 16:9;风格简洁明快;中文旁白,女声;",
      "旁白共三句:",
      "1. 垃圾分类,让城市更干净。",
      "2. 可回收物包括纸张、塑料、金属和玻璃。",
      "3. 厨余垃圾请单独投放,变废为宝。",
      "全程不需要我补充任何信息,请一次性完成并交付成片。",
    ].join("\n"),
  );
  const threadId = await resolveThreadId(page, baseline);
  console.log(`[l3-course-video] thread=${threadId}`);

  const filepath = await waitForPresentFiles(
    page,
    threadId,
    /\.mp4$/i,
    55 * 60_000,
  );
  console.log(`[l3-course-video] present_files: ${filepath}`);

  const { file, duration } = await downloadAndProbe(
    page,
    artifactDownloadUrl(threadId, filepath),
    "l3-course-video.mp4",
    "video",
  );
  console.log(`[l3-course-video] PASS mp4=${file} ${duration.toFixed(1)}s`);
  if (duration < 10) throw new Error(`成片过短: ${duration}s`);
});
