import { Button } from "@/components/ui/button";
import { UsageModelBreakdown } from "@/components/workspace/usage/usage-model-breakdown";
import { UsageCostSourceBadge } from "@/components/workspace/usage/usage-summary";
import { useI18n } from "@/core/i18n/hooks";
import { usageTaskHref } from "@/core/usage/routes";
import type { UsageTask } from "@/core/usage/types";
import { formatUsageCost, formatInteger } from "@/core/utils/format";

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
    <div
      className="flex min-w-0 flex-col gap-3"
      role="list"
      aria-label={c.taskListLabel}
    >
      {tasks.map((task) => {
        const id = task.source_id;
        const isExpanded = expanded.has(task.task_key);
        const detailsId = `usage-details-${task.task_key}`;
        const hasEquation =
          task.llm_cost !== null &&
          task.media_cost !== null &&
          task.cost !== null;
        return (
          <article
            key={task.task_key}
            role="listitem"
            className="bg-card min-w-0 overflow-hidden rounded-xl border"
          >
            <div
              data-testid={`task-row-${id}`}
              role="button"
              tabIndex={0}
              aria-expanded={isExpanded}
              aria-controls={detailsId}
              aria-label={`${task.title}: ${isExpanded ? c.collapseDetails : c.expandDetails}`}
              className="hover:bg-muted/40 focus-visible:ring-ring flex min-w-0 cursor-pointer flex-wrap items-center gap-3 p-4 outline-none select-none focus-visible:ring-2"
              onClick={() => onToggle(task.task_key)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  onToggle(task.task_key);
                }
              }}
            >
              <span
                aria-hidden="true"
                className="text-muted-foreground w-4 text-center"
              >
                {isExpanded ? "−" : "+"}
              </span>
              <div className="min-w-[180px] flex-1">
                <h2 className="text-sm font-medium break-words">
                  {task.title}
                </h2>
                <div className="text-muted-foreground mt-1 flex flex-wrap items-center gap-2 text-xs">
                  <span
                    data-testid={`task-status-${task.status}`}
                    data-task-id={id}
                  >
                    {statusLabels[task.status]}
                  </span>
                  {task.status === "running" ? (
                    <span data-testid={`task-running-note-${id}`}>
                      {c.asOfNow}
                    </span>
                  ) : null}
                  {task.status === "running" &&
                  task.cost === null &&
                  task.total_tokens === null ? (
                    <span>{c.calculating}</span>
                  ) : null}
                </div>
              </div>
              <div className="min-w-0 text-right">
                <div
                  data-testid={`task-cost-${id}`}
                  className="text-sm font-semibold break-all"
                >
                  {formatUsageCost(task.cost, task.currency)}
                </div>
                <div
                  data-testid={`task-tokens-${id}`}
                  className="text-muted-foreground text-xs break-all"
                >
                  {formatInteger(task.total_tokens)} Token
                </div>
              </div>
            </div>
            {isExpanded ? (
              <div
                id={detailsId}
                data-testid={`task-details-${id}`}
                className="bg-muted/20 flex max-w-full min-w-0 flex-col gap-4 border-t p-4"
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
                  <div className="bg-background rounded-lg border p-3">
                    <dt className="text-muted-foreground text-[11px]">
                      {c.llmCost}
                    </dt>
                    <dd className="mt-1 text-sm font-medium break-all">
                      {formatUsageCost(task.llm_cost, task.currency)}
                    </dd>
                  </div>
                  <div className="bg-background rounded-lg border p-3">
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
                  <div className="bg-background rounded-lg border p-3">
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
                  <Button asChild type="button" variant="outline" size="sm">
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
  );
}
