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
}: {
  filepath: string;
  threadId: string;
}) {
  let enhancedFilepath = filepath;
  if (filepath.endsWith(".skill")) {
    enhancedFilepath = filepath + "/SKILL.md";
  }
  const url = urlOfArtifact({ filepath: enhancedFilepath, threadId });
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

/** 引用能定位到的工具调用：按 messageId 找到所有同名消息，再以 toolCallId 为主键。 */
function locateWriteFileToolCall({
  ref,
  thread,
}: {
  ref: WriteFileRef;
  thread: BaseStream<AgentThreadState>;
}) {
  for (const message of thread.messages) {
    if (message.id !== ref.messageId || message.type !== "ai") continue;
    const toolCall = message.tool_calls?.find(
      (toolCall) => toolCall.id === ref.toolCallId,
    );
    if (!toolCall) continue;
    if (toolCall.name !== "write_file" && toolCall.name !== "str_replace") {
      return undefined;
    }
    // path 必须与定位到的工具调用一致：引用不能被改成别的文件
    if ((toolCall.args as { path?: unknown })?.path !== ref.path) {
      return undefined;
    }
    return toolCall;
  }
  return undefined;
}

/**
 * 纵切 A 的过渡签名：非 append 的 write_file 直接以 args.content 作为内联快照；
 * 引用非法、缺定位字段或 path 不匹配时返回 undefined，不触发正文渲染。
 * 五态（pending / not_found / failed、append / str_replace 读当前文件）在纵切 B 切换。
 */
export function loadArtifactContentFromToolCall({
  url: urlString,
  thread,
}: {
  url: string;
  thread: BaseStream<AgentThreadState>;
}): string | undefined {
  const ref = parseWriteFileRef(urlString);
  if (!ref) return undefined;
  const toolCall = locateWriteFileToolCall({ ref, thread });
  if (!toolCall) return undefined;
  const content = (toolCall.args as { content?: unknown }).content;
  return typeof content === "string" ? content : undefined;
}
