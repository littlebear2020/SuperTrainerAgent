import type { Message, Run } from "@langchain/langgraph-sdk";
import { useEffect } from "react";

import {
  findToolCallResult,
  isClarificationToolMessage,
} from "@/core/messages/utils";
import { useUpdateSubtask } from "@/core/tasks/context";
import {
  type SubtaskResultUpdate,
  parseSubtaskResult,
} from "@/core/tasks/subtask-result";
import type { Subtask } from "@/core/tasks/types";

/** 一个 run 的终态判定输入（PR3）：每次只处理一个 run 的消息和该 run 的状态。 */
export type DeriveSubtasksOptions = {
  /** 这批消息所属的 run 在加载时的状态；缺省 = 旧调用方，无 result 一律 in_progress。 */
  runStatus?: Run["status"];
  /** 旧历史还在加载：不下发终态判定。 */
  isHistoryLoading?: boolean;
  /** 流已断开但服务端的 run 未必结束：不下发终态判定。 */
  isRecoverableDisconnect?: boolean;
};

/**
 * 没有 tool result 的 `task` 调用按所属 run 的终态判定。
 *
 * - success → completed；error / timeout → failed（runs API 没有 error 字段，
 *   不虚构错误详情）；interrupted（用户停止）→ cancelled，不是 failed；
 * - pending / running → in_progress；
 * - 澄清不是 interrupted：`success` + 未回答的 `ask_clarification`（写入后
 *   `goto=END`）的 run 保持 in_progress，等用户回答后的下一个 run；
 * - history loading / 断线：返回 undefined，调用方不下发 patch——既不判 failed，
 *   也不会用 in_progress 覆盖旧 run 已经落下的终态。
 */
export function statusWithoutResult(
  messages: Message[],
  options: DeriveSubtasksOptions,
): Subtask["status"] | undefined {
  if (options.isHistoryLoading || options.isRecoverableDisconnect) {
    return undefined;
  }
  const runStatus = options.runStatus;
  if (runStatus === undefined) return "in_progress";
  if (runStatus === "success") {
    // 澄清把 run 正常收尾在等待回答上：没有 result 的任务并未完成
    return messages.some(isClarificationToolMessage)
      ? "in_progress"
      : "completed";
  }
  if (runStatus === "error" || runStatus === "timeout") return "failed";
  if (runStatus === "interrupted") return "cancelled";
  return "in_progress";
}

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
 *
 * PR3：`messages` 应当只是**一个 run** 的消息，`options.runStatus` 是该 run 的状态；
 * 没有 result 的任务据此判定终态（见 statusWithoutResult）。不传 options 保持原行为。
 * 判定为 undefined（history loading / 断线）的任务不下发 patch。
 */
export function deriveSubtasks(
  messages: Message[],
  options: DeriveSubtasksOptions = {},
): Subtask[] {
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
      let parsed: SubtaskResultUpdate;
      if (resultText !== undefined) {
        parsed = parseSubtaskResult(resultText);
      } else {
        const status = statusWithoutResult(messages, options);
        if (status === undefined) continue;
        parsed = { status };
      }
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

/** 扁平消息列表 → 按 run 判定所需的归属信息（PR3）。 */
export type RunAttribution = {
  /** 历史消息 id → 所属 run（useThreadHistory 的 messageRunIds）。 */
  messageRunIds: ReadonlyMap<string, string>;
  /** runs 列表（run_id → run），按加载时 / 最近一次刷新的状态判定。 */
  runsById: Readonly<Record<string, Run>>;
  /** 当前流所属的 run；不在 messageRunIds 里的消息都属于它。缺省 = 不归属任何 run。 */
  activeRunId?: string | null;
};

/**
 * 扁平的消息列表按 run 分别判定：旧 run 的终态不被当前 run 改写。
 * `findToolCallResult` 需要看到同 run 的 result 消息，所以先把消息按 run 归组、
 * 再逐 run 调 deriveSubtasks；输出顺序与消息在列表里的先后一致。
 */
export function deriveSubtasksByRun(
  messages: Message[],
  attribution: RunAttribution,
  options: Omit<DeriveSubtasksOptions, "runStatus"> = {},
): Subtask[] {
  const byRun = new Map<string, Message[]>();
  for (const message of messages) {
    const historyRunId = message.id
      ? attribution.messageRunIds.get(message.id)
      : undefined;
    const runId = historyRunId ?? attribution.activeRunId ?? "";
    const bucket = byRun.get(runId);
    if (bucket) bucket.push(message);
    else byRun.set(runId, [message]);
  }
  const derived = new Map<string, Subtask>();
  for (const [runId, runMessages] of byRun) {
    const runStatus = attribution.runsById[runId]?.status;
    for (const task of deriveSubtasks(runMessages, { ...options, runStatus })) {
      derived.set(task.id, task);
    }
  }
  // 保持消息顺序：按 tool call 在列表里的出现顺序输出
  const out: Subtask[] = [];
  for (const message of messages) {
    if (message.type !== "ai") continue;
    for (const tc of message.tool_calls ?? []) {
      const task = tc.id ? derived.get(tc.id) : undefined;
      if (task) out.push(task);
    }
  }
  return out;
}

/**
 * Apply derived subtask state to the store whenever messages change.
 * 给了 run 归属就按 run 判定；没给（agents/new 等调用方）保持原行为。
 */
export function useSyncSubtaskStatus(
  messages: Message[],
  attribution?: {
    messageRunIds?: ReadonlyMap<string, string>;
    runsById?: Readonly<Record<string, Run>>;
    activeRunId?: string | null;
  },
  options?: Omit<DeriveSubtasksOptions, "runStatus">,
): void {
  const updateSubtask = useUpdateSubtask();
  const messageRunIds = attribution?.messageRunIds;
  const runsById = attribution?.runsById;
  const activeRunId = attribution?.activeRunId ?? null;
  const isHistoryLoading = options?.isHistoryLoading;
  const isRecoverableDisconnect = options?.isRecoverableDisconnect;
  useEffect(() => {
    // 归属信息是 hook 里 memo 过的稳定引用；缺任何一项都退回不分 run 的原行为
    const tasks =
      messageRunIds && runsById
        ? deriveSubtasksByRun(
            messages,
            { messageRunIds, runsById, activeRunId },
            { isHistoryLoading, isRecoverableDisconnect },
          )
        : deriveSubtasks(messages, {
            isHistoryLoading,
            isRecoverableDisconnect,
          });
    for (const task of tasks) {
      updateSubtask(task);
    }
  }, [
    messages,
    updateSubtask,
    messageRunIds,
    runsById,
    activeRunId,
    isHistoryLoading,
    isRecoverableDisconnect,
  ]);
}
