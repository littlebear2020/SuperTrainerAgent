import type { Message } from "@langchain/langgraph-sdk";
import type { BaseStream } from "@langchain/langgraph-sdk/react";

import type { AgentThreadState } from "../threads";

import { urlOfArtifact } from "./utils";
import {
  isCompleteToolArgs,
  isWriteFileToolCall,
  parseWriteFileRef,
  rawArgsByToolCallId,
  type WriteFileRef,
} from "./write-file-ref";

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
  // raw（write-file 引用读当前文件）：按字面路径读，不把 .skill 包当成 SKILL.md
  if (!raw && filepath.endsWith(".skill")) {
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

type ToolMessageLike = Extract<Message, { type: "tool" }>;

/**
 * 定位结果：目标还没定下来时只有阶段；settled 时一定带着工具调用与它的 result
 * （按 phase 收窄的联合类型，调用方不必再判空）。
 */
type WriteFileTarget =
  | { phase: Exclude<WriteFileTargetProbe, { phase: "settled" }>["phase"] }
  | {
      phase: "settled";
      toolCall: ToolCallLike;
      result: ToolMessageLike;
    };

function toolCallsOf(message: Message): ToolCallLike[] {
  if (message.type !== "ai") return [];
  return (message.tool_calls ?? []) as unknown as ToolCallLike[];
}

/**
 * 定位目标：以 toolCallId 为主键扫描所有同 messageId 的消息，并校验 path / 工具类型。
 * 一遍扫描同时找工具调用、它的 result 与流式参数的原始串（流式期间 thread 每个 chunk
 * 都是新引用，这个函数会跟着重跑）。不在任何消息里 → missing；参数还在流式 → incomplete。
 */
function probeWriteFileTarget(
  ref: WriteFileRef,
  thread: BaseStream<AgentThreadState>,
): WriteFileTarget {
  let toolCall: ToolCallLike | undefined;
  let result: ToolMessageLike | undefined;
  let raw: string | undefined;
  for (const message of thread.messages) {
    // result 可能排在 AI 消息之后，任何位置都算
    if (message.type === "tool" && message.tool_call_id === ref.toolCallId) {
      result = message;
    }
    if (message.id !== ref.messageId) continue;
    raw ??= rawArgsByToolCallId(message).get(ref.toolCallId);
    const call = toolCallsOf(message).find(
      (call) => call.id === ref.toolCallId,
    );
    if (call) toolCall = call;
  }
  // 参数还在流式且 result 没到：SDK 的宽松解析已经把半截 path 放进了 tool_calls，不能
  // 拿它去校验 path（那会闪现「引用无效」）；原始串能严格解析了才算到齐。result 到了
  // 参数就已定型，直接用 tool_calls 里的 args——与工具步骤（completeToolArgs）同口径
  if (!result && raw !== undefined && !isCompleteToolArgs(raw)) {
    return { phase: "incomplete" };
  }
  if (!toolCall) return { phase: "missing" };
  if (!isWriteFileToolCall(toolCall.name)) return { phase: "invalid" };
  // path 必须与定位到的工具调用一致：引用不能被改成别的文件
  if ((toolCall.args as { path?: unknown })?.path !== ref.path) {
    return { phase: "invalid" };
  }
  if (!result) return { phase: "awaiting-result" };
  return { phase: "settled", toolCall, result };
}

/** 渲染层用：目标现在处在哪个阶段（不读 result 内容）。 */
export function probeWriteFileRef(
  url: string,
  thread: BaseStream<AgentThreadState>,
): WriteFileTargetProbe {
  const ref = parseWriteFileRef(url);
  if (!ref) return { phase: "invalid" };
  return probeWriteFileTarget(ref, thread);
}

function resultTextOf(result: ToolMessageLike): string {
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

/**
 * 工具失败的结果文本前缀。两类出处：
 * - `ToolErrorHandlingMiddleware` 把工具异常包成 `Error: …` 的 ToolMessage 并置
 *   `status="error"`（agents/middlewares/tool_error_handling_middleware.py）；
 * - write_file / str_replace 自己 catch 后直接返回 `Error: …` 文本、**不带 status**
 *   （sandbox/tools.py）——对这些工具，文本前缀才是主要信号。
 * 两个信号任一成立即判失败。后端改包装格式时同步。
 */
const TOOL_ERROR_PREFIX = "Error:";

/**
 * 工具失败：`ToolMessage.status === "error"`，或 status 缺失时结果文本以工具错误前缀
 * 开头；status 是 success 的结果正文恰好以 `Error:` 开头（比如文件内容）不算失败。
 */
function isToolFailure(result: ToolMessageLike): boolean {
  if (result.status === "error") return true;
  if (result.status !== undefined) return false;
  return resultTextOf(result).trimStart().startsWith(TOOL_ERROR_PREFIX);
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
  if (target.phase === "invalid") return { status: "invalid" };
  if (target.phase !== "settled") {
    // 参数没流式完整、result 没到、目标 call 不在消息里：还在流式 / 加载，或仍有未加载
    // 的旧 run 时是 pending（不能判 not_found）；全部 settled 后仍缺失才是 not_found
    return writeFileTargetMayArrive({
      threadLoading: thread.isThreadLoading || thread.isLoading,
      isHistoryLoading,
      hasMoreHistory,
    })
      ? { status: "pending" }
      : { status: "not_found" };
  }
  // settled：目标调用与 result 都在（类型已收窄）
  const { toolCall, result } = target;
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

/**
 * 渲染层判定「目标缺失」时该不该等：还在流式 / 加载，或仍有未加载的旧 run。
 * 参数停在半截（流被停止 / 断线 / 输出被截断）也一样——全部 settled 后这次写入不会再有
 * 下文，是 not_found（带重试入口），不是永远 pending。
 */
export function writeFileTargetMayArrive(context: {
  threadLoading: boolean;
  isHistoryLoading?: boolean;
  hasMoreHistory?: boolean;
}): boolean {
  return (
    context.threadLoading ||
    (context.isHistoryLoading ?? false) ||
    (context.hasMoreHistory ?? false)
  );
}
