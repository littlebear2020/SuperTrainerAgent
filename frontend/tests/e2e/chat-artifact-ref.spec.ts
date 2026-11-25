/**
 * PR4（2026-11-24 前端可靠性批）：write-file 引用的行为验收（mock API）。
 *
 * 引用把 path 作为 query 参数编码，特殊文件名（`% # ?` 空格）也能定位到那次工具
 * 调用。流式里参数是一段段到的：半截 path 不构造引用、不自动打开；参数完整后才
 * 自动选中，正文是 args.content 的内联快照。append / str_replace 读的是文件当前
 * 内容（GET），工具失败显示错误而不是空文件，合法的空文件不被误报。
 *
 * 流式用例用真实 SSE 服务保持连接，帧序与真实服务端一致：metadata → values（用户
 * 消息入库）→ messages（AIMessageChunk 片段，SDK 自己拼接——`tool_calls` 是宽松解析
 * 的结果，半截 path 也在里面）→ 工具结果 → values → end。
 */

import { expect, test, type Page } from "@playwright/test";

import {
  startHeldOpenStreamServer,
  type HeldOpenStreamServer,
} from "./utils/held-open-stream";
import { pinEnglishLocale } from "./utils/locale";
import {
  MOCK_THREAD_ID,
  mockLangGraphAPI,
  runStreamThreadId,
  type MockRun,
} from "./utils/mock-api";

const COMPOSER = "[data-testid='composer-input']";
const SUBMIT = "button[aria-label='Submit']";
const NEW_CHAT_URL = /\/workspace\/chats\/[0-9a-f-]{36}$/;
/** 详情里的四态说明（pending / not_found / invalid / failed）；ready 时不存在。 */
const NOTICE = "[data-testid='write-file-notice']";
const PENDING_NOTICE = "Loading the content of this write…";
const FAILED_NOTICE = "This write failed";

const RUN_ID = "run-wf-1";
const CALL_ID = "call-wf-1";
const MSG_ID = "msg-wf-1";
const TOOL_MSG_ID = "tool-wf-1";
const HUMAN_MSG_ID = "human-wf-1";
const SPECIAL_PATH = "/mnt/data/100% final#1?.md";
const INLINE_BODY = "INLINE-BODY-7f3a 这就是这次写入的正文";
const PROMPT = "把报告写成文件";
const SPECIAL_ARGS = JSON.stringify({
  description: "写报告",
  path: SPECIAL_PATH,
  content: INLINE_BODY,
});
/** 在 path 中间、`%` 之后切开：前半截 `"path":"/mnt/data/100% "`。 */
const CUT = SPECIAL_ARGS.indexOf("100% fi") + 4;

type Msg = Record<string, unknown>;
type ToolCall = { id: string; name: string; args: Record<string, unknown> };

const human = (id: string, text: string): Msg => ({
  type: "human",
  id,
  content: [{ type: "text", text }],
});
const ai = (id: string, toolCalls: ToolCall[]): Msg => ({
  type: "ai",
  id,
  content: "",
  tool_calls: toolCalls,
});
const tool = (id: string, toolCallId: string, content: string): Msg => ({
  type: "tool",
  id,
  tool_call_id: toolCallId,
  name: "write_file",
  content,
});

function sseFrame(event: string, data: unknown) {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

const STREAM_META = { run_id: RUN_ID, langgraph_step: 1 };

/** `messages-tuple` 事件：一段 AIMessageChunk，工具参数是原始 JSON 串的一部分。 */
function toolCallChunkFrame(args: string, withIdentity = false) {
  return sseFrame("messages", [
    {
      content: "",
      additional_kwargs: {},
      response_metadata: {},
      type: "AIMessageChunk",
      name: null,
      id: MSG_ID,
      // 后续片段不带 id / name，只靠 index 归并（与模型服务端的流一致）
      tool_call_chunks: [
        {
          ...(withIdentity ? { name: "write_file", id: CALL_ID } : {}),
          args,
          index: 0,
          type: "tool_call_chunk",
        },
      ],
    },
    STREAM_META,
  ]);
}

function toolResultFrame(content: string) {
  return sseFrame("messages", [
    {
      content,
      name: "write_file",
      id: TOOL_MSG_ID,
      tool_call_id: CALL_ID,
      status: "success",
      type: "ToolMessageChunk",
    },
    { ...STREAM_META, langgraph_step: 2 },
  ]);
}

function trackPageErrors(page: Page) {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  return errors;
}

/** 产物正文的 GET（路径，去掉查询串）。 */
function trackArtifactGets(page: Page) {
  const paths: string[] = [];
  page.on("request", (request) => {
    if (request.method() !== "GET") return;
    const { pathname } = new URL(request.url());
    if (/\/api\/threads\/[^/]+\/artifacts\//.test(pathname))
      paths.push(pathname);
  });
  return paths;
}

/** 把提交的 run stream 桥接到保持连接的 SSE 服务（响应头回显 Content-Location）。 */
async function bridgeSubmitStream(page: Page, server: HeldOpenStreamServer) {
  // SDK 的提交 URL 可能带查询串，glob 末尾要放开
  await page.route("**/api/langgraph/threads/*/runs/stream**", (route) => {
    const threadId = runStreamThreadId(route);
    return route.continue({
      url: `${server.url}/threads/${threadId}/runs/${RUN_ID}`,
    });
  });
}

test.describe("write-file references", () => {
  test("a streamed write_file with a special filename: no reference while the path is half, pending once complete, ready when the result lands", async ({
    page,
  }) => {
    // run 在流结束前不出现在 runs 列表里（否则历史会先于流到达）
    const options: {
      runs: MockRun[];
      runMessages: Record<string, never[]>;
    } = { runs: [], runMessages: {} };
    mockLangGraphAPI(page, options);
    await pinEnglishLocale(page);
    const pageErrors = trackPageErrors(page);
    const gets = trackArtifactGets(page);
    const server = await startHeldOpenStreamServer({
      echoContentLocation: true,
    });
    let serverClosed = false;
    try {
      await bridgeSubmitStream(page, server);
      await page.goto("/workspace/chats/new");
      await page.locator(COMPOSER).fill(PROMPT);
      await page.locator(SUBMIT).click();
      // onCreated：页面翻到新线程地址，流已开始
      await expect(page).toHaveURL(NEW_CHAT_URL, { timeout: 10_000 });
      const stopIcon = page.locator(`${SUBMIT} svg.lucide-square`);
      await expect(stopIcon).toBeVisible();

      server.writeFrame(sseFrame("metadata", { run_id: RUN_ID }));
      // 等流真正开始消费再发后续帧：同一批写入的首帧可能早于 SDK 挂上监听
      await page.waitForTimeout(300);
      server.writeFrame(
        sseFrame("values", { messages: [human(HUMAN_MSG_ID, PROMPT)] }),
      );
      await expect(page.getByText(PROMPT, { exact: true })).toBeVisible();

      // 半截参数：步骤已出现（默认标签），但没有 path、没有引用、没有自动打开
      server.writeFrame(toolCallChunkFrame(SPECIAL_ARGS.slice(0, CUT), true));
      const halfStep = page.getByText("Write file", { exact: true });
      await expect(halfStep).toBeVisible({ timeout: 10_000 });
      // 自动打开的延迟是 100ms：等过了这个窗口仍然什么都没选
      await page.waitForTimeout(400);
      await expect(page.getByText(SPECIAL_PATH)).toHaveCount(0);
      await expect(page.locator(NOTICE)).toHaveCount(0);
      await expect(page.getByText("INLINE-BODY-7f3a")).toHaveCount(0);

      // 参数完整：自动选中并打开；result 还没到 → pending
      server.writeFrame(toolCallChunkFrame(SPECIAL_ARGS.slice(CUT)));
      await expect(page.getByText("写报告", { exact: true })).toBeVisible();
      await expect(page.getByText(SPECIAL_PATH, { exact: true })).toBeVisible();
      await expect(page.locator(NOTICE)).toContainText(PENDING_NOTICE);
      await expect(page.getByText("INLINE-BODY-7f3a")).toHaveCount(0);

      // result 到位（流仍在进行）：不等 onFinish 就是 ready，正文是这次写入的内联快照
      server.writeFrame(toolResultFrame("OK"));
      await expect(page.getByText("INLINE-BODY-7f3a")).toBeVisible({
        timeout: 10_000,
      });
      await expect(page.locator(NOTICE)).toHaveCount(0);

      // 收尾：values 给出完整状态，run 进列表供 onFinish 的强制重取
      options.runs.push({ run_id: RUN_ID });
      const complete: Msg[] = [
        human(HUMAN_MSG_ID, PROMPT),
        ai(MSG_ID, [
          {
            id: CALL_ID,
            name: "write_file",
            args: JSON.parse(SPECIAL_ARGS) as Record<string, unknown>,
          },
        ]),
        tool(TOOL_MSG_ID, CALL_ID, "OK"),
      ];
      options.runMessages[RUN_ID] = complete as never[];
      server.writeFrame(sseFrame("values", { messages: complete }));
      server.writeFrame(sseFrame("end", {}));
      // SDK 以响应体结束为准收尾流
      await server.close();
      serverClosed = true;
      await expect(stopIcon).toHaveCount(0, { timeout: 10_000 });
      // 流结束、历史重取之后正文仍然在
      await expect(page.getByText("INLINE-BODY-7f3a")).toBeVisible();
      // 非 append 的写入不读文件
      expect(gets).toEqual([]);
      expect(pageErrors).toEqual([]);
    } finally {
      if (!serverClosed) await server.close();
    }
  });

  test("append and str_replace read the current file; a failed write shows the error", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      runs: [{ run_id: "run-1", thread_id: MOCK_THREAD_ID }],
      runMessages: {
        "run-1": [
          human("h-1", "整理报告"),
          ai("a-1", [
            {
              id: "call-append",
              name: "write_file",
              args: {
                description: "追加写入",
                path: "/mnt/data/report.md",
                content: "追加的片段",
                append: true,
              },
            },
          ]),
          tool("t-1", "call-append", "OK"),
          ai("a-2", [
            {
              id: "call-replace",
              name: "str_replace",
              args: {
                description: "替换段落",
                path: "/mnt/data/report.md",
                old_str: "旧段落",
                new_str: "新段落",
              },
            },
          ]),
          tool("t-2", "call-replace", "OK"),
          ai("a-3", [
            {
              id: "call-fail",
              name: "write_file",
              args: {
                description: "出错的那次",
                path: "/mnt/data/huge.md",
                content: "x".repeat(10),
              },
            },
          ]),
          tool("t-3", "call-fail", "Error: File content exceeds the limit"),
        ] as never[],
      },
      artifacts: {
        "/mnt/data/report.md": "CURRENT-FILE-9c2d 这是文件现在的内容",
      },
    });
    await pinEnglishLocale(page);
    const pageErrors = trackPageErrors(page);
    const gets = trackArtifactGets(page);

    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
    await expect(page.getByText("整理报告", { exact: true })).toBeVisible({
      timeout: 15_000,
    });
    // 同一组里的前几步默认折叠在「N more steps」后面，没展开时根本不在 DOM 里
    const expand = page.getByRole("button", { name: /\d+ more steps?/i });
    await expect(expand).toBeVisible();
    await expand.click();
    await expect(page.getByText("追加写入", { exact: true })).toBeVisible();
    // append：读当前文件，不显示这次追加的片段
    await page.getByText("追加写入", { exact: true }).click();
    await expect(page.getByText("CURRENT-FILE-9c2d")).toBeVisible();
    await expect(page.getByText("追加的片段")).toHaveCount(0);
    await expect(page.locator(NOTICE)).toHaveCount(0);

    // str_replace：同样读当前文件
    await page.getByText("替换段落", { exact: true }).click();
    await expect(page.getByText("CURRENT-FILE-9c2d")).toBeVisible();
    expect(
      gets.filter((path) => path.endsWith("/artifacts/mnt/data/report.md")),
    ).toHaveLength(2);

    // 工具失败：显示错误，不当成空文件，也不读文件
    await page.getByText("出错的那次", { exact: true }).click();
    const notice = page.locator(NOTICE);
    await expect(notice).toContainText(FAILED_NOTICE);
    await expect(notice).toContainText("exceeds the limit");
    await expect(page.getByText("CURRENT-FILE-9c2d")).toHaveCount(0);
    expect(gets).toHaveLength(2);
    expect(pageErrors).toEqual([]);
  });

  test("a successful write of an empty file is ready and empty, not misreported", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      runs: [{ run_id: "run-1", thread_id: MOCK_THREAD_ID }],
      runMessages: {
        "run-1": [
          human("h-1", "建一个空文件"),
          ai("a-1", [
            {
              id: "call-empty",
              name: "write_file",
              // .txt 走代码编辑器（.md 会渲染成预览），空正文可以直接断言
              args: {
                description: "写了个空文件",
                path: "/mnt/data/empty.txt",
                content: "",
              },
            },
          ]),
          tool("t-1", "call-empty", "OK"),
        ] as never[],
      },
    });
    await pinEnglishLocale(page);
    const pageErrors = trackPageErrors(page);
    const gets = trackArtifactGets(page);

    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
    const step = page.getByText("写了个空文件", { exact: true });
    await expect(step).toBeVisible({ timeout: 15_000 });
    await step.click();

    // ready 才会挂编辑器；pending / not_found / failed 都是说明文字
    const editor = page.locator(".cm-content");
    await expect(editor).toBeVisible();
    await expect(editor).toHaveText("");
    await expect(page.locator(NOTICE)).toHaveCount(0);
    expect(gets).toEqual([]);
    expect(pageErrors).toEqual([]);
  });
});
