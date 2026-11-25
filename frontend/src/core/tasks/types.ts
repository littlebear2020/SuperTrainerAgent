import type { AIMessage } from "@langchain/langgraph-sdk";

import type { SubtaskStep } from "./steps";

/** 写进 store 的一次子任务 patch：不确定的状态不带 status（applySubtaskPatch 过滤 undefined）。 */
export type SubtaskPatch = Partial<Subtask> & { id: string };

export interface Subtask {
  id: string;
  /** cancelled：所属 run 被用户停止 / 中断，任务没有拿到 result（PR3，不是失败）。 */
  status: "in_progress" | "completed" | "failed" | "cancelled";
  subagent_type: string;
  description: string;
  latestMessage?: AIMessage;
  /** PR-3 5.1：live SSE 步骤增量累积（mergeSteps 去重合并，禁 spread）。 */
  steps?: SubtaskStep[];
  prompt: string;
  result?: string;
  error?: string;
}
