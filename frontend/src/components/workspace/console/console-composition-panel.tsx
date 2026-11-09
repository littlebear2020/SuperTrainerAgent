"use client";

import { useState } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import {
  formatCompactNumber,
  formatCost,
  formatInteger,
} from "@/core/console/format";
import { providerLabel } from "@/core/console/provider-labels";
import type {
  ConsoleExternalCallsResponse,
  ConsoleUsage,
} from "@/core/console/types";
import { useI18n } from "@/core/i18n/hooks";

type CompositionTab = "model" | "provider" | "type";

interface Props {
  usage: ConsoleUsage | undefined;
  externalCalls: ConsoleExternalCallsResponse | undefined;
  isLoading: boolean;
}

/**
 * 构成区（C6b-3 用户拍板 2026-08-26）：「按模型 / 按供应商 / 按类型」三 tab
 * 收进同一卡位（与趋势区「用量/花费」tab 同一交互语言），消灭长滚动。
 *
 * - 按模型 = usage.by_model（LLM 管线 + 对话，cost 为 LLM 估算+真账）；
 * - 按供应商 = usage.media_cost_by_provider_split（媒体 real/estimated 拆分，
 *   C4 分色来源）；
 * - 按类型 = external-calls by_type（媒体调用真账+估算）。
 * 三处数字与统计卡/趋势图同源（C5 三链收口的后端保证）。
 */
export function ConsoleCompositionPanel({
  usage,
  externalCalls,
  isLoading,
}: Props) {
  const { t } = useI18n();
  const c = t.console;
  const [tab, setTab] = useState<CompositionTab>("model");

  if (isLoading && !usage) {
    return <Skeleton className="h-56 rounded-xl" />;
  }

  const currency = usage?.currency;

  const tabs: { key: CompositionTab; label: string }[] = [
    { key: "model", label: c.compositionByModel },
    { key: "provider", label: c.compositionByProvider },
    { key: "type", label: c.compositionByType },
  ];

  const modelRows = Object.entries(usage?.by_model ?? {})
    .map(([model, entry]) => ({ model, ...entry }))
    .sort((a, b) => (b.cost ?? 0) - (a.cost ?? 0) || b.tokens - a.tokens);
  const peak = Math.max(...modelRows.map((r) => r.tokens), 0);

  const providerRows = Object.entries(
    usage?.media_cost_by_provider_split ?? {},
  ).sort((a, b) => b[1].real + b[1].estimated - (a[1].real + a[1].estimated));

  const typeRows = externalCalls?.by_type ?? [];

  return (
    <div
      data-testid="composition-panel"
      className="bg-card/60 rounded-xl border p-4"
    >
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-medium">{c.byModel}</h3>
        <span className="text-muted-foreground text-[11px]">
          {c.compositionScopeNote}
        </span>
      </div>

      <div className="mb-3 flex gap-1">
        {tabs.map(({ key, label }) => (
          <button
            key={key}
            data-testid={`composition-tab-${key}`}
            onClick={() => setTab(key)}
            className={`rounded px-2 py-1 text-xs font-medium transition-colors ${
              tab === key
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-muted"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "model" && (
        <div data-testid="composition-model">
          {modelRows.length === 0 ? (
            <p className="text-muted-foreground py-10 text-center text-xs">
              {c.emptyNoData}
            </p>
          ) : (
            <ul className="flex flex-col gap-2.5">
              {modelRows.map((row) => (
                <li key={row.model} className="flex flex-col gap-1">
                  <div className="flex items-baseline justify-between gap-2 text-xs">
                    <span className="truncate font-medium" title={row.model}>
                      {row.model}
                    </span>
                    <span className="text-muted-foreground shrink-0 tabular-nums">
                      <span title={formatInteger(row.tokens)}>
                        {formatCompactNumber(row.tokens)}
                      </span>
                      {" · "}
                      {formatCost(row.cost, currency)}
                    </span>
                  </div>
                  <div className="bg-muted h-1.5 w-full overflow-hidden rounded-full">
                    <div
                      className="bg-primary/60 h-full rounded-full"
                      style={{
                        width: `${peak > 0 ? Math.max((row.tokens / peak) * 100, 1) : 0}%`,
                      }}
                    />
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {tab === "provider" && (
        <div data-testid="composition-provider">
          {providerRows.length === 0 ? (
            <p className="text-muted-foreground py-10 text-center text-xs">
              {c.emptyNoData}
            </p>
          ) : (
            <ul className="flex flex-col gap-2.5">
              {providerRows.map(([provider, split]) => {
                const total = split.real + split.estimated;
                const realPct = total > 0 ? (split.real / total) * 100 : 0;
                return (
                  <li key={provider} className="flex flex-col gap-1">
                    <div className="flex items-baseline justify-between gap-2 text-xs">
                      <span className="truncate font-medium">
                        {providerLabel(provider, c)}
                      </span>
                      <span className="text-muted-foreground shrink-0 tabular-nums">
                        {formatCost(total, currency)}
                        {split.real === 0 && split.estimated > 0 && (
                          <span className="text-muted-foreground"> *</span>
                        )}
                      </span>
                    </div>
                    {/* C4 分色：实心=真账 · 半透明=估算 */}
                    <div className="bg-muted flex h-1.5 w-full overflow-hidden rounded-full">
                      <div
                        className="bg-primary h-full"
                        style={{ width: `${realPct}%` }}
                      />
                      <div
                        className="bg-primary/30 h-full"
                        style={{ width: `${100 - realPct}%` }}
                      />
                    </div>
                  </li>
                );
              })}
              <p className="text-muted-foreground text-[11px]">
                {c.providerCostNote}
              </p>
            </ul>
          )}
        </div>
      )}

      {tab === "type" && (
        <div data-testid="composition-type">
          {typeRows.length === 0 ? (
            <p className="text-muted-foreground py-10 text-center text-xs">
              {c.emptyNoData}
            </p>
          ) : (
            <ul className="flex flex-col gap-2.5">
              {typeRows.map((row) => (
                <li
                  key={row.media_type}
                  className="flex items-baseline justify-between gap-2 text-xs"
                >
                  <span className="font-medium">
                    {row.media_type === "image"
                      ? c.externalCallsTabImage
                      : row.media_type === "video"
                        ? c.externalCallsTabVideo
                        : row.media_type === "tts"
                          ? c.externalCallsTabTts
                          : row.media_type}
                  </span>
                  <span className="text-muted-foreground tabular-nums">
                    {formatCompactNumber(row.calls)} {c.callsSuffix}
                    {row.cost != null && ` · ${formatCost(row.cost, currency)}`}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
