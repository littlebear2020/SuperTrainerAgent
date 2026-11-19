import { test } from "@playwright/test";

import {
  artifactDownloadUrl,
  openAgentChat,
  resolveThreadId,
  sendChatMessage,
  threadIdBaseline,
  validateZipArtifact,
  waitForPresentFiles,
} from "./helpers";

/**
 * L3 PPT 课件(ppt-master agent):首页能力 #5。真实链路 = 360 LLM 生成大纲
 * 与内容 + 沙箱产出 pptx,经 present_files 交付。产物校验 zip 魔数与体积。
 */
test("PPT 课件:agent 交付可下载 pptx", async ({ page }) => {
  // 实测(2026-11-16):ppt-master 沙箱内 SVG→PPTX 制作超 28 分钟仍未完,
  // 预算放大到 50 分钟。
  test.setTimeout(60 * 60_000);

  await openAgentChat(page, "ppt-master");
  const baseline = await threadIdBaseline(page);
  await sendChatMessage(
    page,
    [
      "生成一份 3 页 PPT,主题「新人时间管理入门」,要求:",
      "第1页 封面:标题+副标题;",
      "第2页 三个核心方法:要事优先/番茄工作法/每日复盘;",
      "第3页 行动清单:三个立即执行的步骤。",
      "内容已齐全,直接开始生成并交付 pptx 文件,不要向我提问确认。",
    ].join("\n"),
  );
  const threadId = await resolveThreadId(page, baseline);
  console.log(`[l3-ppt] thread=${threadId}`);

  const filepath = await waitForPresentFiles(
    page,
    threadId,
    /\.(pptx|pdf)$/i,
    50 * 60_000,
  );
  console.log(`[l3-ppt] present_files: ${filepath}`);

  const url = artifactDownloadUrl(threadId, filepath);
  if (/\.pptx$/i.test(filepath)) {
    const bytes = await validateZipArtifact(page, url, "l3-ppt.pptx");
    console.log(
      `[l3-ppt] PASS l3-ppt.pptx ${(bytes / 1024).toFixed(0)}KB(zip 魔数校验通过)`,
    );
  } else {
    const resp = await page.request.get(url);
    if (!resp.ok()) throw new Error(`PDF 下载失败 ${resp.status()}`);
    const buf = await resp.body();
    if (!(buf[0] === 0x25 && buf[1] === 0x50))
      throw new Error("缺 PDF 魔数(%P)");
    console.log(`[l3-ppt] PASS pdf ${(buf.byteLength / 1024).toFixed(0)}KB`);
  }
});
