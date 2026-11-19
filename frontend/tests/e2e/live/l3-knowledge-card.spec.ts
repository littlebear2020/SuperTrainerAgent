import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

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
 * L3 知识卡片(creative-studio agent):首页能力 #4。真实链路 = 360 LLM 规划
 * + Aio 沙箱(fba6cf98 新镜像首次真实拉起)+ 卡片渲染,经 present_files 交付。
 * 断言走线程 history(权威数据源),不依赖 DOM 锚点渲染。
 */
test("知识卡片:agent 交付图片产物", async ({ page }) => {
  test.setTimeout(25 * 60_000);

  await openAgentChat(page, "creative-studio");
  const baseline = await threadIdBaseline(page);
  await sendChatMessage(
    page,
    [
      "请做一张知识卡片,信息如下,已齐全,直接开始,不要向我提问确认:",
      "标题:《复利:时间的朋友》",
      "要点1:复利 = 收益再投资产生新收益",
      "要点2:年化 7% 约十年本金翻倍",
      "要点3:越早开始,时间杠杆越长",
      "风格:tech-blue,输出静态卡片图即可。",
    ].join("\n"),
  );
  const threadId = await resolveThreadId(page, baseline);
  console.log(`[l3-knowledge-card] thread=${threadId}`);

  const filepath = await waitForPresentFiles(
    page,
    threadId,
    /\.(png|svg|mp4)$/i,
    22 * 60_000,
  );
  console.log(`[l3-knowledge-card] present_files: ${filepath}`);
  const url = artifactDownloadUrl(threadId, filepath);

  if (/\.mp4$/i.test(filepath)) {
    const { file, duration } = await downloadAndProbe(
      page,
      url,
      "l3-knowledge-card.mp4",
      "video",
    );
    console.log(`[l3-knowledge-card] PASS mp4=${file} ${duration.toFixed(1)}s`);
  } else {
    const resp = await page.request.get(url);
    if (!resp.ok()) throw new Error(`产物下载失败 ${resp.status()}`);
    const buf = await resp.body();
    if (buf.byteLength < 4096) throw new Error(`图片过小 ${buf.byteLength}B`);
    if (/\.png$/i.test(filepath)) {
      if (!(buf[0] === 0x89 && buf[1] === 0x50)) throw new Error("缺 PNG 魔数");
    } else if (!(buf.includes(Buffer.from("<svg")) || buf[0] === 0x3c)) {
      throw new Error("SVG 内容异常");
    }
    await mkdir(path.join(process.cwd(), "test-results", "live-artifacts"), {
      recursive: true,
    });
    await writeFile(
      path.join(
        process.cwd(),
        "test-results",
        "live-artifacts",
        `l3-knowledge-card${path.extname(filepath)}`,
      ),
      buf,
    );
    console.log(
      `[l3-knowledge-card] PASS ${filepath} ${(buf.byteLength / 1024).toFixed(0)}KB`,
    );
  }
});
