"use client";

import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { UsageCapabilityFilter } from "@/components/workspace/usage/usage-capability-filter";
import { UsageSummary } from "@/components/workspace/usage/usage-summary";
import { UsageTaskList } from "@/components/workspace/usage/usage-task-list";
import { localTzOffsetMinutes } from "@/core/console/format";
import { useI18n } from "@/core/i18n/hooks";
import { PERSONAL_USAGE_PAGE_SIZE as PAGE_SIZE } from "@/core/usage/api";
import { usePersonalUsage } from "@/core/usage/hooks";
import type {
  UsageCapabilityFilter as CapabilityFilter,
  UsageDays,
} from "@/core/usage/types";
import { cn } from "@/lib/utils";

const WINDOW_OPTIONS: readonly UsageDays[] = [7, 14, 30];

export function PersonalUsagePage() {
  const { t } = useI18n();
  const c = t.personalUsage;
  const windowLabelId = useId();
  const [days, setDays] = useState<UsageDays>(7);
  const [capability, setCapability] = useState<CapabilityFilter>("all");
  const [offset, setOffset] = useState(0);
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const query = usePersonalUsage({
    days,
    capability,
    offset,
    limit: PAGE_SIZE,
    tzOffsetMinutes: localTzOffsetMinutes(),
  });

  const resetForFilter = () => {
    setOffset(0);
    setExpanded(new Set());
  };
  const changeDays = (value: UsageDays) => {
    setDays(value);
    resetForFilter();
  };
  const changeCapability = (value: CapabilityFilter) => {
    setCapability(value);
    resetForFilter();
  };
  const changeOffset = (value: number) => {
    setOffset(value);
    setExpanded(new Set());
  };
  const toggleTask = (taskKey: string) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(taskKey)) next.delete(taskKey);
      else next.add(taskKey);
      return next;
    });
  };

  return (
    <main
      data-testid="personal-usage-page"
      className="flex size-full min-w-0 flex-col overflow-x-hidden overflow-y-auto p-4 min-[1050px]:px-8 min-[1050px]:py-10 sm:p-6"
    >
      <div
        data-testid="usage-layout-content"
        className="mx-auto flex w-full max-w-[1120px] min-w-0 flex-col"
      >
        <header
          data-testid="usage-header"
          className="flex min-w-0 flex-col gap-3 min-[820px]:flex-row min-[820px]:items-start min-[820px]:justify-between min-[820px]:gap-6"
        >
          <div className="min-w-0">
            <h1 className="text-[25px] leading-tight font-bold tracking-tight min-[820px]:text-[28px]">
              {c.title}
            </h1>
            <p className="text-muted-foreground mt-2 text-sm">{c.subtitle}</p>
          </div>
          <section
            data-testid="usage-window-control"
            className="flex min-w-0 flex-col gap-2 min-[820px]:shrink-0 min-[820px]:flex-row min-[820px]:items-center min-[820px]:gap-3"
            role="group"
            aria-labelledby={windowLabelId}
          >
            <span
              id={windowLabelId}
              className="text-muted-foreground shrink-0 text-sm"
            >
              {c.windowLabel}
            </span>
            <div className="bg-muted flex min-w-0 gap-1 rounded-xl p-1 min-[820px]:flex-none">
              {WINDOW_OPTIONS.map((option) => {
                const active = days === option;
                return (
                  <Button
                    key={option}
                    type="button"
                    size="sm"
                    variant="ghost"
                    aria-pressed={active}
                    data-testid={`usage-days-${option}`}
                    onClick={() => changeDays(option)}
                    className={cn(
                      "h-8 min-w-[66px] flex-1 rounded-lg px-3 text-sm focus-visible:ring-indigo-500 min-[820px]:flex-none",
                      active
                        ? "bg-background text-foreground hover:bg-background hover:text-foreground dark:bg-input/30 dark:hover:bg-input/30 shadow-sm"
                        : "text-muted-foreground hover:text-foreground",
                    )}
                  >
                    {c.lastNDays.replace("{n}", String(option))}
                  </Button>
                );
              })}
            </div>
          </section>
        </header>

        <div className="mt-[30px] min-w-0">
          <UsageCapabilityFilter
            value={capability}
            onChange={changeCapability}
          />
        </div>

        {query.isLoading && !query.data ? (
          <div
            role="status"
            className="text-muted-foreground mt-6 rounded-2xl border border-dashed p-8 text-center text-sm"
          >
            {c.loading}
          </div>
        ) : null}

        {query.isError && !query.data ? (
          <div
            role="alert"
            className="border-destructive/30 mt-6 flex flex-col items-center gap-3 rounded-2xl border p-8 text-center"
          >
            <p className="text-destructive text-sm">{c.error}</p>
            <Button
              data-testid="usage-retry"
              type="button"
              variant="outline"
              className="focus-visible:ring-indigo-500"
              onClick={() => void query.refetch()}
            >
              {c.retry}
            </Button>
          </div>
        ) : null}

        {query.data ? (
          <>
            {query.isPlaceholderData ? (
              <div
                data-testid="usage-updating"
                role="status"
                className="text-muted-foreground mt-5 text-xs"
              >
                {c.loading}
              </div>
            ) : null}
            <div className="mt-5 min-w-0">
              <UsageSummary summary={query.data.summary} />
            </div>
            <section
              data-testid="usage-task-panel"
              className="bg-card mt-6 min-w-0 rounded-2xl border shadow-sm"
            >
              <div className="flex min-h-[60px] min-w-0 flex-wrap items-center justify-between gap-x-3 gap-y-1 px-4 min-[820px]:px-[22px]">
                <h2 className="text-base font-semibold">{c.taskListLabel}</h2>
                {query.data.total_tasks > 0 ? (
                  <span
                    data-testid="usage-page-info"
                    className="text-muted-foreground text-xs"
                  >
                    {c.pageInfo(
                      offset + 1,
                      offset + query.data.tasks.length,
                      query.data.total_tasks,
                    )}
                  </span>
                ) : null}
              </div>
              {query.data.tasks.length > 0 ? (
                <UsageTaskList
                  tasks={query.data.tasks}
                  expanded={expanded}
                  onToggle={toggleTask}
                />
              ) : (
                <div className="text-muted-foreground m-4 rounded-xl border border-dashed p-8 text-center text-sm min-[820px]:m-[22px]">
                  {c.empty}
                </div>
              )}
              {query.data.total_tasks > 0 ? (
                <nav
                  className="grid grid-cols-2 gap-2 border-t px-4 py-3 min-[820px]:flex min-[820px]:justify-end min-[820px]:px-[22px]"
                  aria-label={c.taskListLabel}
                >
                  <Button
                    data-testid="usage-previous-page"
                    type="button"
                    size="sm"
                    variant="outline"
                    className="focus-visible:ring-indigo-500"
                    disabled={offset === 0}
                    onClick={() =>
                      changeOffset(Math.max(0, offset - PAGE_SIZE))
                    }
                  >
                    {c.previous}
                  </Button>
                  <Button
                    data-testid="usage-next-page"
                    type="button"
                    size="sm"
                    variant="outline"
                    className="focus-visible:ring-indigo-500"
                    disabled={!query.data.has_more}
                    onClick={() => changeOffset(offset + PAGE_SIZE)}
                  >
                    {c.next}
                  </Button>
                </nav>
              ) : null}
            </section>
            <p className="text-muted-foreground mt-3.5 text-xs">
              {c.footerNote}
            </p>
          </>
        ) : null}
      </div>
    </main>
  );
}
