"use client";

import { useState } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { consoleErrorMessage } from "@/core/console/errors";
import { formatCompactNumber, formatCost } from "@/core/console/format";
import { useConsoleExternalCalls } from "@/core/console/hooks";
import { providerLabel } from "@/core/console/provider-labels";
import { useI18n } from "@/core/i18n/hooks";

/** Type tab values. */
type TypeTab = "all" | "image" | "video" | "tts";

interface Props {
  days: number;
  userId?: string;
  currency?: string | null;
}

/**
 * External API call counts + costs panel (C3/C4/C7/C8; C6 2026-08-27 重构).
 *
 * - provider→类型过滤走后端 ``media_type`` 字段（删三个前端硬编码 Set——
 *   新 provider 漏显 / ``qihu`` 同 provider 双 type 重复出现的根源）；
 * - 花费一律用后端 ``currency`` 渲染（删 ¥ 硬编码）；
 * - tab 过滤后合计行随过滤集同步（不再显示未过滤的总数）；
 * - 空态区分「窗口内无数据」与「有调用但均未计价」；
 * - 每日花费趋势图移至页首「用量/花费」切换卡，此处不再重复渲染。
 */
export function ConsoleExternalCallsPanel({ days, userId, currency }: Props) {
  const { t } = useI18n();
  const c = t.console;
  const query = useConsoleExternalCalls({ days, userId });
  const [activeTab, setActiveTab] = useState<TypeTab>("all");

  if (query.isLoading) {
    return <Skeleton className="h-48 rounded-xl" />;
  }

  if (query.error && !query.data) {
    return (
      <section
        data-testid="external-calls-panel"
        className="flex flex-col gap-2"
      >
        <div>
          <h2 className="text-sm font-medium">{c.externalCallsTitle}</h2>
          <p className="text-muted-foreground text-xs">{c.costPanelNote}</p>
        </div>
        <div
          data-testid="external-calls-error"
          className="bg-card/60 flex items-center justify-center gap-2 rounded-xl border px-4 py-6"
        >
          <p className="text-muted-foreground text-xs">
            {consoleErrorMessage(query.error, c)}
          </p>
        </div>
      </section>
    );
  }

  const allProviders = query.data?.by_provider ?? [];

  // B1（2026-08-27 评审修复）：by_provider 已按 (provider, media_type) 出多行——
  // 同名多行的 provider（如 qihu 生图+生视频）在行内补类型后缀以区分。
  const providerCounts = new Map<string, number>();
  for (const p of allProviders) {
    providerCounts.set(p.provider, (providerCounts.get(p.provider) ?? 0) + 1);
  }
  const typeLabel = (mediaType: string | undefined) =>
    mediaType === "image"
      ? c.externalCallsTabImage
      : mediaType === "video"
        ? c.externalCallsTabVideo
        : mediaType === "tts"
          ? c.externalCallsTabTts
          : (mediaType ?? "");

  // C6: filter by the backend-served media_type — exact, future-proof, and
  // stops `qihu` (image+video rows under one provider label) from appearing
  // in both tabs.
  const providers = allProviders.filter((p) => {
    if (activeTab === "all") return true;
    return p.media_type === activeTab;
  });

  // C6: totals follow the filtered set — the pre-fix footer showed the
  // unfiltered total, which contradicted the table above it.
  const filteredCalls = providers.reduce((sum, p) => sum + p.calls, 0);
  const filteredCost = providers.reduce((sum, p) => sum + (p.cost ?? 0), 0);
  const anyPricedRow = providers.some((p) => p.cost != null);

  const typeData = query.data?.by_type ?? [];

  return (
    <section data-testid="external-calls-panel" className="flex flex-col gap-2">
      <div>
        <h2 className="text-sm font-medium">{c.externalCallsTitle}</h2>
        {/* C6b-6：每面板一行口径说明 */}
        <p className="text-muted-foreground text-xs">{c.costPanelNote}</p>
      </div>

      {/* C8: Type tabs */}
      <div className="flex gap-1">
        {(["all", "image", "video", "tts"] as TypeTab[]).map((tab) => (
          <button
            key={tab}
            data-testid={`external-calls-tab-${tab}`}
            onClick={() => setActiveTab(tab)}
            className={`rounded px-2 py-1 text-xs font-medium transition-colors ${
              activeTab === tab
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-muted"
            }`}
          >
            {tab === "all"
              ? c.externalCallsTabAll
              : tab === "image"
                ? c.externalCallsTabImage
                : tab === "video"
                  ? c.externalCallsTabVideo
                  : c.externalCallsTabTts}
          </button>
        ))}
      </div>

      <div className="bg-card/60 overflow-hidden rounded-xl border">
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-muted-foreground border-b">
              <tr>
                <th className="px-3 py-2.5 text-left font-medium">
                  {c.externalCallsProvider}
                </th>
                <th className="px-3 py-2.5 text-right font-medium">
                  {c.externalCallsTotal}
                </th>
                <th className="px-3 py-2.5 text-right font-medium">
                  {c.llmUsageCost}
                </th>
              </tr>
            </thead>
            <tbody>
              {providers.length === 0 ? (
                <tr>
                  <td
                    colSpan={3}
                    data-testid="external-calls-empty"
                    className="text-muted-foreground px-3 py-6 text-center"
                  >
                    {/* A17② 双因空态：区分「窗口内无数据」与「有调用但未计价」 */}
                    {allProviders.length === 0
                      ? c.emptyNoData
                      : c.emptyUnpricedOnly}
                  </td>
                </tr>
              ) : (
                providers.map((p) => (
                  <tr
                    key={`${p.provider}:${p.media_type}`}
                    className="border-b last:border-0"
                  >
                    <td className="px-3 py-2">
                      {providerLabel(p.provider, c)}
                      {(providerCounts.get(p.provider) ?? 0) > 1 && (
                        <span className="text-muted-foreground ml-1 text-[10px]">
                          （{typeLabel(p.media_type)}）
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      {formatCompactNumber(p.calls)}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      {p.cost != null ? (
                        <span
                          title={
                            p.cost_estimated != null
                              ? `真账 ${p.cost_real ?? 0} + 估算 ${p.cost_estimated}`
                              : undefined
                          }
                        >
                          {/* C6：币种来自后端字段 */}
                          {formatCost(p.cost, currency ?? "CNY")}
                          {p.cost_estimated != null && p.cost_real == null && (
                            <span className="text-muted-foreground"> *</span>
                          )}
                        </span>
                      ) : (
                        <span className="text-muted-foreground">
                          {c.pricingSourceUnpriced}
                        </span>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
            {providers.length > 0 && (
              <tfoot className="border-t font-medium">
                <tr>
                  <td className="px-3 py-2">{c.externalCallsTotal}</td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {formatCompactNumber(filteredCalls)}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {anyPricedRow
                      ? formatCost(filteredCost, currency ?? "CNY")
                      : ""}
                  </td>
                </tr>
              </tfoot>
            )}
          </table>
        </div>
      </div>

      {/* C4/C6：估算 * 图例（真账优先口径说明） */}
      <p className="text-muted-foreground text-[11px]">{c.providerCostNote}</p>

      {/* C3: Type breakdown summary */}
      {typeData.length > 0 && (
        <div className="text-muted-foreground flex gap-3 text-[11px]">
          {typeData.map((t) => (
            <span key={t.media_type}>
              {t.media_type}: {formatCompactNumber(t.calls)}
              {t.cost != null && ` ${formatCost(t.cost, currency ?? "CNY")}`}
            </span>
          ))}
        </div>
      )}
    </section>
  );
}
