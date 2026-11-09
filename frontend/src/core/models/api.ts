import { getBackendBaseURL } from "../config";

import type { ModelsResponse } from "./types";

export async function loadModels(): Promise<ModelsResponse> {
  const res = await fetch(`${getBackendBaseURL()}/api/models`);
  // 非 2xx 必须抛错进 react-query 的 error 态——否则 JSON 错误体会被下面的
  // `data.models ?? []` 静默变成空数组，调用方连 error 都拿不到（缺陷 2 路径 A
  // 的根因：模型列表加载失败 = 无声空白，用户无从重试）。
  if (!res.ok) {
    throw new Error(`Failed to load models: HTTP ${res.status}`);
  }
  const data = (await res.json()) as Partial<ModelsResponse>;
  return {
    models: data.models ?? [],
    token_usage: data.token_usage ?? { enabled: false },
    token_budget_warn_tokens: data.token_budget_warn_tokens ?? {},
  };
}
