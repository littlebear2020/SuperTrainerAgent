"use client";

import type { Message } from "@langchain/langgraph-sdk";
import { CoinsIcon } from "lucide-react";
import { useMemo } from "react";

import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useI18n } from "@/core/i18n/hooks";
import { accumulateUsage, formatTokenCount } from "@/core/messages/usage";
import type { ThreadTokenUsage } from "@/core/threads/token-usage";
import { cn } from "@/lib/utils";

interface TokenUsageIndicatorProps {
  messages: Message[];
  enabled?: boolean;
  className?: string;
  /**
   * D3（2026-09-23 批 2）：thread 累计 tokens 告警线（由任务类型解析而来，
   * /api/models 下发）。超过即变红——只提示不阻断；undefined = 无告警线。
   */
  warnThresholdTokens?: number;
  /**
   * D3 修订（2026-09-23 review #4）：端点侧 thread 全历史聚合（与后端告警
   * 同源）——总量展示与超线判定都以它为准，覆盖未加载的分页历史；
   * null/undefined（拉取失败或不可用）回落 messages 内聚合。
   */
  threadUsage?: ThreadTokenUsage | null;
}

export function TokenUsageIndicator({
  messages,
  enabled = false,
  className,
  warnThresholdTokens,
  threadUsage,
}: TokenUsageIndicatorProps) {
  const { t } = useI18n();

  const messageUsage = useMemo(() => accumulateUsage(messages), [messages]);
  // 端点聚合优先（与后端 D3 同一 SQL 聚合源）；不可用回落消息聚合。
  const usage = threadUsage ?? messageUsage;
  const overBudget =
    warnThresholdTokens !== undefined &&
    usage !== null &&
    usage.totalTokens > warnThresholdTokens;

  if (!enabled) {
    return null;
  }

  return (
    <Tooltip delayDuration={200}>
      <TooltipTrigger asChild>
        <button
          type="button"
          className={cn(
            "text-muted-foreground bg-background/70 flex cursor-default items-center gap-1.5 rounded-full border px-2 py-1 text-xs",
            !usage && "opacity-60",
            // 红色只给真异常（第三轮 Q4）：正常任务照常灰色。
            overBudget &&
              "text-destructive border-destructive/50 bg-destructive/10",
            className,
          )}
        >
          <CoinsIcon size={14} />
          <span>{t.tokenUsage.label}</span>
          <span className="font-mono">
            {usage ? formatTokenCount(usage.totalTokens) : "-"}
          </span>
        </button>
      </TooltipTrigger>
      <TooltipContent side="bottom" align="end">
        <div className="space-y-1 text-xs">
          <div className="font-medium">{t.tokenUsage.title}</div>
          {usage ? (
            <>
              <div className="flex justify-between gap-4">
                <span>{t.tokenUsage.input}</span>
                <span className="font-mono">
                  {formatTokenCount(usage.inputTokens)}
                </span>
              </div>
              <div className="flex justify-between gap-4">
                <span>{t.tokenUsage.output}</span>
                <span className="font-mono">
                  {formatTokenCount(usage.outputTokens)}
                </span>
              </div>
              <div className="border-t pt-1">
                <div className="flex justify-between gap-4">
                  <span>{t.tokenUsage.total}</span>
                  <span className="font-mono font-medium">
                    {formatTokenCount(usage.totalTokens)}
                  </span>
                </div>
              </div>
              {overBudget && (
                <div className="text-destructive max-w-64 pt-1">
                  {t.tokenUsage.overBudgetHint}
                </div>
              )}
            </>
          ) : (
            <div className="text-muted-foreground max-w-56">
              {t.tokenUsage.unavailable}
            </div>
          )}
        </div>
      </TooltipContent>
    </Tooltip>
  );
}
