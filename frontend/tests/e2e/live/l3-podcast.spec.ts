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
 * L3 播客(podcast-studio agent):首页能力 #6。真实链路 = 360 LLM 编排 +
 * 火山方舟豆包 TTS(云舟2.0/Vivi2.0)+ 沙箱 FFmpeg 拼接,经 present_files
 * 交付 MP3。本用例即「TTS 走方舟」的端到端验证点。
 */
test("播客:agent 交付可解码 MP3(方舟 TTS 链路)", async ({ page }) => {
  test.setTimeout(25 * 60_000);

  await openAgentChat(page, "podcast-studio");
  const baseline = await threadIdBaseline(page);
  await sendChatMessage(
    page,
    [
      "把以下文字做成约 60 秒的单人播客 MP3。原文朗读,不要改写,不要添加片头片尾,直接开始,不要向我提问确认:",
      "复利被称为世界第八大奇迹。它指的是收益再投资产生新收益的过程。假设年化收益率百分之七,大约十年,本金就能翻一倍。",
      "越早开始,时间的礼物就越丰厚。与其追逐一夜暴富,不如让时间成为你最好的朋友。投资有风险,长期主义才是真正的答案。",
    ].join("\n"),
  );
  const threadId = await resolveThreadId(page, baseline);
  console.log(`[l3-podcast] thread=${threadId}`);

  const filepath = await waitForPresentFiles(
    page,
    threadId,
    /\.(mp3|wav|m4a)$/i,
    22 * 60_000,
  );
  console.log(`[l3-podcast] present_files: ${filepath}`);

  const { file, duration } = await downloadAndProbe(
    page,
    artifactDownloadUrl(threadId, filepath),
    "l3-podcast.mp3",
    "audio",
  );
  console.log(`[l3-podcast] PASS 产物=${file} 时长=${duration.toFixed(1)}s`);
  if (duration < 20) {
    console.log(
      `[l3-podcast] WARN 时长 ${duration.toFixed(1)}s 偏短(原文约 60s)`,
    );
  }
});
