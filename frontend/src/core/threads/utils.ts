import type { Message } from "@langchain/langgraph-sdk";

import { resolveAgentName } from "@/core/agents";

import type { AgentThread, AgentThreadContext } from "./types";

// Namespaced to match other internal metadata keys so it cannot collide with
// a future feature or a client-supplied key. Keep in sync with the backend
// thread_meta constant (deerflow/persistence/thread_meta/base.py).
export const THREAD_PINNED_METADATA_KEY = "deerflow_pinned";

type ThreadRouteTarget =
  | string
  | {
      thread_id: string;
      context?: Pick<AgentThreadContext, "agent_name"> | null;
      metadata?: Record<string, unknown> | null;
    };

export function agentNameOfThread(
  thread: Exclude<ThreadRouteTarget, string>,
): string | undefined {
  const candidates = [
    thread.context?.agent_name,
    thread.metadata?.agent_name,
    thread.metadata?.project_type,
  ];
  const agentName = candidates.find(
    (candidate): candidate is string =>
      typeof candidate === "string" && candidate.length > 0,
  );
  return agentName ? resolveAgentName(agentName) : undefined;
}

export function pathOfThread(
  thread: ThreadRouteTarget,
  context?: Pick<AgentThreadContext, "agent_name"> | null,
) {
  const threadId = typeof thread === "string" ? thread : thread.thread_id;
  const agentName =
    typeof thread === "string"
      ? agentNameOfThread({ thread_id: thread, context })
      : agentNameOfThread(thread);

  // 2026-09-09 创意工作合并：历史线程 context/metadata 里的旧 5 壳名统一映射到
  // creative-studio，侧边栏/分享/列表链接直接生成新名 URL。
  return agentName
    ? `/workspace/agents/${encodeURIComponent(agentName)}/chats/${threadId}`
    : `/workspace/chats/${threadId}`;
}

export function textOfMessage(message: Message) {
  if (typeof message.content === "string") {
    return message.content;
  } else if (Array.isArray(message.content)) {
    for (const part of message.content) {
      if (part.type === "text") {
        return part.text;
      }
    }
  }
  return null;
}

export function titleOfThread(thread: AgentThread) {
  return thread.values?.title ?? "Untitled";
}

export function isThreadPinned(thread: Pick<AgentThread, "metadata">) {
  return thread.metadata?.[THREAD_PINNED_METADATA_KEY] === true;
}

/** Stable sort: pinned threads first, original relative order preserved. */
export function sortPinnedThreads<T extends Pick<AgentThread, "metadata">>(
  threads: readonly T[],
) {
  return threads
    .map((thread, index) => ({ thread, index }))
    .sort((left, right) => {
      const pinnedDiff =
        Number(isThreadPinned(right.thread)) -
        Number(isThreadPinned(left.thread));
      return pinnedDiff || left.index - right.index;
    })
    .map(({ thread }) => thread);
}
