import { execFile } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";

import type { Page } from "@playwright/test";

/** Live 套件公共工具:agent 聊天驱动 / 流式等待 / 澄清自动应答 / 产物下载校验。 */

export const CHAT_PLACEHOLDER = "今天我能为你做些什么？";

// ClarificationOptions 容器签名(className="flex flex-col items-start gap-1.5"),
// :enabled 过滤掉运行中置灰的选项。
const CLARIFICATION_BUTTONS = "div.flex.flex-col.items-start button:enabled";

const ARTIFACT_DIR = path.join(process.cwd(), "test-results", "live-artifacts");

/** 打开某 agent 的新会话页并等待输入框就绪。 */
export async function openAgentChat(page: Page, agent: string) {
  await page.goto(`/workspace/agents/${agent}/chats/new`);
  await page.getByPlaceholder(CHAT_PLACEHOLDER).waitFor({ timeout: 30_000 });
}

/** 在当前会话发送一条消息(Enter 发送)。 */
export async function sendChatMessage(page: Page, text: string) {
  const box = page.getByPlaceholder(CHAT_PLACEHOLDER);
  await box.fill(text);
  await box.press("Enter");
}

/** 流式进行中的可视化锚点(三点动画)。 */
function streamingDots(page: Page) {
  return page.locator(".animate-bouncing");
}

/**
 * 等待一次回复完成:等动画出现(可能很快)再等它消失;未观察到动画也可能
 * 回复已完成,交由调用方断言内容。返回是否观察到动画。
 */
export async function waitForRunSettled(page: Page, timeoutMs = 180_000) {
  const sawDots = await streamingDots(page)
    .first()
    .waitFor({ timeout: 20_000 })
    .then(() => true)
    .catch(() => false);
  if (sawDots) {
    await streamingDots(page)
      .first()
      .waitFor({ state: "detached", timeout: timeoutMs });
  }
  // 动画消失后再宽限,给最终消息/文件卡片渲染留时间。
  await page.waitForTimeout(1_500);
  return sawDots;
}

/**
 * agent 会话期间自动应答 ask_clarification:优先点含"确认/开始/直接"的选项,
 * 无匹配点第一个。返回本轮是否处理过澄清。
 */
export async function answerClarificationIfPresent(
  page: Page,
): Promise<boolean> {
  const candidates = page.locator(CLARIFICATION_BUTTONS);
  const count = await candidates.count().catch(() => 0);
  if (!count) return false;
  const preferred = candidates.filter({ hasText: /确认|开始|直接|同意|继续/ });
  const target = (await preferred.count())
    ? preferred.first()
    : candidates.first();
  await target.click();
  await page.waitForTimeout(2_000);
  return true;
}

/** 等待聊天里出现指定扩展名的可下载产物链接,返回其 href(期间自动应答澄清)。 */
export async function waitForArtifactLink(
  page: Page,
  extPattern: RegExp,
  timeoutMs = 20 * 60_000,
): Promise<string> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const hrefs = await page
      .locator("a[href]")
      .evaluateAll((els) => els.map((el) => (el as HTMLAnchorElement).href));
    const hit = hrefs.find((href) => extPattern.test(href));
    if (hit) return hit;
    await answerClarificationIfPresent(page).catch(() => undefined);
    await page.waitForTimeout(5_000);
  }
  throw new Error(
    `等待产物链接超时(${extPattern}),${timeoutMs / 1000}s 内未出现`,
  );
}

/** 下载产物到本地并用 ffprobe 校验(返回时长秒;probeType 校验流类型)。 */
export async function downloadAndProbe(
  page: Page,
  url: string,
  name: string,
  probeType?: "audio" | "video",
): Promise<{ file: string; duration: number }> {
  const resp = await page.request.get(url);
  if (!resp.ok()) {
    throw new Error(`产物下载失败 ${resp.status()}: ${url}`);
  }
  const buf = await resp.body();
  if (buf.byteLength < 2048) {
    throw new Error(`产物过小(${buf.byteLength}B),疑似占位/损坏: ${url}`);
  }
  await mkdir(ARTIFACT_DIR, { recursive: true });
  const file = path.join(ARTIFACT_DIR, name);
  await writeFile(file, buf);
  const run = promisify(execFile);
  const args = [
    "-v",
    "error",
    "-show_entries",
    "format=duration",
    "-of",
    "csv=p=0",
  ];
  if (probeType)
    args.push("-select_streams", probeType === "audio" ? "a:0" : "v:0");
  args.push(file);
  const { stdout } = await run("ffprobe", args).catch((error: Error) => {
    throw new Error(`ffprobe 失败: ${error.message}(文件 ${file})`);
  });
  const duration = Number.parseFloat(stdout.trim());
  if (!Number.isFinite(duration) || duration <= 0) {
    throw new Error(`ffprobe 时长异常: "${stdout.trim()}"(文件 ${file})`);
  }
  return { file, duration };
}

/** 校验 pptx(zip 容器)文件头与最小体积,返回字节数。 */
export async function validateZipArtifact(
  page: Page,
  url: string,
  name: string,
) {
  const resp = await page.request.get(url);
  if (!resp.ok()) throw new Error(`产物下载失败 ${resp.status()}: ${url}`);
  const buf = await resp.body();
  if (buf.byteLength < 20_480) {
    throw new Error(`PPT 产物过小(${buf.byteLength}B),疑似空壳`);
  }
  if (!(buf[0] === 0x50 && buf[1] === 0x4b)) {
    throw new Error("PPT 产物不是 zip 容器(缺 PK 魔数)");
  }
  await mkdir(ARTIFACT_DIR, { recursive: true });
  await writeFile(path.join(ARTIFACT_DIR, name), buf);
  return buf.byteLength;
}

/** 从上下文 cookie 取 csrf_token(Double Submit),供 API POST 用。 */
export async function csrfHeaders(page: Page): Promise<Record<string, string>> {
  const cookies = await page.context().cookies();
  const token = cookies.find((c) => c.name.includes("csrf"))?.value;
  if (!token)
    throw new Error("上下文里找不到 csrf cookie(应先经登录态建 context)");
  return { "X-CSRF-Token": token };
}

/** 从当前页面 URL 提取线程 ID(chats/<id> 或 agents/<a>/chats/<id>)。 */
export function threadIdFromUrl(url: string): string {
  const m = /chats\/([0-9a-zA-Z-]+)/.exec(url);
  if (!m) throw new Error(`URL 里没有线程 ID: ${url}`);
  return m[1]!;
}

/** 发送消息前的线程 ID 基线(用于事后识别新线程)。 */
export async function threadIdBaseline(page: Page): Promise<Set<string>> {
  const headers = await csrfHeaders(page);
  const resp = await page.request.post("/api/threads/search", {
    headers,
    data: { limit: 20 },
  });
  if (!resp.ok()) throw new Error(`threads/search 失败 ${resp.status()}`);
  const list = (await resp.json()) as Array<{ thread_id: string }>;
  return new Set(list.map((t) => t.thread_id));
}

/**
 * 解析刚创建线程的真实 ID:优先等 URL 落成 chats/<id>(id≠new),
 * 超时则回退查线程列表里基线外的新线程(agent 页流式期间 URL 可能停在 /new)。
 */
export async function resolveThreadId(
  page: Page,
  baseline: Set<string>,
  timeoutMs = 90_000,
): Promise<string> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const m = /chats\/([0-9a-zA-Z-]+)/.exec(page.url());
    const fromUrl = m?.[1];
    if (fromUrl && fromUrl !== "new") return fromUrl;
    try {
      const headers = await csrfHeaders(page);
      const resp = await page.request.post("/api/threads/search", {
        headers,
        data: { limit: 10 },
      });
      if (resp.ok()) {
        const list = (await resp.json()) as Array<{ thread_id: string }>;
        const fresh = list.find((t) => !baseline.has(t.thread_id));
        if (fresh) return fresh.thread_id;
      }
    } catch {
      // 查询失败继续等
    }
    await page.waitForTimeout(5_000);
  }
  throw new Error(`未能解析新线程 ID(url=${page.url()})`);
}

type HistoryEntry = {
  // 消息挂在首个 checkpoint 的 values.messages 下(不是顶层 messages)。
  values?: {
    messages?: Array<{
      type?: string;
      tool_calls?: Array<{
        name?: string;
        args?: { filepaths?: unknown };
      }>;
    }>;
  };
};

/**
 * 轮询线程 history 直到 present_files 交付匹配扩展名的文件,返回其 filepath。
 * 期间自动应答聊天页可能出现的澄清选项。比扫 DOM 锚点可靠:产物有时只以
 * 文本列出、不渲染 <a>,而 history 是权威数据源。
 */
export async function waitForPresentFiles(
  page: Page,
  threadId: string,
  extPattern: RegExp,
  timeoutMs = 20 * 60_000,
): Promise<string> {
  const headers = await csrfHeaders(page);
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const resp = await page.request.post(`/api/threads/${threadId}/history`, {
      headers,
      data: {},
    });
    if (resp.ok()) {
      const entries = (await resp.json()) as HistoryEntry[];
      const messages =
        entries.find((e) => Array.isArray(e.values?.messages))?.values
          ?.messages ?? [];
      for (const msg of messages) {
        for (const call of msg.tool_calls ?? []) {
          if (call.name !== "present_files") continue;
          const paths = Array.isArray(call.args?.filepaths)
            ? (call.args.filepaths as unknown[])
            : [];
          const hit = paths.find(
            (p) => typeof p === "string" && extPattern.test(p),
          ) as string | undefined;
          if (hit) return hit;
        }
      }
    }
    await answerClarificationIfPresent(page).catch(() => undefined);
    await page.waitForTimeout(8_000);
  }
  throw new Error(
    `等待 present_files 交付超时(${extPattern}),${timeoutMs / 1000}s`,
  );
}

/** 产物下载 URL(对齐 urlOfArtifact:/api/threads/<id>/artifacts/<path>?download=true)。 */
export function artifactDownloadUrl(
  threadId: string,
  filepath: string,
): string {
  const encoded = filepath
    .replace(/^\/+/, "")
    .split("/")
    .map(encodeURIComponent)
    .join("/");
  return `/api/threads/${threadId}/artifacts/${encoded}?download=true`;
}
