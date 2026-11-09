"use client";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { consoleErrorMessage } from "@/core/console/errors";
import {
  formatCompactNumber,
  formatCost,
  formatDayLabel,
  formatInteger,
} from "@/core/console/format";
import type { ConsoleUsage } from "@/core/console/types";
import { useI18n } from "@/core/i18n/hooks";

interface Props {
  usage: ConsoleUsage | undefined;
  isLoading: boolean;
  error?: unknown;
  onRetry?: () => void;
}

/**
 * Daily token trend, drawn as plain CSS bars.
 *
 * No chart library: the repo has none installed, and this deployment ships to
 * an offline server where adding an npm dependency means rebuilding and
 * hand-carrying the bundle. A bar chart over at most 90 zero-filled days does
 * not justify that, and scaling by the window maximum is the whole of the
 * math involved.
 *
 * B2: bars are stacked - chat tokens (primary) on the bottom, llm_bookkeeping
 * tokens (amber) on top. Old backends without `chat_tokens`/`llm_tokens` fall
 * back to `total_tokens` as a single-color bar.
 */
export function ConsoleUsageChart({ usage, isLoading, error, onRetry }: Props) {
  const { t } = useI18n();
  const c = t.console;

  if (isLoading && !usage) {
    return <Skeleton className="h-56 rounded-xl" />;
  }

  if (error && !usage) {
    return (
      <div className="bg-card/60 rounded-xl border p-4">
        <div className="mb-4 flex items-baseline justify-between gap-2">
          <h3 className="text-sm font-medium">{c.dailyTrend}</h3>
        </div>
        <div
          data-testid="usage-chart-error"
          className="flex flex-col items-center gap-2 py-12"
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
      </div>
    );
  }

  const days = usage?.days ?? [];
  const peak = Math.max(...days.map((d) => d.total_tokens), 0);
  const dayCount = days.length;
  const emptyText = c.noUsageInWindow.replace("{n}", String(dayCount));

  return (
    <div className="bg-card/60 rounded-xl border p-4">
      <div className="mb-4 flex items-baseline justify-between gap-2">
        <div>
          <h3 className="text-sm font-medium">{c.dailyTrend}</h3>
          <p className="text-muted-foreground mt-0.5 text-[11px]">
            {c.trendScopeNote}
          </p>
        </div>
        <span className="text-muted-foreground text-xs">
          {c.windowTotal} {formatInteger(usage?.total_tokens ?? 0)} ·{" "}
          {formatCost(usage?.total_cost, usage?.currency)}
        </span>
      </div>

      {/* Legend for the stacked bars */}
      {peak > 0 && (
        <div className="mb-2 flex items-center gap-4 text-[11px]">
          <span className="flex items-center gap-1.5">
            <span className="bg-primary/70 inline-block size-2.5 rounded-sm" />
            <span className="text-muted-foreground">{c.legendChat}</span>
          </span>
          <span className="flex items-center gap-1.5">
            <span className="inline-block size-2.5 rounded-sm bg-amber-500/70" />
            <span className="text-muted-foreground">{c.legendLlm}</span>
          </span>
        </div>
      )}

      {peak === 0 ? (
        <p className="text-muted-foreground py-12 text-center text-xs">
          {emptyText}
        </p>
      ) : (
        <div className="flex h-40 items-end gap-1">
          {days.map((day) => {
            // B2: split chat vs llm tokens. Old backends without the split
            // fields fall back to total_tokens as a single-color bar.
            const chatTokens = day.chat_tokens ?? day.total_tokens;
            const llmTokens = day.llm_tokens ?? 0;
            const totalTokens = day.total_tokens;

            // Floor non-zero bars at 2% so a small-but-real day stays visible
            // next to a 250M-token outlier.
            const heightPct =
              totalTokens === 0 ? 0 : Math.max((totalTokens / peak) * 100, 2);

            // Proportion of the bar that is chat vs llm.
            const chatPct =
              totalTokens > 0 ? (chatTokens / totalTokens) * 100 : 0;
            const llmPct =
              totalTokens > 0 ? (llmTokens / totalTokens) * 100 : 0;

            return (
              <div
                key={day.date}
                className="group relative flex h-full flex-1 flex-col items-center justify-end"
                title={`${day.date}\n${c.legendChat}: ${formatInteger(chatTokens)}\n${c.legendLlm}: ${formatInteger(llmTokens)}\n${c.tokensLabel}: ${formatInteger(totalTokens)}\n${c.runsLabel}: ${formatInteger(day.runs)}\n${c.costLabel}: ${formatCost(day.cost, usage?.currency)}`}
              >
                <div
                  className="group-hover:bg-primary/80 w-full overflow-hidden rounded-t transition-colors"
                  style={{ height: `${heightPct}%` }}
                  data-testid={`usage-bar-${day.date}`}
                  data-tokens={totalTokens}
                >
                  {llmTokens > 0 && (
                    <div
                      className="w-full bg-amber-500/70"
                      style={{ height: `${llmPct}%` }}
                    />
                  )}
                  {chatTokens > 0 && (
                    <div
                      className="bg-primary/70 w-full"
                      style={{ height: `${chatPct}%` }}
                    />
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      <div className="text-muted-foreground mt-2 flex gap-1 text-[10px]">
        {days.map((day, index) => (
          <span key={day.date} className="flex-1 text-center">
            {/* Label every other day so 90-day windows stay legible. */}
            {days.length <= 16 || index % Math.ceil(days.length / 12) === 0
              ? formatDayLabel(day.date)
              : ""}
          </span>
        ))}
      </div>

      {peak > 0 && (
        <p className="text-muted-foreground mt-3 text-[11px]">
          {c.peakDay}: {formatCompactNumber(peak)}
        </p>
      )}
    </div>
  );
}
