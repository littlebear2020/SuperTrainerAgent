/**
 * write-file 引用（PR4，2026-11-24 前端可靠性批）。
 *
 * 旧格式把文件路径放进 URL pathname：`%` / `#` / `?` 一类字符会被截断，或让
 * `decodeURIComponent` 抛错，引用就再也定位不到那次工具调用。新格式把 path 作为独立
 * 的 query 参数编码；合法的旧格式仍可解析，截断 / 非法的值按无效处理，不猜文件名。
 */

import type { Message } from "@langchain/langgraph-sdk";

export const WRITE_FILE_REF_PREFIX = "write-file:";

export interface WriteFileRef {
  path: string;
  messageId: string;
  toolCallId: string;
}

/** 三个定位字段任一缺失即不生成引用（不生成含 `undefined` 的引用）。 */
export function createWriteFileRef({
  path,
  messageId,
  toolCallId,
}: {
  path: string | undefined;
  messageId: string | undefined;
  toolCallId: string | undefined;
}): string | null {
  if (!path || !messageId || !toolCallId) return null;
  // URLSearchParams 把 path 作为独立参数编码：空格、`/`、`%` 都不会再被 URL 结构吞掉
  const params = new URLSearchParams({
    path,
    message_id: messageId,
    tool_call_id: toolCallId,
  });
  return `${WRITE_FILE_REF_PREFIX}?${params.toString()}`;
}

/** 不是合法 percent 编码的 `%`（后面没跟两位十六进制）。 */
const INVALID_PERCENT = /%(?![0-9A-Fa-f]{2})/;

/**
 * query 里的键值：值不解码（用来在校验之前发现非法的 percent 编码），键按 URLSearchParams
 * 的规则解码——否则 `p%61th` 这种编码过的键在两套解析里会对不上。
 */
function rawParams(search: string): Map<string, string> {
  const out = new Map<string, string>();
  for (const pair of search.replace(/^\?/, "").split("&")) {
    if (!pair) continue;
    const eq = pair.indexOf("=");
    const rawKey = eq === -1 ? pair : pair.slice(0, eq);
    const value = eq === -1 ? "" : pair.slice(eq + 1);
    let key = rawKey;
    try {
      key = decodeURIComponent(rawKey.replace(/\+/g, " "));
    } catch {
      // 键不是合法的 percent 编码：按原样查，不会命中任何已知键 → 按无效处理
    }
    if (!out.has(key)) out.set(key, value);
  }
  return out;
}

/**
 * 解析 write-file 引用。支持新格式（`write-file:?path=…&message_id=…&tool_call_id=…`）
 * 与合法的旧格式（path 在 pathname，只解码一次）；缺定位字段、非法 percent、旧路径已
 * 截断时返回 null。不抛异常。
 */
export function parseWriteFileRef(value: string): WriteFileRef | null {
  if (!value.startsWith(WRITE_FILE_REF_PREFIX)) return null;
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  if (url.protocol !== WRITE_FILE_REF_PREFIX) return null;

  const raw = rawParams(url.search);
  const rawMessageId = raw.get("message_id");
  const rawToolCallId = raw.get("tool_call_id");
  const rawPath = raw.get("path");
  if (!rawMessageId || !rawToolCallId) return null;
  if (
    INVALID_PERCENT.test(rawMessageId) ||
    INVALID_PERCENT.test(rawToolCallId) ||
    (rawPath !== undefined && INVALID_PERCENT.test(rawPath))
  ) {
    return null;
  }
  const messageId = url.searchParams.get("message_id");
  const toolCallId = url.searchParams.get("tool_call_id");
  if (!messageId || !toolCallId) return null;

  if (rawPath !== undefined) {
    // 新格式：URLSearchParams 只解码一次（%2520 → 字面的 %20）
    const path = url.searchParams.get("path");
    if (!path) return null;
    return { path, messageId, toolCallId };
  }

  // 旧格式：path 在 pathname（保留它原有的形态——带或不带开头的 /），只解码一次；
  // 截断或非法的值不可恢复
  if (!url.pathname || url.pathname === "/") return null;
  let path: string;
  try {
    path = decodeURIComponent(url.pathname);
  } catch {
    return null;
  }
  if (!path) return null;
  return { path, messageId, toolCallId };
}

const WRITE_FILE_TOOL_NAMES = new Set(["write_file", "str_replace"]);

/** write_file / str_replace：会写出文件、需要靠引用定位到这次调用的两类工具。 */
export function isWriteFileToolCall(name: string | undefined): boolean {
  return name !== undefined && WRITE_FILE_TOOL_NAMES.has(name);
}

// ── 流式工具参数 ──────────────────────────────────────────────────────────────
// UI 侧（工具步骤要不要生成引用）与内容加载侧（五态判定）共用下面这一份「参数是否
// 完整」的定义，两边口径一致才不会出现「步骤可点、详情永远等待」。

/** 严格解析原始参数串；不是合法的 JSON 对象时返回 undefined（数组也不是工具参数）。 */
function parseToolArgs(raw: string): Record<string, unknown> | undefined {
  try {
    const parsed: unknown = JSON.parse(raw);
    return parsed !== null &&
      typeof parsed === "object" &&
      !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : undefined;
  } catch {
    return undefined;
  }
}

/**
 * 流式中的工具参数原始串（按 toolCallId）：`tool_call_chunks` 里这个调用的 args 片段
 * 拼起来。SDK 交付的消息字典里 chunk 已按 index 归并（后续片段不带 id / name 也并进了
 * 同一条），所以每个 id 对应一条；没有 chunk 的历史消息返回空 Map。
 */
export function rawArgsByToolCallId(message: Message): Map<string, string> {
  const out = new Map<string, string>();
  if (message.type !== "ai") return out;
  const chunks = (message as { tool_call_chunks?: unknown }).tool_call_chunks;
  if (!Array.isArray(chunks)) return out;
  for (const chunk of chunks) {
    if (!chunk || typeof chunk !== "object") continue;
    const { id, args } = chunk as { id?: unknown; args?: unknown };
    if (typeof id !== "string" || !id || typeof args !== "string") continue;
    out.set(id, (out.get(id) ?? "") + args);
  }
  return out;
}

/** 参数是否已完整：原始串能被严格解析成 JSON 对象（流式中的半截串解析失败）。 */
export function isCompleteToolArgs(raw: string): boolean {
  return parseToolArgs(raw) !== undefined;
}

/**
 * 工具步骤该用的参数。原始串还在流式（JSON 不完整）时返回空对象：不显示 path、
 * 不构造引用；`settled`（工具已有 result）或没有原始串（历史消息）时参数已定型，
 * 直接用 `args`——SDK 宽松解析的结果。模型产出的 args 本身就是非法 JSON 时严格解析
 * 永远不会成功：result 到了还要求严格 JSON，会话内就永远点不了（刷新后却能打开）。
 */
export function completeToolArgs(
  args: Record<string, unknown> | undefined,
  rawArgs: string | undefined,
  settled: boolean,
): Record<string, unknown> {
  if (rawArgs === undefined || settled) {
    return args && typeof args === "object" ? args : {};
  }
  return parseToolArgs(rawArgs) ?? {};
}
