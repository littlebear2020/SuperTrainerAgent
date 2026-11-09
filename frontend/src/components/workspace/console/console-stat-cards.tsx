"use client";

import {
  ActivityIcon,
  AlertCircleIcon,
  CoinsIcon,
  LayersIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { consoleErrorMessage } from "@/core/console/errors";
import { formatCost, formatInteger } from "@/core/console/format";
import type { ConsoleStats } from "@/core/console/types";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

interface Props {
  stats: ConsoleStats | undefined;
  isLoading: boolean;
  error?: unknown;
  onRetry?: () => void;
}

/**
 * Headline counters (C6b-2 四卡瘦身, A7 用户拍板)：全链路总成本（含 breakdown
 * 小字）/ 总 token / 总任务数 / 未计价提醒。失败任务与超阈值撤出卡位，移至
 * 任务列表面板顶部小字（信息保留不占黄金卡位）。
 *
 * 总成本卡读 ``total_cost_all``（C3：对话 + 管线 LLM + 媒体，真账优先+估算），
 * 老 backend 无该字段时回退 ``total_cost``。
 */
export function ConsoleStatCards({ stats, isLoading, error, onRetry }: Props) {
  const { t } = useI18n();
  const c = t.console;

  if (isLoading && !stats) {
    return (
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {Array.from({ length: 4 }, (_, i) => (
          <Skeleton key={i} className="h-24 rounded-xl" />
        ))}
      </div>
    );
  }

  if (error && !stats) {
    // Single error bar at the same height as the card grid to prevent layout
    // shift. Four all-zero cards would read as "everything is zero" rather
    // than "something went wrong".
    return (
      <div
        data-testid="stat-cards-error"
        className="flex h-24 items-center justify-center gap-2 rounded-xl border px-4"
      >
        <p className="text-muted-foreground text-xs">
          {consoleErrorMessage(error, c)}
        </p>
        {onRetry && (
          <Button variant="outline" size="sm" onClick={onRetry}>
            {c.retry}
          </Button>
        )}
      </div>
    );
  }

  // C3: prefer the full-chain figure; fall back to the legacy LLM-only field
  // for old backends that predate total_cost_all.
  const costAll = stats?.total_cost_all ?? stats?.total_cost ?? null;
  const breakdown = stats?.cost_breakdown;
  const unpriced = stats?.unpriced_calls ?? 0;

  const cards = [
    {
      key: "cost-all",
      label: c.totalCostAll,
      // null (no pricing configured) renders as "-", never as 0.00.
      value: formatCost(costAll, stats?.currency),
      hint:
        costAll == null
          ? c.costUnpriced
          : `${c.costBreakdownLlm} ${formatCost(breakdown?.llm ?? null, stats?.currency)} · ${c.costBreakdownMedia} ${formatCost(breakdown?.media ?? 0, stats?.currency)}`,
      icon: CoinsIcon,
      tone: "text-emerald-500",
    },
    {
      key: "tokens",
      label: c.totalTokens,
      value: formatInteger(stats?.total_tokens ?? 0),
      hint: c.tokensHint,
      icon: ActivityIcon,
      tone: "text-violet-500",
    },
    {
      key: "runs",
      label: c.totalRuns,
      value: formatInteger(stats?.total_runs ?? 0),
      hint: `${c.activeRuns} ${formatInteger(stats?.active_runs ?? 0)}`,
      icon: LayersIcon,
      tone: "text-sky-500",
    },
    {
      key: "unpriced",
      label: c.unpricedCard,
      value: formatInteger(unpriced),
      hint:
        unpriced > 0
          ? c.unpricedCardHint.replace("{n}", String(unpriced))
          : c.unpricedCardZero,
      icon: AlertCircleIcon,
      tone: unpriced > 0 ? "text-amber-500" : "text-muted-foreground",
    },
  ];

  return (
    <div
      data-testid="console-stat-cards"
      className="grid grid-cols-2 gap-3 lg:grid-cols-4"
    >
      {cards.map((card) => (
        <div
          key={card.key}
          data-testid={`stat-card-${card.key}`}
          className="bg-card/60 flex flex-col gap-1 rounded-xl border p-4"
        >
          <div className="text-muted-foreground flex items-center gap-1.5 text-xs">
            <card.icon size={13} className={cn(card.tone)} />
            <span className="truncate">{card.label}</span>
          </div>
          <div
            className="truncate text-xl font-semibold tabular-nums"
            title={card.value}
          >
            {card.value}
          </div>
          {/* Breakdown line can exceed the card width; allow wrapping here —
              the old `truncate` would clip the LLM/media split to nothing. */}
          <div className="text-muted-foreground text-[11px] leading-tight">
            {card.hint}
          </div>
        </div>
      ))}
    </div>
  );
}
