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
  title: string;
  status: UsageStatus;
  cost: number | null;
  llm_cost: number | null;
  media_cost: number | null;
  currency: string | null;
  cost_source: UsageCostSource;
  total_tokens: number | null;
  models: UsageModel[];
  updated_at: string | null;
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
