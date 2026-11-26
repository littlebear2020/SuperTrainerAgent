import type { WorkCapability } from "@/core/workspace/video-assets-route";

export type UsageDays = 7 | 14 | 30;
export type UsageCapability = WorkCapability;
export type UsageStatus = "completed" | "running" | "incomplete";
export type UsageCostSource = "real" | "estimated" | "mixed" | null;
export type UsageCapabilityFilter = UsageCapability | "all";

export interface UsageModel {
  name: string;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number | null;
  total_tokens: number;
  cost: number | null;
  cost_source: UsageCostSource;
}

export interface UsageTask {
  task_key: string;
  source_id: string;
  capability: UsageCapability;
  title: string | null;
  status: UsageStatus;
  cost: number | null;
  llm_cost: number | null;
  media_cost: number | null;
  currency: string | null;
  cost_source: UsageCostSource;
  total_tokens: number | null;
  models: UsageModel[];
  updated_at: string | null;
  /** 作品/会话已删除：历史费用仍计入、原任务不可再打开。旧后端不带此字段，按 false 处理。 */
  deleted?: boolean;
}

export interface PersonalUsageResponse {
  window: { days: UsageDays; start_date: string; end_date: string };
  summary: {
    cost: number | null;
    currency: string | null;
    cost_source: UsageCostSource;
    total_tokens: number | null;
  };
  tasks: UsageTask[];
  total_tasks: number;
  has_more: boolean;
}
