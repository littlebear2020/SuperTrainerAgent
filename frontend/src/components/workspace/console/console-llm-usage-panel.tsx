"use client";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { consoleErrorMessage } from "@/core/console/errors";
import { formatCompactNumber, formatCost } from "@/core/console/format";
import { useConsoleLlmUsage } from "@/core/console/hooks";
import { useI18n } from "@/core/i18n/hooks";

/** domain key → i18n key mapping (replaces hardcoded DOMAIN_LABELS). */
function domainLabel(
  domain: string,
  c: ReturnType<typeof useI18n>["t"]["console"],
): string {
  const map: Record<string, string> = {
    video_gen: c.llmUsageDomainVideoGen,
    vimax: c.llmUsageDomainVimax,
    writer_assist: c.llmUsageDomainWriterAssist,
    gallery_recipe: c.llmUsageDomainGalleryRecipe,
    suggestions: c.llmUsageDomainSuggestions,
    unknown: c.llmUsageDomainUnknown,
  };
  return map[domain] ?? c.llmUsageDomainUnknown;
}

interface Props {
  days: number;
  userId?: string;
}

/**
 * Non-conversation LLM usage panel (C2).
 *
 * Visible to all users (auto-scoped by the backend). Shows aggregated LLM
 * call counts, tokens, and spend grouped by domain.
 */
export function ConsoleLlmUsagePanel({ days, userId }: Props) {
  const { t } = useI18n();
  const c = t.console;
  const query = useConsoleLlmUsage({ days, userId });

  if (query.isLoading) {
    return <Skeleton className="h-48 rounded-xl" />;
  }

  if (query.error && !query.data) {
    return (
      <section data-testid="llm-usage-panel" className="flex flex-col gap-2">
        <div>
          <h2 className="text-sm font-medium">{c.llmUsageTitle}</h2>
          <p className="text-muted-foreground text-xs">{c.llmUsageSubtitle}</p>
        </div>
        <div
          data-testid="llm-usage-error"
          className="bg-card/60 flex items-center justify-center gap-2 rounded-xl border px-4 py-8"
        >
          <p className="text-muted-foreground text-xs">
            {consoleErrorMessage(query.error, c)}
          </p>
          <Button variant="outline" size="sm" onClick={() => query.refetch()}>
            {c.retry}
          </Button>
        </div>
      </section>
    );
  }

  const domains = query.data?.domains ?? [];
  const currency = query.data?.currency;

  return (
    <section data-testid="llm-usage-panel" className="flex flex-col gap-2">
      <div>
        <h2 className="text-sm font-medium">{c.llmUsageTitle}</h2>
        {/* C6b-6：每面板一行口径说明 */}
        <p className="text-muted-foreground text-xs">{c.llmPanelNote}</p>
      </div>

      <div className="bg-card/60 overflow-hidden rounded-xl border">
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-muted-foreground border-b">
              <tr>
                <th className="px-3 py-2.5 text-left font-medium">
                  {c.llmUsageDomain}
                </th>
                <th className="px-3 py-2.5 text-right font-medium">
                  {c.llmUsageCalls}
                </th>
                <th className="px-3 py-2.5 text-right font-medium">
                  {c.llmUsageFailedCalls}
                </th>
                <th className="px-3 py-2.5 text-right font-medium">
                  {c.llmUsageTokens}
                </th>
                <th className="px-3 py-2.5 text-right font-medium">
                  {c.llmUsageCost}
                </th>
              </tr>
            </thead>
            <tbody>
              {domains.length === 0 ? (
                <tr>
                  <td
                    colSpan={5}
                    className="text-muted-foreground px-3 py-8 text-center"
                  >
                    {c.llmUsageEmpty}
                  </td>
                </tr>
              ) : (
                domains.map((d) => (
                  <tr key={d.domain} className="border-b last:border-0">
                    <td className="px-3 py-2">{domainLabel(d.domain, c)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      {formatCompactNumber(d.calls)}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      {d.failed_calls > 0 ? (
                        <span className="text-rose-600 dark:text-rose-400">
                          {d.failed_calls}
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      {formatCompactNumber(d.tokens)}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      {d.cost != null ? formatCost(d.cost, currency) : "—"}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}
