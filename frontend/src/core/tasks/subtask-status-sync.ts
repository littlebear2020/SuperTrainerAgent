import type { Message } from "@langchain/langgraph-sdk";
import { useEffect } from "react";

import { findToolCallResult } from "@/core/messages/utils";
import { useUpdateSubtask } from "@/core/tasks/context";
import {
  type SubtaskResultUpdate,
  parseSubtaskResult,
} from "@/core/tasks/subtask-result";
import type { Subtask } from "@/core/tasks/types";

/**
 * Derive subtask metadata + status purely from the thread message list.
 *
 * This is the single source of truth for the history-load / page-refresh path,
 * where custom SSE events are NOT replayed. It seeds {subagent_type, description,
 * prompt} from each `task` tool call and resolves status from the matching tool
 * result via parseSubtaskResult(). Called from a useEffect (post-render) so it
 * never mutates React state during render.
 *
 * Deliberately omits latestMessage — that field comes only from streaming
 * task_running events (onCustomEvent); including undefined here would clobber it
 * via the applySubtaskPatch spread on every messages change.
 */
export function deriveSubtasks(messages: Message[]): Subtask[] {
  const out: Subtask[] = [];
  for (const message of messages) {
    if (message.type !== "ai") continue;
    for (const tc of message.tool_calls ?? []) {
      if (tc.name !== "task") continue;
      const id = tc.id;
      if (!id) continue;
      const args = tc.args as {
        subagent_type?: string;
        description?: string;
        prompt?: string;
      };
      const resultText = findToolCallResult(id, messages);
      const parsed: SubtaskResultUpdate =
        resultText !== undefined
          ? parseSubtaskResult(resultText)
          : { status: "in_progress" };
      out.push({
        id,
        subagent_type: args.subagent_type ?? "general-purpose",
        description: args.description ?? "",
        prompt: args.prompt ?? "",
        status: parsed.status,
        // Only include result/error when actually parsed — an in_progress
        // re-derivation (result message not yet in thread.messages) must not
        // emit result:undefined, which would clobber a result already set by a
        // task_completed streaming event via the applySubtaskPatch spread.
        ...(parsed.result !== undefined ? { result: parsed.result } : {}),
        ...(parsed.error !== undefined ? { error: parsed.error } : {}),
      });
    }
  }
  return out;
}

/** Apply derived subtask state to the store whenever messages change. */
export function useSyncSubtaskStatus(messages: Message[]): void {
  const updateSubtask = useUpdateSubtask();
  useEffect(() => {
    for (const task of deriveSubtasks(messages)) {
      updateSubtask(task);
    }
  }, [messages, updateSubtask]);
}
