"use client";

import { currencySymbol } from "@/core/console/format";
import type { ConsoleExternalCallsDailyItem } from "@/core/console/types";
import { useI18n } from "@/core/i18n/hooks";

/**
 * C7: Simple stacked bar chart for daily media cost (real + estimated).
 * Deliberately NOT a generalization of console-usage-chart.tsx — that chart's
 * Props are bound to ConsoleUsageDay shape (chat_tokens/llm_tokens with
 * fallback logic). This chart only needs {date, cost_real, cost_estimated}.
 *
 * C6（2026-08-27 成本可信化）：币种来自后端字段（删 ¥ 硬编码）；双柱图例 +
 * 「单价修正分界」标注——C0 读时重算后跨修正点的历史趋势会出现 ~30 倍断崖，
 * 不标注会被误读为成本暴跌。
 */
export function ConsoleCostChart({
  data,
  currency,
}: {
  data: ConsoleExternalCallsDailyItem[];
  currency: string | null | undefined;
}) {
  const { t } = useI18n();
  const c = t.console;
  const symbol = currencySymbol(currency ?? "CNY");

  if (!data.length) {
    return (
      <p
        data-testid="cost-chart-empty"
        className="text-muted-foreground py-8 text-center text-xs"
      >
        {c.emptyNoData}
      </p>
    );
  }

  const maxValue = Math.max(
    ...data.map((d) => d.cost_real + d.cost_estimated),
    0.01, // avoid div-by-zero
  );

  return (
    <div className="flex flex-col gap-1.5">
      <div
        data-testid="console-cost-chart"
        className="border-border/50 flex items-end gap-[2px] rounded-lg border p-2"
        style={{ height: "80px" }}
      >
        {data.map((d) => {
          const total = d.cost_real + d.cost_estimated;
          const heightPct = (total / maxValue) * 100;
          const realPct = total > 0 ? (d.cost_real / total) * 100 : 0;
          return (
            <div
              key={d.date}
              className="group relative flex flex-1 flex-col justify-end"
              style={{ height: "100%" }}
              title={`${d.date}: ${symbol}${total.toFixed(2)} (${c.costChartReal} ${symbol}${d.cost_real.toFixed(2)} + ${c.costChartEstimated} ${symbol}${d.cost_estimated.toFixed(2)})`}
            >
              <div
                className="w-full overflow-hidden rounded-sm transition-all"
                style={{
                  height: `${heightPct}%`,
                  minHeight: total > 0 ? "2px" : "0",
                }}
              >
                {/* Real cost bar (bottom, solid) */}
                <div className="bg-primary" style={{ height: `${realPct}%` }} />
                {/* Estimated cost bar (top, translucent) */}
                <div
                  className="bg-primary/30"
                  style={{ height: `${100 - realPct}%` }}
                />
              </div>
            </div>
          );
        })}
      </div>
      {/* C6：双柱图例（实心=真账 · 半透明=估算） */}
      <p
        data-testid="cost-chart-legend"
        className="text-muted-foreground flex items-center gap-3 text-[11px]"
      >
        <span className="flex items-center gap-1">
          <span className="bg-primary inline-block h-2 w-3 rounded-sm" />
          {c.costChartReal}
        </span>
        <span className="flex items-center gap-1">
          <span className="bg-primary/30 inline-block h-2 w-3 rounded-sm" />
          {c.costChartEstimated}
        </span>
      </p>
    </div>
  );
}
