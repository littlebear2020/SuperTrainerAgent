import type { AIMessage } from "@langchain/langgraph-sdk";

import { messageToStep } from "./steps";
import type { Subtask } from "./types";

/**
 * Map a backend `task` custom SSE event to a subtask patch, or null if unknown.
 *
 * The backend `task` tool (tools/builtins/task_tool.py) emits these via the
 * stream writer. Only `task_running` was previously handled on the frontend;
 * the terminal events were dropped, leaving every ultra subtask card spinning
 * forever regardless of outcome.
 */
export function mapTaskEvent(
  event: unknown,
): (Partial<Subtask> & { id: string }) | null {
  if (typeof event !== "object" || event === null || !("type" in event)) {
    return null;
  }
  const e = event as { type: string; task_id?: string; [k: string]: unknown };
  const id = typeof e.task_id === "string" ? e.task_id : null;
  if (!id) return null;

  switch (e.type) {
    case "task_running":
      // 1.5（PR-1 throttle 配套）：钉 in_progress——useStream throttle 合帧下
      // custom 事件可先于 store 通知到达，若先建出无 status 条目，后续 derive 的
      // in_progress patch 会被 applySubtaskPatch 终态守卫误删（existing.status
      // === undefined ≠ "in_progress" 命中守卫分支），子任务卡全程无转圈。
      // 5.1（PR-3）：同分支叠加 steps 单条 delta（backend message_index 1-based，
      // 与 messageToStep 输入逐字段一致）——消费端 applySubtaskPatch 走 mergeSteps
      // 按 message_index 去重合并，每条事件只带这一条，禁 spread。
      const patch: Partial<Subtask> & { id: string } = {
        id,
        status: "in_progress",
        latestMessage: e.message as AIMessage,
      };
      if (typeof e.message_index === "number" && e.message) {
        patch.steps = [
          messageToStep(
            e.message as Parameters<typeof messageToStep>[0],
            e.message_index,
          ),
        ];
      }
      return patch;
    case "task_completed":
      return {
        id,
        status: "completed",
        result: typeof e.result === "string" ? e.result : undefined,
      };
    case "task_failed":
    case "task_timed_out":
    case "task_cancelled":
      return {
        id,
        status: "failed",
        error: typeof e.error === "string" ? e.error : e.type,
      };
    default:
      return null;
  }
}
