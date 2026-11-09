/**
 * Types for the operations console (`/api/console/*`).
 *
 * Mirrors the pydantic response models in
 * `backend/app/gateway/routers/console.py`. Cost fields are nullable on
 * purpose: a deployment with no `models[*].pricing` configured reports
 * `null` rather than `0`, because `0` would read as "this was free".
 */

/** Who the caller is and how wide the numbers in the response are. */
export interface ConsoleScope {
  is_admin: boolean;
  /** True when the response covers every user rather than a single one. */
  all_users: boolean;
  /** The user the response is scoped to, or null for the platform-wide view. */
  user_id: string | null;
}

export interface ConsoleCostBreakdown {
  llm: number | null;
  media: number;
}

export interface ConsoleStats {
  total_runs: number;
  active_runs: number;
  failed_runs: number;
  // total_threads / total_agents were removed from the API (A4, 2026-08-04):
  // total_agents reported the platform-wide custom-agent count to every
  // caller regardless of scope, and neither field was ever rendered here.
  total_tokens: number;
  /** Legacy LLM-only (chat runs) spend — kept for rollback (A12). */
  total_cost: number | null;
  /** C3: full-chain spend (chat + llm_bookkeeping + media) for the window. */
  total_cost_all?: number | null;
  cost_breakdown?: ConsoleCostBreakdown;
  /** C5: billable media calls with no pricing row. */
  unpriced_calls?: number;
  currency: string | null;
  /** Runs whose token usage crossed the attention threshold (T20). */
  over_threshold_runs: number;
  scope: ConsoleScope;
}

export interface ConsoleRun {
  run_id: string;
  thread_id: string;
  thread_title: string | null;
  assistant_id: string | null;
  /** Populated for admin callers only. */
  user_id: string | null;
  /** Populated for admin callers only. */
  user_email: string | null;
  status: string;
  model_name: string | null;
  created_at: string | null;
  updated_at: string | null;
  duration_seconds: number | null;
  total_tokens: number;
  subagent_tokens: number;
  message_count: number;
  cost: number | null;
  error: string | null;
  stop_reason: string | null;
  over_threshold: boolean;
}

export interface ConsoleRunsPage {
  runs: ConsoleRun[];
  has_more: boolean;
  scope: ConsoleScope;
}

export interface ConsoleUsageDay {
  date: string;
  total_tokens: number;
  input_tokens: number;
  output_tokens: number;
  runs: number;
  /** B1: chat-only tokens (operation_kind=run). Falls back to total_tokens for old backends. */
  chat_tokens?: number;
  /** B1: llm_bookkeeping tokens (video-gen, vimax, writer, etc.). */
  llm_tokens?: number;
  /** B1: number of llm_bookkeeping calls on this day. */
  llm_calls?: number;
  /** Null when the day's runs are unpriced — same "unknown, not free" rule as `total_cost`. */
  cost: number | null;
}

export interface ConsoleUsageModel {
  tokens: number;
  runs: number;
  cost: number | null;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  /** B1: non-conversation LLM calls (llm_bookkeeping) that used this model. */
  llm_calls?: number;
}

export interface ConsoleUsage {
  days: ConsoleUsageDay[];
  by_model: Record<string, ConsoleUsageModel>;
  total_tokens: number;
  total_runs: number;
  /** B1: chat-only tokens. Falls back to total_tokens for old backends. */
  total_chat_tokens?: number;
  /** B1: llm_bookkeeping tokens. */
  total_llm_tokens?: number;
  /** B1: total non-conversation LLM calls in the window. */
  total_llm_calls?: number;
  total_cost: number | null;
  /** C3: full-chain spend (LLM + media) for the window. */
  total_cost_all?: number | null;
  cost_breakdown?: ConsoleCostBreakdown;
  /** C5: billable media calls with no pricing row. */
  unpriced_calls?: number;
  /** C4: per-provider {real, estimated} split for coloring / 估 marker. */
  media_cost_by_provider_split?: Record<
    string,
    { real: number; estimated: number }
  >;
  currency: string | null;
  subagent_tokens: number;
  scope: ConsoleScope;
}

export interface ConsoleUser {
  user_id: string;
  email: string | null;
  system_role: string | null;
  total_runs: number;
  total_tokens: number;
}

export interface ConsoleUsersResponse {
  users: ConsoleUser[];
}

// C2: Non-conversation LLM usage

export interface ConsoleLlmUsageDomain {
  domain: string;
  calls: number;
  failed_calls: number;
  tokens: number;
  input_tokens: number;
  output_tokens: number;
  cost: number | null;
}

export interface ConsoleLlmUsageResponse {
  domains: ConsoleLlmUsageDomain[];
  total_cost: number | null;
  currency: string | null;
  scope: ConsoleScope;
}

// F2/C3: External API call counts and costs

export interface ConsoleExternalCallsProvider {
  provider: string;
  calls: number;
  cost_real: number | null;
  cost_estimated: number | null;
  cost: number | null;
  priced: boolean;
  unit: string | null;
  /** C6: provider→type mapping served by the backend (replaces the frontend's hardcoded Sets). */
  media_type?: string;
}

export interface ConsoleExternalCallsDailyItem {
  date: string;
  calls: number;
  cost_real: number;
  cost_estimated: number;
}

export interface ConsoleExternalCallsTypeItem {
  media_type: string;
  calls: number;
  cost: number | null;
}

export interface ConsoleExternalCallsResponse {
  total_calls: number;
  by_provider: ConsoleExternalCallsProvider[];
  by_date: ConsoleExternalCallsDailyItem[];
  by_type: ConsoleExternalCallsTypeItem[];
  unpriced_calls: number;
  scope: ConsoleScope;
}

// C2: Media pricing

export interface MediaPricingEntry {
  provider: string;
  /** Model id this row prices; null = provider-level default row (C2). */
  model_id: string | null;
  media_type: string;
  unit: string;
  unit_price: number;
  currency: string;
  updated_at: string | null;
  updated_by: string;
  source: "db" | "default";
}

export interface MediaPricingResponse {
  pricing: MediaPricingEntry[];
}

/** PUT body shape — only editable fields, no audit/source metadata. */
export interface MediaPricingUpdateEntry {
  provider: string;
  model_id: string | null;
  media_type: string;
  unit: string;
  unit_price: number;
  currency: string;
}

export interface MediaPricingReplaceRequest {
  pricing: MediaPricingUpdateEntry[];
  /** C7: acknowledge >5x unit_price changes on existing keys (backend 422s otherwise). */
  confirm?: boolean;
}

/** C7: single-row upsert (PATCH) — same guards as the full-table PUT. */
export interface MediaPricingPatchRequest {
  entry: MediaPricingUpdateEntry;
  confirm?: boolean;
}

/** Sortable columns on the run list — must match `_RUN_SORT_COLUMNS` server-side. */
export type ConsoleRunSortKey =
  | "created_at"
  | "updated_at"
  | "total_tokens"
  | "status";

export type SortDirection = "asc" | "desc";

// ---------------------------------------------------------------------------
// Model pricing (A0/A1)
// ---------------------------------------------------------------------------

/** One model's effective prices, with provenance. */
export interface ModelPricingEntry {
  currency: string;
  input_per_million: number;
  output_per_million: number;
  /** null bills cache hits at the miss price; 0 = hits are free. */
  input_cache_hit_per_million: number | null;
  updated_at: string | null;
  updated_by: string;
  /** "db" = operator-set via the admin panel; "yaml" = fallback from config.yaml. */
  source: "db" | "yaml";
}

export interface ModelPricingResponse {
  pricing: Record<string, ModelPricingEntry>;
}

export interface ModelPricingUpdateRequest {
  pricing: Record<
    string,
    {
      currency: string;
      input_per_million: number;
      output_per_million: number;
      input_cache_hit_per_million: number | null;
    }
  >;
}
