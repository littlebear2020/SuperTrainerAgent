import type { AIMessage } from "@langchain/langgraph-sdk";

import type { SubtaskStep } from "./steps";

export interface Subtask {
  id: string;
  status: "in_progress" | "completed" | "failed";
  subagent_type: string;
  description: string;
  latestMessage?: AIMessage;
  /** PR-3 5.1：live SSE 步骤增量累积（mergeSteps 去重合并，禁 spread）。 */
  steps?: SubtaskStep[];
  prompt: string;
  result?: string;
  error?: string;
}
