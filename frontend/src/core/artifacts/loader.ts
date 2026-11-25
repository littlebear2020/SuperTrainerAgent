import type { Message } from "@langchain/langgraph-sdk";
import type { BaseStream } from "@langchain/langgraph-sdk/react";

import type { AgentThreadState } from "../threads";

import { urlOfArtifact } from "./utils";
import { parseWriteFileRef, type WriteFileRef } from "./write-file-ref";

async function sha256OfText(content: string): Promise<string | null> {
  // crypto.subtle is only available in secure contexts (HTTPS or localhost).
  // On an intranet served over plain HTTP, it is undefined. Return null so
  // the caller can degrade gracefully (artifact is viewable but not editable
  // -- hasRevision gates editing on typeof sha256 === "string").
  if (!globalThis.crypto?.subtle) {
    return null;
  }
  const digest = await globalThis.crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(content),
  );
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}

export async function loadArtifactContent({
  filepath,
  threadId,
  raw = false,
}: {
  filepath: string;
  threadId: string;
  /** 来自 tool args 的原始路径：按字面编码，不预解码。 */
  raw?: boolean;
}) {
  let enhancedFilepath = filepath;
  if (filepath.endsWith(".skill")) {
    enhancedFilepath = filepath + "/SKILL.md";
  }
  const url = urlOfArtifact({ filepath: enhancedFilepath, threadId, raw });
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Failed to load artifact: HTTP ${response.status}`);
  }
  const text = await response.text();
  // The text branch of the artifact GET emits an ETag holding the content
  // sha256; prefer it and fall back to a local digest for responses without
  // a SHA-256 ETag. The revision doubles as the PUT optimistic-concurrency
  // baseline (expected_sha256).
  const etag = response.headers.get("etag");
  const sha256 =
    etag?.match(/^"([0-9a-f]{64})"$/)?.[1] ?? (await sha256OfText(text));
  return { content: text, url, sha256 };
}

/** write-file 引用对应的内容五态（PR4）。 */
export type WriteFileContentState =
  | { status: "ready"; content: string }
  | { status: "pending" }
  | { status: "not_found" }
  | { status: "invalid" }
  | { status: "failed"; message: string };

/** 渲染层同步可得的归属信号：hook 据此组 query key，让 pending → ready / failed 能过渡。 */
export type WriteFileTargetProbe =
  | { phase: "invalid" }
  /** 参数还没流式完整（只有 tool_call_chunks） */
  | { phase: "incomplete" }
  /** 参数完整，result 还没到 */
  | { phase: "awaiting-result" }
  /** result 已到位：可以判定 */
  | { phase: "settled" }
  /** 目标 call 不在消息里 */
  | { phase: "missing" };

type ToolCallLike = {
  id?: string;
  name?: string;
  args?: Record<string, unknown>;
};

function toolCallsOf(message: Message): ToolCallLike[] {
  if (message.type !== "ai") return [];
  return (message.tool_calls ?? []) as unknown as ToolCallLike[];
}

/** 流式中的半截参数：tool_call_chunks 里有这个 id，但 tool_calls 里还没有完整参数。 */
function hasIncompleteChunk(message: Message, toolCallId: string): boolean {
  if (message.type !== "ai") return false;
  const chunks = (
    message as unknown as {
      tool_call_chunks?: Array<{ id?: string; args?: string }>;
    }
  ).tool_call_chunks;
  return (
    Array.isArray(chunks) && chunks.some((chunk) => chunk.id === toolCallId)
  );
}

/**
 * 定位目标：以 toolCallId 为主键扫描所有同 messageId 的消息，并校验 path / 工具类型。
 * 不在任何消息里 → missing；只有半截参数 → incomplete。
 */
function probeWriteFileTarget(
  ref: WriteFileRef,
  thread: BaseStream<AgentThreadState>,
):
  | {
      probe: WriteFileTargetProbe;
      toolCall?: ToolCallLike;
      result?: Extract<Message, { type: "tool" }>;
    }
  | undefined {
  let toolCall: ToolCallLike | undefined;
  for (const message of thread.messages) {
    if (message.id !== ref.messageId) continue;
    if (hasIncompleteChunk(message, ref.toolCallId) && !toolCall) {
      toolCall = toolCallsOf(message).find(
        (call) => call.id === ref.toolCallId,
      );
      if (!toolCall) return { probe: { phase: "incomplete" } };
    }
    const call = toolCallsOf(message).find(
      (call) => call.id === ref.toolCallId,
    );
    if (call) toolCall = call;
  }
  if (!toolCall) return { probe: { phase: "missing" } };
  if (toolCall.name !== "write_file" && toolCall.name !== "str_replace") {
    return { probe: { phase: "invalid" } };
  }
  // path 必须与定位到的工具调用一致：引用不能被改成别的文件
  if ((toolCall.args as { path?: unknown })?.path !== ref.path) {
    return { probe: { phase: "invalid" } };
  }
  let result: Extract<Message, { type: "tool" }> | undefined;
  for (const message of thread.messages) {
    if (message.type === "tool" && message.tool_call_id === ref.toolCallId) {
      result = message;
    }
  }
  return {
    probe: { phase: result ? "settled" : "awaiting-result" },
    toolCall,
    result,
  };
}

/** 渲染层用：目标现在处在哪个阶段（不读 result 内容）。 */
export function probeWriteFileRef(
  url: string,
  thread: BaseStream<AgentThreadState>,
): WriteFileTargetProbe {
  const ref = parseWriteFileRef(url);
  if (!ref) return { phase: "invalid" };
  return probeWriteFileTarget(ref, thread)?.probe ?? { phase: "missing" };
}

function resultTextOf(result: Extract<Message, { type: "tool" }>): string {
  const content = result.content;
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((part) =>
        typeof part === "object" && part && "text" in part
          ? String(part.text)
          : "",
      )
      .join("");
  }
  return "";
}

/** 工具失败：ToolMessage.status === "error"，或结果文本以 Error: 开头（含 ToolErrorHandlingMiddleware 包装）。 */
function isToolFailure(result: Extract<Message, { type: "tool" }>): boolean {
  return (
    result.status === "error" ||
    resultTextOf(result).trimStart().startsWith("Error:")
  );
}

async function fetchCurrentFile(
  ref: WriteFileRef,
  threadId: string,
): Promise<WriteFileContentState> {
  try {
    const loaded = await loadArtifactContent({
      filepath: ref.path,
      threadId,
      raw: true,
    });
    return { status: "ready", content: loaded.content };
  } catch (error) {
    // GET 非 OK / 网络异常：捕获为 failed，不让 Promise 逸出异常
    return {
      status: "failed",
      message: error instanceof Error ? error.message : String(error),
    };
  }
}

/**
 * write-file 引用的内容五态（PR4）。判定按目标优先：先定位目标 tool call 与它的
 * result——成功的非 append write_file 直接 ready（args.content 内联快照，空串也是
 * ready）；append=True / str_replace 成功后 GET 当前文件（明确不是历史调用快照）；
 * 工具 Error 是 failed。只有目标 call 或 result 缺失时才看加载状态：流式参数未完整、
 * SDK history loading、自定义 run-history loading 或 hasMoreHistory=true → pending；
 * 全部 settled 且 hasMoreHistory=false 后仍缺失才是 not_found。引用非法或 path 与定位到
 * 的 tool call 不一致是 invalid。不为还原历史版本另写完整文件操作回放器。
 */
export async function loadArtifactContentFromToolCall({
  url: urlString,
  thread,
  threadId,
  isHistoryLoading = false,
  hasMoreHistory = false,
}: {
  url: string;
  thread: BaseStream<AgentThreadState>;
  threadId: string;
  isHistoryLoading?: boolean;
  hasMoreHistory?: boolean;
}): Promise<WriteFileContentState> {
  const ref = parseWriteFileRef(urlString);
  if (!ref) return { status: "invalid" };
  const target = probeWriteFileTarget(ref, thread);
  if (!target) return { status: "invalid" };
  const { probe, toolCall, result } = target;
  if (probe.phase === "invalid") return { status: "invalid" };
  if (probe.phase !== "settled") {
    // 参数没流式完整、result 没到、目标 call 不在消息里：还在流式 / 加载，或仍有未加载
    // 的旧 run 时是 pending（不能判 not_found）；全部 settled 后仍缺失才是 not_found
    return writeFileTargetMayArrive(probe, {
      threadLoading: thread.isThreadLoading || thread.isLoading,
      isHistoryLoading,
      hasMoreHistory,
    })
      ? { status: "pending" }
      : { status: "not_found" };
  }
  if (!toolCall || !result) return { status: "invalid" };
  if (isToolFailure(result)) {
    return { status: "failed", message: resultTextOf(result) };
  }
  const args = toolCall.args as {
    content?: unknown;
    append?: unknown;
  };
  if (toolCall.name === "write_file" && args.append !== true) {
    // 内联快照：就是这次调用写下去的正文
    return {
      status: "ready",
      content: typeof args.content === "string" ? args.content : "",
    };
  }
  // append=True 与 str_replace：读当前文件
  return fetchCurrentFile(ref, threadId);
}

/** 渲染层判定「目标缺失」时该不该等：还在流式 / 加载，或仍有未加载的旧 run。 */
export function writeFileTargetMayArrive(
  probe: WriteFileTargetProbe,
  context: {
    threadLoading: boolean;
    isHistoryLoading?: boolean;
    hasMoreHistory?: boolean;
  },
): boolean {
  if (probe.phase === "incomplete") return true;
  return Boolean(
    context.threadLoading || context.isHistoryLoading || context.hasMoreHistory,
  );
}
