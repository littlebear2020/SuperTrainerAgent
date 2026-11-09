import { useQuery } from "@tanstack/react-query";

import { loadModels } from "./api";

export function useModels({ enabled = true }: { enabled?: boolean } = {}) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["models"],
    queryFn: () => loadModels(),
    enabled,
    refetchOnWindowFocus: false,
  });
  return {
    models: data?.models ?? [],
    tokenUsageEnabled: data?.token_usage.enabled ?? false,
    // D3（2026-09-23 批 2）：thread 累计 tokens 告警线（任务类型 → tokens）。
    tokenBudgetWarnTokens: data?.token_budget_warn_tokens ?? {},
    isLoading,
    error,
    // 透出 refetch 供「模型列表加载失败」toast 的重试按钮使用（缺陷 2 路径 A）
    refetch,
  };
}
