import { ChevronDown } from "lucide-react";

import { Button } from "@/components/ui/button";
import { UsageModelBreakdown } from "@/components/workspace/usage/usage-model-breakdown";
import { UsageCostSourceBadge } from "@/components/workspace/usage/usage-summary";
import { useI18n } from "@/core/i18n/hooks";
import { usageTaskHref } from "@/core/usage/routes";
import type { UsageTask } from "@/core/usage/types";
import { formatUsageCost, formatInteger } from "@/core/utils/format";
import { cn } from "@/lib/utils";

// One grid definition shared by the column header and every row. The title
// track has no pixel floor (minmax(0,1fr)): when the sidebar is expanded the
// content area loses 208px and the title yields instead of clipping the
// status/Token/cost columns. Below 820px the same cells re-flow into a
// two-row card (title + cost, then status + tokens) through grid areas; the
// shared number column is capped at 45% so a long value wraps instead of
// squeezing the title.
const ROW_GRID =
  "grid grid-cols-[minmax(0,1fr)_fit-content(45%)_28px] min-[820px]:grid-cols-[minmax(0,1fr)_82px_118px_105px_28px] min-[1050px]:grid-cols-[minmax(0,1fr)_92px_142px_126px_28px]";
const ROW_PADDING = "px-4 min-[820px]:px-[22px]";
const ROW_GAP = "gap-x-3 min-[1050px]:gap-x-[18px]";
const CARD_AREAS =
  "max-[820px]:[grid-template-areas:'title_cost_chevron'_'status_tokens_chevron']";

// The theme has no success/warning tokens, so the three states use palette
// classes local to this page (light + dark pairs).
const STATUS_TONE = {
  completed: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  running:
    "bg-amber-50 text-amber-700 dark:bg-amber-950/30 dark:text-amber-300",
  incomplete: "bg-muted text-muted-foreground",
} as const;

export function UsageTaskList({
  tasks,
  expanded,
  onToggle,
}: {
  tasks: UsageTask[];
  expanded: ReadonlySet<string>;
  onToggle: (taskKey: string) => void;
}) {
  const { t } = useI18n();
  const c = t.personalUsage;
  const statusLabels = {
    completed: c.completed,
    running: c.running,
    incomplete: c.incomplete,
  };

  return (
    <>
      {/* Visual aid only: every row already carries its own labels. */}
      <div
        data-testid="usage-task-columns"
        aria-hidden="true"
        className={cn(
          ROW_GRID,
          ROW_PADDING,
          ROW_GAP,
          "text-muted-foreground bg-muted/40 hidden h-[39px] items-center border-b text-xs min-[820px]:grid",
        )}
      >
        <span>{c.taskColumn}</span>
        <span>{c.statusColumn}</span>
        <span className="text-right">{c.totalTokens}</span>
        <span className="text-right">{c.totalCost}</span>
        <span />
      </div>
      <div role="list" aria-label={c.taskListLabel} className="min-w-0">
        {tasks.map((task) => {
          const id = task.source_id;
          const isExpanded = expanded.has(task.task_key);
          const detailsId = `usage-details-${task.task_key}`;
          const hasEquation =
            task.llm_cost !== null &&
            task.media_cost !== null &&
            task.cost !== null;
          const title = task.title ?? "标题生成中";
          return (
            <article
              key={task.task_key}
              role="listitem"
              className="min-w-0 border-b last:border-b-0"
            >
              <div
                data-testid={`task-row-${id}`}
                role="button"
                tabIndex={0}
                aria-expanded={isExpanded}
                aria-controls={detailsId}
                aria-label={`${title}: ${isExpanded ? c.collapseDetails : c.expandDetails}`}
                className={cn(
                  ROW_GRID,
                  CARD_AREAS,
                  ROW_PADDING,
                  ROW_GAP,
                  "hover:bg-muted/40 grid min-h-[70px] min-w-0 cursor-pointer items-center gap-y-1 py-2.5 outline-none select-none focus-visible:ring-2 focus-visible:ring-indigo-500 max-[820px]:grid-rows-2",
                )}
                onClick={() => onToggle(task.task_key)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    onToggle(task.task_key);
                  }
                }}
              >
                <h2
                  data-testid={`task-title-${id}`}
                  title={title}
                  className="min-w-0 truncate text-sm font-medium max-[820px]:line-clamp-2 max-[820px]:whitespace-normal max-[820px]:[grid-area:title]"
                >
                  {title}
                </h2>
                <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 max-[820px]:[grid-area:status]">
                  <span
                    data-testid={`task-status-${task.status}`}
                    data-task-id={id}
                    className={cn(
                      "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium",
                      STATUS_TONE[task.status],
                    )}
                  >
                    <span
                      aria-hidden="true"
                      className="size-1.5 shrink-0 rounded-full bg-current"
                    />
                    {statusLabels[task.status]}
                  </span>
                  {task.status === "running" ? (
                    <span
                      data-testid={`task-running-note-${id}`}
                      className="text-muted-foreground text-xs"
                    >
                      {c.asOfNow}
                    </span>
                  ) : null}
                  {task.status === "running" &&
                  task.cost === null &&
                  task.total_tokens === null ? (
                    <span className="text-muted-foreground text-xs">
                      {c.calculating}
                    </span>
                  ) : null}
                </div>
                <div
                  data-testid={`task-tokens-${id}`}
                  className="text-muted-foreground min-w-0 text-right text-xs break-all tabular-nums max-[820px]:[grid-area:tokens]"
                >
                  {formatInteger(task.total_tokens)} Token
                </div>
                <div
                  data-testid={`task-cost-${id}`}
                  className="min-w-0 text-right text-sm font-semibold break-all tabular-nums max-[820px]:[grid-area:cost]"
                >
                  {formatUsageCost(task.cost, task.currency)}
                </div>
                <span
                  data-testid={`task-chevron-${id}`}
                  aria-hidden="true"
                  className="text-muted-foreground flex items-center justify-center max-[820px]:[grid-area:chevron]"
                >
                  <ChevronDown
                    className={cn(
                      "size-4 transition-transform duration-150 motion-reduce:transition-none",
                      isExpanded && "rotate-180",
                    )}
                  />
                </span>
              </div>
              {isExpanded ? (
                <div
                  id={detailsId}
                  data-testid={`task-details-${id}`}
                  className="bg-muted/20 flex max-w-full min-w-0 flex-col gap-4 border-t p-4 min-[820px]:p-5"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <h3 className="text-muted-foreground text-xs font-semibold">
                      {c.taskCost}
                    </h3>
                    <UsageCostSourceBadge
                      source={task.cost_source}
                      testId={`task-cost-source-${id}`}
                    />
                  </div>
                  <dl className="grid min-w-0 grid-cols-1 gap-2 sm:grid-cols-3">
                    <div className="bg-background rounded-xl border p-3">
                      <dt className="text-muted-foreground text-[11px]">
                        {c.llmCost}
                      </dt>
                      <dd className="mt-1 text-sm font-medium break-all">
                        {formatUsageCost(task.llm_cost, task.currency)}
                      </dd>
                    </div>
                    <div className="bg-background rounded-xl border p-3">
                      <dt className="text-muted-foreground text-[11px]">
                        {c.mediaCost}
                      </dt>
                      <dd
                        data-testid={`task-media-cost-${id}`}
                        className="mt-1 text-sm font-medium break-all"
                      >
                        {task.media_cost === null
                          ? c.mediaUnknown
                          : formatUsageCost(task.media_cost, task.currency)}
                      </dd>
                    </div>
                    <div className="bg-background rounded-xl border p-3">
                      <dt className="text-muted-foreground text-[11px]">
                        {c.taskCost}
                      </dt>
                      <dd
                        data-testid={`task-total-cost-${id}`}
                        className="mt-1 text-sm font-medium break-all"
                      >
                        {formatUsageCost(task.cost, task.currency)}
                      </dd>
                    </div>
                  </dl>
                  {hasEquation ? (
                    <p
                      data-testid={`cost-equation-${id}`}
                      className="text-muted-foreground text-xs break-all"
                    >
                      {formatUsageCost(task.llm_cost, task.currency)} +{" "}
                      {formatUsageCost(task.media_cost, task.currency)} ={" "}
                      {formatUsageCost(task.cost, task.currency)}
                    </p>
                  ) : null}
                  <UsageModelBreakdown
                    models={task.models}
                    currency={task.currency}
                  />
                  <div>
                    <Button
                      asChild
                      type="button"
                      variant="outline"
                      size="sm"
                      className="focus-visible:ring-indigo-500"
                    >
                      <a
                        data-testid={`open-original-${id}`}
                        href={usageTaskHref(task)}
                        onClick={(event) => event.stopPropagation()}
                        onKeyDown={(event) => {
                          event.stopPropagation();
                          if (event.key === "Enter") {
                            event.preventDefault();
                            event.currentTarget.click();
                          }
                        }}
                      >
                        {c.openOriginal}
                      </a>
                    </Button>
                  </div>
                </div>
              ) : null}
            </article>
          );
        })}
      </div>
    </>
  );
}
