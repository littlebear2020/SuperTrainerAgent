/**
 * Thread token-usage API + hook（D3 修订，2026-09-23 review #4）。
 *
 * 徽章总量与超线判定改读 `GET /api/threads/{thread_id}/token-usage`——与后端
 * D3 告警同一聚合源（runs 表 thread 全历史 `aggregate_tokens_by_thread`），
 * 消除「已加载分页窗口 < 阈值 → 超线长会话徽章永不变红」的口径偏差。
 *
 * 拉取时机契约（守 DEF-06 零损耗红线）：仅挂载时与 run 结束（refreshKey 递增）
 * 各一次，禁止按 token / 定时轮询；失败或不可用静默回落消息内聚合
 * （`accumulateUsage`，仅覆盖已加载窗口）。
 */

import { useEffect, useState } from "react";

import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

export interface ThreadTokenUsage {
  threadId: string;
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
}

/** 字段命名与 TokenUsage（core/messages/usage）对齐，徽章/tooltip 可直接共用。 */
export async function fetchThreadTokenUsage(
  threadId: string,
): Promise<ThreadTokenUsage | null> {
  try {
    const res = await fetch(
      `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}/token-usage`,
    );
    if (!res.ok) return null;
    const data = (await res.json()) as {
      thread_id?: string;
      total_tokens?: number;
      total_input_tokens?: number;
      total_output_tokens?: number;
    };
    // 无 run 记录（新线程）或字段缺失 → null，回落消息聚合
    if (typeof data.total_tokens !== "number") return null;
    return {
      threadId: data.thread_id ?? threadId,
      inputTokens: data.total_input_tokens ?? 0,
      outputTokens: data.total_output_tokens ?? 0,
      totalTokens: data.total_tokens,
    };
  } catch {
    return null; // 观测性拉取：失败静默回落，不打扰用户
  }
}

/**
 * 挂载 + refreshKey 变化时各拉一次；无轮询。
 *
 * `enabled=false`（token usage 关闭）或 threadId 未知（新线程）时保持 null。
 */
export function useThreadTokenUsage(
  threadId: string | undefined,
  enabled: boolean,
  refreshKey: number,
): ThreadTokenUsage | null {
  const [usage, setUsage] = useState<ThreadTokenUsage | null>(null);

  useEffect(() => {
    if (!enabled || !threadId) {
      setUsage(null);
      return;
    }
    let cancelled = false;
    void fetchThreadTokenUsage(threadId).then((result) => {
      if (!cancelled) setUsage(result);
    });
    return () => {
      cancelled = true;
    };
    // 依赖固定三元组：挂载 / threadId 变化 / run 结束（refreshKey 递增）。
    // 显式不含任何时间维度——本 hook 的契约就是零轮询。
  }, [threadId, enabled, refreshKey]);

  return usage;
}
