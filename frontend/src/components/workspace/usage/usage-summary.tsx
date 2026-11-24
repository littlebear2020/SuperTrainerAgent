import { Coins, Cpu } from "lucide-react";
import { useId } from "react";

import { useI18n } from "@/core/i18n/hooks";
import type {
  PersonalUsageResponse,
  UsageCostSource,
} from "@/core/usage/types";
import { formatUsageCost, formatInteger } from "@/core/utils/format";
import { cn } from "@/lib/utils";

// The theme has no success/warning/accent tokens for this page, so the
// violet accent stays local to the usage components (light + dark pairs).
const accentTile =
  "bg-violet-50 text-violet-600 dark:bg-indigo-400/10 dark:text-indigo-300";

export function UsageCostSourceBadge({
  source,
  testId,
  className,
}: {
  source: UsageCostSource;
  testId?: string;
  className?: string;
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
      className={cn(
        accentTile,
        "inline-flex w-fit shrink-0 rounded-full border border-violet-200/70 px-2 py-0.5 text-[10px] font-medium whitespace-nowrap dark:border-indigo-400/20",
        className,
      )}
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
  const costLabelId = useId();
  const tokensLabelId = useId();
  return (
    <section
      data-testid="usage-summary"
      className="bg-card grid min-w-0 grid-cols-1 divide-y rounded-2xl border shadow-sm sm:grid-cols-2 sm:divide-x sm:divide-y-0"
      aria-label={c.title}
    >
      <section
        data-testid="usage-summary-cost"
        aria-labelledby={costLabelId}
        className="min-w-0 p-5 min-[1050px]:px-7 sm:p-6"
      >
        <div className="flex min-w-0 items-center gap-2.5">
          <span
            aria-hidden="true"
            className={cn(
              accentTile,
              "flex size-7 shrink-0 items-center justify-center rounded-lg",
            )}
          >
            <Coins className="size-4" />
          </span>
          <h2 id={costLabelId} className="min-w-0 text-sm font-medium">
            {c.totalCost}
          </h2>
          <UsageCostSourceBadge
            source={summary.cost_source}
            testId="usage-summary-cost-source"
            className="ml-auto"
          />
        </div>
        <p className="mt-3 text-[28px] leading-tight font-semibold tracking-tight break-all min-[640px]:text-4xl">
          {formatUsageCost(summary.cost, summary.currency)}
        </p>
        <p className="text-muted-foreground mt-2 text-xs">{c.totalCostHint}</p>
      </section>
      <section
        data-testid="usage-summary-tokens"
        aria-labelledby={tokensLabelId}
        className="min-w-0 p-5 min-[1050px]:px-7 sm:p-6"
      >
        <div className="flex min-w-0 items-center gap-2.5">
          <span
            aria-hidden="true"
            className={cn(
              accentTile,
              "flex size-7 shrink-0 items-center justify-center rounded-lg",
            )}
          >
            <Cpu className="size-4" />
          </span>
          <h2 id={tokensLabelId} className="min-w-0 text-sm font-medium">
            {c.totalTokens}
          </h2>
        </div>
        <p className="mt-3 text-[28px] leading-tight font-semibold tracking-tight break-all min-[640px]:text-4xl">
          {formatInteger(summary.total_tokens)}
        </p>
        <p className="text-muted-foreground mt-2 text-xs">
          {c.totalTokensHint}
        </p>
      </section>
    </section>
  );
}
