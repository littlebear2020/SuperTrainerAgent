import type { Message } from "@langchain/langgraph-sdk";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  loadArtifactContent,
  loadArtifactContentFromToolCall,
} from "@/core/artifacts/loader";
import { urlOfArtifact } from "@/core/artifacts/utils";
import { createWriteFileRef } from "@/core/artifacts/write-file-ref";

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://backend",
}));

/**
 * PR4（2026-11-24 前端可靠性批）纵切 B：write-file 引用的五态内容
 * （ready / pending / not_found / invalid / failed）与分工具正文来源。
 *
 * 判定按目标优先：先定位目标 tool call 与它的 result——成功的非 append write_file
 * 直接 ready（args.content 内联快照，空串也是 ready），append / str_replace 成功后 GET
 * 当前文件（明确不是历史调用快照），工具 Error 是 failed。只有目标 call 或 result
 * 缺失时才看加载状态：流式 / history loading / hasMoreHistory=true → pending；全部
 * settled 且 hasMoreHistory=false 后仍缺失才是 not_found。
 */

type Thread = Parameters<typeof loadArtifactContentFromToolCall>[0]["thread"];

const IDS = { messageId: "msg-1", toolCallId: "call-1" };
const PATH = "/mnt/data/report.md";

type Call = {
  id: string;
  name: "write_file" | "str_replace";
  args: Record<string, unknown>;
  /** 流式中：只有原始 chunk、JSON 还不完整 */
  rawArgs?: string;
};

function ai(id: string, calls: Call[], streaming = false): Message {
  const complete = calls.filter((call) => call.rawArgs === undefined);
  const partial = calls.filter((call) => call.rawArgs !== undefined);
  return {
    id,
    type: "ai",
    content: "",
    tool_calls: complete.map((call) => ({
      id: call.id,
      name: call.name,
      args: call.args,
    })),
    ...(streaming || partial.length > 0
      ? {
          tool_call_chunks: partial.map((call) => ({
            id: call.id,
            name: call.name,
            args: call.rawArgs,
          })),
        }
      : {}),
  } as unknown as Message;
}

function toolResult(
  toolCallId: string,
  content: string,
  options: { name?: string; status?: "success" | "error" } = {},
): Message {
  return {
    id: `tool-${toolCallId}`,
    type: "tool",
    tool_call_id: toolCallId,
    name: options.name ?? "write_file",
    content,
    ...(options.status ? { status: options.status } : {}),
  } as unknown as Message;
}

const write = (
  id: string,
  path: string,
  content: string,
  append = false,
): Call => ({
  id,
  name: "write_file",
  args: { description: "d", path, content, append },
});
const replace = (id: string, path: string): Call => ({
  id,
  name: "str_replace",
  args: { description: "d", path, old_str: "a", new_str: "b" },
});

function thread(messages: Message[], isThreadLoading = false): Thread {
  return { messages, isThreadLoading } as unknown as Thread;
}

function ref(
  toolCallId = IDS.toolCallId,
  path = PATH,
  messageId = IDS.messageId,
) {
  const value = createWriteFileRef({ path, messageId, toolCallId });
  if (!value) throw new Error("ref not created");
  return value;
}

async function load(
  url: string,
  t: Thread,
  history: { isHistoryLoading?: boolean; hasMoreHistory?: boolean } = {},
) {
  return loadArtifactContentFromToolCall({
    url,
    thread: t,
    threadId: "t-1",
    isHistoryLoading: history.isHistoryLoading,
    hasMoreHistory: history.hasMoreHistory,
  });
}

/** 第 index 次 fetch 的 URL（mock.calls 的首参是 string | URL | Request）。 */
function requestedUrl(index = 0): string {
  const input = vi.mocked(globalThis.fetch).mock.calls[index]![0];
  if (typeof input === "string") return input;
  return input instanceof URL ? input.href : input.url;
}

const okGet = (content: string) =>
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(content, {
      status: 200,
      headers: { etag: `"${"a".repeat(64)}"` },
    }),
  );

beforeEach(() => {
  window.sessionStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("目标 call 与 result 齐全时直接判定，不看加载状态", () => {
  test("非 append write_file 成功：ready，正文是 args.content 内联快照（空串也是 ready）", async () => {
    const t = thread([
      ai("msg-1", [write("call-1", PATH, "正文", false)]),
      toolResult("call-1", "OK"),
    ]);
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    await expect(
      load(ref(), t, { hasMoreHistory: true, isHistoryLoading: true }),
    ).resolves.toEqual({ status: "ready", content: "正文" });
    expect(fetchSpy).not.toHaveBeenCalled();

    const empty = thread([
      ai("msg-1", [write("call-1", PATH, "", false)]),
      toolResult("call-1", "OK"),
    ]);
    await expect(load(ref(), empty)).resolves.toEqual({
      status: "ready",
      content: "",
    });
  });

  test("目标缺失的加载态与它无关：旧 run 未加载、新 run 在流式都不影响已齐全的目标", async () => {
    // 另一条消息正在流式（半截参数），且 hasMoreHistory=true
    const t = thread(
      [
        ai("msg-0", [
          write("old", PATH, "旧"),
          { ...write("x", PATH, "{"), rawArgs: '{"pa' },
        ]),
        ai("msg-1", [write("call-1", PATH, "正文")]),
        toolResult("call-1", "OK"),
      ],
      true,
    );
    await expect(load(ref(), t, { hasMoreHistory: true })).resolves.toEqual({
      status: "ready",
      content: "正文",
    });
  });
});

describe("分工具正文来源", () => {
  test("初始 write + 同路径 append：引用指向 append 时读当前文件，不以 args.content 冒充", async () => {
    const t = thread([
      ai("msg-1", [
        write("call-1", PATH, "第一段"),
        write("call-2", PATH, "第二段", true),
      ]),
      toolResult("call-1", "OK"),
      toolResult("call-2", "OK"),
    ]);
    const fetchSpy = okGet("第一段\n第二段");
    await expect(load(ref("call-2"), t)).resolves.toEqual({
      status: "ready",
      content: "第一段\n第二段",
    });
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(requestedUrl()).toBe(
      "http://backend/api/threads/t-1/artifacts/mnt/data/report.md",
    );
  });

  test("str_replace 成功：读当前文件", async () => {
    const t = thread([
      ai("msg-1", [write("call-1", PATH, "a c"), replace("call-2", PATH)]),
      toolResult("call-1", "OK"),
      toolResult("call-2", "OK", { name: "str_replace" }),
    ]);
    const fetchSpy = okGet("b c");
    await expect(load(ref("call-2"), t)).resolves.toEqual({
      status: "ready",
      content: "b c",
    });
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  test("同路径连续 toolCall：按 toolCallId 定位，正文对应正确的那次调用", async () => {
    const t = thread([
      ai("msg-1", [
        write("call-1", PATH, "第一次的正文"),
        write("call-2", PATH, "第二次的正文"),
      ]),
      toolResult("call-1", "OK"),
      toolResult("call-2", "OK"),
    ]);
    await expect(load(ref("call-1"), t)).resolves.toEqual({
      status: "ready",
      content: "第一次的正文",
    });
    await expect(load(ref("call-2"), t)).resolves.toEqual({
      status: "ready",
      content: "第二次的正文",
    });
  });

  test("append / str_replace 的 GET 非 OK / 网络异常 → failed:{message}，不让 Promise 逸出异常", async () => {
    const t = thread([
      ai("msg-1", [
        write("call-1", PATH, "底稿"),
        write("call-2", PATH, "追加", true),
        replace("call-3", PATH),
      ]),
      toolResult("call-1", "OK"),
      toolResult("call-2", "OK"),
      toolResult("call-3", "OK", { name: "str_replace" }),
    ]);
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response("nope", { status: 404 }))
      .mockRejectedValueOnce(new TypeError("Failed to fetch"));

    const notOk = await load(ref("call-2"), t);
    expect(notOk.status).toBe("failed");
    expect(notOk).toMatchObject({ message: expect.stringContaining("404") });

    const network = await load(ref("call-3"), t);
    expect(network).toEqual({ status: "failed", message: "Failed to fetch" });
  });
});

describe("工具失败是 failed，不是 ready", () => {
  test("超限被拒（status 缺省 + Error: 文本）：不显示 args.content", async () => {
    const t = thread([
      ai("msg-1", [write("call-1", PATH, "x".repeat(10))]),
      toolResult(
        "call-1",
        "Error: write_file content (90000 bytes) exceeds the 81920-byte single-call limit.",
      ),
    ]);
    const state = await load(ref(), t);
    expect(state.status).toBe("failed");
    expect(state).toMatchObject({
      message: expect.stringContaining("exceeds the 81920-byte"),
    });
  });

  test("str_replace 失败与 ToolMessage.status === 'error' 都是 failed", async () => {
    const t = thread([
      ai("msg-1", [replace("call-1", PATH)]),
      toolResult(
        "call-1",
        "Error: String to replace not found in file: /mnt/data/report.md",
        {
          name: "str_replace",
        },
      ),
    ]);
    expect((await load(ref(), t)).status).toBe("failed");

    const errored = thread([
      ai("msg-1", [write("call-1", PATH, "正文")]),
      toolResult("call-1", "Unexpected error", { status: "error" }),
    ]);
    await expect(load(ref(), errored)).resolves.toEqual({
      status: "failed",
      message: "Unexpected error",
    });
  });
});

describe("目标 call 或 result 缺失时才看加载状态", () => {
  test("流式参数未完整 / SDK history loading / run-history loading / hasMoreHistory=true → pending", async () => {
    // 流式：只有原始 chunk，JSON 还不完整
    const streaming = thread(
      [
        ai(
          "msg-1",
          [{ ...write("call-1", PATH, ""), rawArgs: '{"path":"/mnt/da' }],
          true,
        ),
      ],
      true,
    );
    await expect(load(ref(), streaming)).resolves.toEqual({
      status: "pending",
    });

    // 目标 call 在、result 还没到（流式期间）
    const awaiting = thread(
      [ai("msg-1", [write("call-1", PATH, "正文")])],
      true,
    );
    await expect(load(ref(), awaiting)).resolves.toEqual({ status: "pending" });

    // 目标 call 完全不在消息里：SDK 还在加载首屏
    const sdkLoading = thread([], true);
    await expect(load(ref(), sdkLoading)).resolves.toEqual({
      status: "pending",
    });

    // 自定义 run 历史还在加载 / 仍有未加载的旧 run
    const settled = thread([ai("msg-other", [write("x", PATH, "别的")])]);
    await expect(
      load(ref(), settled, { isHistoryLoading: true }),
    ).resolves.toEqual({
      status: "pending",
    });
    await expect(
      load(ref(), settled, { hasMoreHistory: true }),
    ).resolves.toEqual({
      status: "pending",
    });
  });

  test("chunk 还在、tool_calls 里已有宽松解析出的半截条目：仍是 pending，不是 invalid", async () => {
    // SDK 的部分 JSON 解析会把半截 path 以完整对象的形态放进 tool_calls；原始串还没拼完
    const halfParsed = {
      id: "msg-1",
      type: "ai",
      content: "",
      tool_calls: [
        {
          id: "call-1",
          name: "write_file",
          args: { description: "d", path: "/mnt/data/repo" },
        },
      ],
      tool_call_chunks: [
        {
          id: "call-1",
          name: "write_file",
          args: '{"description":"d","path":"/mnt/data/repo',
        },
      ],
    } as unknown as Message;
    await expect(load(ref(), thread([halfParsed], true))).resolves.toEqual({
      status: "pending",
    });

    // 原始串拼完了就不再算「未完整」：目标在、result 没到
    const parsed = thread([ai("msg-1", [write("call-1", PATH, "正文")])], true);
    await expect(load(ref(), parsed)).resolves.toEqual({ status: "pending" });
  });

  test("参数停在半截、流已经结束（被停止 / 断线 / 输出被截断）：not_found（有重试入口），不是永远 pending", async () => {
    const stuckMessages = [
      ai(
        "msg-1",
        [{ ...write("call-1", PATH, ""), rawArgs: '{"path":"/mnt/da' }],
        true,
      ),
    ];
    await expect(load(ref(), thread(stuckMessages))).resolves.toEqual({
      status: "not_found",
    });
    // 还在流式、或仍有未加载的旧 run：照旧 pending
    await expect(load(ref(), thread(stuckMessages, true))).resolves.toEqual({
      status: "pending",
    });
    await expect(
      load(ref(), thread(stuckMessages), { hasMoreHistory: true }),
    ).resolves.toEqual({ status: "pending" });
  });

  test("全部 settled 且 hasMoreHistory=false 后仍缺失 → not_found", async () => {
    const settled = thread([ai("msg-other", [write("x", PATH, "别的")])]);
    await expect(load(ref(), settled)).resolves.toEqual({
      status: "not_found",
    });
    await expect(
      load(ref(), settled, { isHistoryLoading: false, hasMoreHistory: false }),
    ).resolves.toEqual({
      status: "not_found",
    });
  });
  test("status=success 而正文恰好以 Error: 开头：不算失败——结构化状态优先，文本前缀只在缺 status 时兜底", async () => {
    const t = thread([
      ai("msg-1", [write("call-1", PATH, "正文")]),
      toolResult("call-1", "Error: this file documents an error message", {
        status: "success",
      }),
    ]);
    await expect(load(ref(), t)).resolves.toEqual({
      status: "ready",
      content: "正文",
    });
  });
});

describe("引用非法或 path 不一致 → invalid", () => {
  test("非法引用、缺字段、path 与定位到的 tool call 不一致", async () => {
    const t = thread([
      ai("msg-1", [write("call-1", PATH, "正文")]),
      toolResult("call-1", "OK"),
    ]);
    await expect(load("write-file:/100%.txt", t)).resolves.toEqual({
      status: "invalid",
    });
    await expect(
      load("write-file:?path=%2Fmnt%2Fdata%2Freport.md", t),
    ).resolves.toEqual({ status: "invalid" });
    await expect(
      load(ref(IDS.toolCallId, "/mnt/data/other.md"), t),
    ).resolves.toEqual({
      status: "invalid",
    });
  });
});

describe("raw 路径编码通道（来自 tool args 的原始路径不预解码）", () => {
  test("字面 %20 / %2F / %23 的文件名：真实请求 URL 里的 % 被编码成 %25，仍丢弃 . / .. 段", async () => {
    expect(
      urlOfArtifact({
        filepath: "/mnt/data/a%20b.txt",
        threadId: "t-1",
        raw: true,
      }),
    ).toBe("http://backend/api/threads/t-1/artifacts/mnt/data/a%2520b.txt");
    expect(
      urlOfArtifact({
        filepath: "/mnt/data/x%2Fy#z.md",
        threadId: "t-1",
        raw: true,
      }),
    ).toBe("http://backend/api/threads/t-1/artifacts/mnt/data/x%252Fy%23z.md");
    expect(
      urlOfArtifact({
        filepath: "/mnt/data/../etc/x.txt",
        threadId: "t-1",
        raw: true,
      }),
    ).toBe(
      // `..` 段被过滤（既有口径：不解引用），而不是解析成上级目录
      "http://backend/api/threads/t-1/artifacts/mnt/data/etc/x.txt",
    );
    // artifact list 既有的「已编码路径幂等」语义不变
    expect(
      urlOfArtifact({ filepath: "/mnt/data/%E8%AF%BE.md", threadId: "t-1" }),
    ).toBe("http://backend/api/threads/t-1/artifacts/mnt/data/%E8%AF%BE.md");

    okGet("当前文件");
    await loadArtifactContent({
      filepath: "/mnt/data/a%20b.txt",
      threadId: "t-1",
      raw: true,
    });
    expect(requestedUrl()).toBe(
      "http://backend/api/threads/t-1/artifacts/mnt/data/a%2520b.txt",
    );
  });

  test("append 成功后的 GET 走 raw 通道", async () => {
    const literal = "/mnt/data/100%20x.txt";
    const t = thread([
      ai("msg-1", [
        write("call-1", literal, "a"),
        write("call-2", literal, "b", true),
      ]),
      toolResult("call-1", "OK"),
      toolResult("call-2", "OK"),
    ]);
    okGet("a b");
    await load(ref("call-2", literal), t);
    expect(requestedUrl()).toContain("/mnt/data/100%2520x.txt");
  });
  test("str_replace 命中 .skill 包：按字面路径读当前文件，不追加 /SKILL.md", async () => {
    okGet("PK\u0003\u0004");
    const t = thread([
      ai("msg-1", [replace("call-1", "/mnt/data/pkg.skill")]),
      toolResult("call-1", "OK"),
    ]);
    await expect(
      load(ref("call-1", "/mnt/data/pkg.skill"), t),
    ).resolves.toEqual({
      status: "ready",
      content: "PK\u0003\u0004",
    });
    expect(requestedUrl()).toMatch(/\/artifacts\/mnt\/data\/pkg\.skill$/);
  });
});
