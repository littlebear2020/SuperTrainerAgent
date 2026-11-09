import type { BaseStream } from "@langchain/langgraph-sdk/react";

import type { AgentThreadState } from "../threads";

import { urlOfArtifact } from "./utils";

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

export function loadArtifactContentFromToolCall({
  url: urlString,
  thread,
}: {
  url: string;
  thread: BaseStream<AgentThreadState>;
}) {
  const url = new URL(urlString);
  const toolCallId = url.searchParams.get("tool_call_id");
  const messageId = url.searchParams.get("message_id");
  if (messageId && toolCallId) {
    const message = thread.messages.find((message) => message.id === messageId);
    if (message?.type === "ai" && message.tool_calls) {
      const toolCall = message.tool_calls.find(
        (toolCall) => toolCall.id === toolCallId,
      );
      if (toolCall) {
        return toolCall.args.content;
      }
    }
  }
}
