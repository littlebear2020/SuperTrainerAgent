export interface Model {
  id: string;
  name: string;
  model: string;
  display_name: string;
  description?: string | null;
  supports_thinking?: boolean;
  supports_reasoning_effort?: boolean;
}

export interface TokenUsageSettings {
  enabled: boolean;
}

export interface ModelsResponse {
  models: Model[];
  token_usage: TokenUsageSettings;
  /** D3（2026-09-23 批 2）：thread 累计 tokens 告警线（任务类型 → tokens）。 */
  token_budget_warn_tokens?: Record<string, number>;
}
