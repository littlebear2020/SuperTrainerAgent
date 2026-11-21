import { useI18n } from "@/core/i18n/hooks";
import type {
  PersonalUsageResponse,
  UsageCostSource,
} from "@/core/usage/types";
import { formatUsageCost, formatInteger } from "@/core/utils/format";

export function UsageCostSourceBadge({
  source,
  testId,
}: {
  source: UsageCostSource;
  testId?: string;
}) {
  const { t } = useI18n();
  const c = t.personalUsage;
  if (!source) return null;
  const label =
    source === "real" ? c.real : source === "estimated" ? c.estimated : c.mixed;
  return (
    <span
      data-testid={testId}
      data-cost-source={source}
      className="bg-muted text-muted-foreground inline-flex w-fit rounded-full px-2 py-0.5 text-[10px] font-medium whitespace-nowrap"
    >
      {label}
    </span>
  );
}

export function UsageSummary({
  summary,
}: {
  summary: PersonalUsageResponse["summary"];
}) {
  const { t } = useI18n();
  const c = t.personalUsage;
  return (
    <section
      className="grid min-w-0 grid-cols-1 gap-3 sm:grid-cols-2"
      aria-label={c.title}
    >
      <div
        data-testid="usage-summary-cost"
        className="bg-card min-w-0 rounded-2xl border p-5 shadow-sm"
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-muted-foreground text-sm font-medium">
            {c.totalCost}
          </h2>
          <UsageCostSourceBadge
            source={summary.cost_source}
            testId="usage-summary-cost-source"
          />
        </div>
        <p className="mt-3 text-3xl font-semibold tracking-tight">
          {formatUsageCost(summary.cost, summary.currency)}
        </p>
        <p className="text-muted-foreground mt-2 text-xs">{c.totalCostHint}</p>
      </div>
      <div
        data-testid="usage-summary-tokens"
        className="bg-card min-w-0 rounded-2xl border p-5 shadow-sm"
      >
        <h2 className="text-muted-foreground text-sm font-medium">
          {c.totalTokens}
        </h2>
        <p className="mt-3 text-3xl font-semibold tracking-tight break-all">
          {formatInteger(summary.total_tokens)}
        </p>
        <p className="text-muted-foreground mt-2 text-xs">
          {c.totalTokensHint}
        </p>
      </div>
    </section>
  );
}
