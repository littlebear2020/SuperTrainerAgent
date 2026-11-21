"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { UsageCapabilityFilter } from "@/components/workspace/usage/usage-capability-filter";
import { UsageSummary } from "@/components/workspace/usage/usage-summary";
import { UsageTaskList } from "@/components/workspace/usage/usage-task-list";
import { localTzOffsetMinutes } from "@/core/console/format";
import { useI18n } from "@/core/i18n/hooks";
import { usePersonalUsage } from "@/core/usage/hooks";
import type {
  UsageCapabilityFilter as CapabilityFilter,
  UsageDays,
} from "@/core/usage/types";

const PAGE_SIZE = 20;
const WINDOW_OPTIONS: readonly UsageDays[] = [7, 14, 30];

export function PersonalUsagePage() {
  const { t } = useI18n();
  const c = t.personalUsage;
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
      className="flex size-full min-w-0 flex-col gap-5 overflow-x-hidden overflow-y-auto p-4 sm:p-6"
    >
      <header className="min-w-0">
        <h1 className="text-xl font-semibold tracking-tight">{c.title}</h1>
        <p className="text-muted-foreground mt-1 text-sm">{c.subtitle}</p>
      </header>

      <section
        className="flex flex-wrap gap-2"
        role="group"
        aria-label={c.windowLabel}
      >
        {WINDOW_OPTIONS.map((option) => (
          <Button
            key={option}
            type="button"
            size="sm"
            variant={days === option ? "default" : "outline"}
            aria-pressed={days === option}
            data-testid={`usage-days-${option}`}
            onClick={() => changeDays(option)}
          >
            {c.lastNDays.replace("{n}", String(option))}
          </Button>
        ))}
      </section>

      <UsageCapabilityFilter value={capability} onChange={changeCapability} />

      {query.isLoading && !query.data ? (
        <div
          role="status"
          className="text-muted-foreground rounded-xl border border-dashed p-8 text-center text-sm"
        >
          {c.loading}
        </div>
      ) : null}

      {query.isError && !query.data ? (
        <div
          role="alert"
          className="border-destructive/30 flex flex-col items-center gap-3 rounded-xl border p-8 text-center"
        >
          <p className="text-destructive text-sm">{c.error}</p>
          <Button
            data-testid="usage-retry"
            type="button"
            variant="outline"
            onClick={() => void query.refetch()}
          >
            {c.retry}
          </Button>
        </div>
      ) : null}

      {query.data ? (
        <>
          <UsageSummary summary={query.data.summary} />
          <section className="min-w-0">
            {query.data.tasks.length > 0 ? (
              <UsageTaskList
                tasks={query.data.tasks}
                expanded={expanded}
                onToggle={toggleTask}
              />
            ) : (
              <div className="text-muted-foreground rounded-xl border border-dashed p-8 text-center text-sm">
                {c.empty}
              </div>
            )}
          </section>
          {query.data.total_tasks > 0 ? (
            <nav
              className="flex flex-wrap items-center justify-between gap-3"
              aria-label={c.taskListLabel}
            >
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
              <div className="flex gap-2">
                <Button
                  data-testid="usage-previous-page"
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={offset === 0}
                  onClick={() => changeOffset(Math.max(0, offset - PAGE_SIZE))}
                >
                  {c.previous}
                </Button>
                <Button
                  data-testid="usage-next-page"
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={!query.data.has_more}
                  onClick={() => changeOffset(offset + PAGE_SIZE)}
                >
                  {c.next}
                </Button>
              </div>
            </nav>
          ) : null}
          <p className="text-muted-foreground text-xs">{c.footerNote}</p>
        </>
      ) : null}
    </main>
  );
}
