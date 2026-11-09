"use client";

import { ArrowDownIcon, ArrowUpIcon, TriangleAlertIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { consoleErrorMessage } from "@/core/console/errors";
import {
  formatCompactNumber,
  formatCost,
  formatDateTime,
  formatDuration,
  formatInteger,
} from "@/core/console/format";
import type {
  ConsoleRunSortKey,
  ConsoleRunsPage,
  SortDirection,
} from "@/core/console/types";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

interface Props {
  page: ConsoleRunsPage | undefined;
  isLoading: boolean;
  isAdmin: boolean;
  sortBy: ConsoleRunSortKey;
  sortDir: SortDirection;
  onSort: (key: ConsoleRunSortKey) => void;
  currency: string | null | undefined;
  error?: unknown;
  onRetry?: () => void;
}

const STATUS_TONES: Record<string, string> = {
  success: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  error: "bg-rose-500/10 text-rose-600 dark:text-rose-400",
  timeout: "bg-rose-500/10 text-rose-600 dark:text-rose-400",
  running: "bg-sky-500/10 text-sky-600 dark:text-sky-400",
  pending: "bg-amber-500/10 text-amber-600 dark:text-amber-400",
  interrupted: "bg-muted text-muted-foreground",
};

/**
 * Cross-thread run listing.
 *
 * Token counts render compact (`256.5M`) with the exact value in `title`:
 * real data spans 0 to 256,517,593, and a full-grouped integer in a table
 * column forces horizontal scroll on every row. `tabular-nums` keeps the
 * digits aligned so the column can be scanned vertically.
 */
export function ConsoleRunsTable({
  page,
  isLoading,
  isAdmin,
  sortBy,
  sortDir,
  onSort,
  currency,
  error,
  onRetry,
}: Props) {
  const { t } = useI18n();
  const c = t.console;

  if (isLoading && !page) {
    return <Skeleton className="h-72 rounded-xl" />;
  }

  if (error && !page) {
    return (
      <div
        data-testid="runs-table-error"
        className="bg-card/60 flex items-center justify-center gap-2 rounded-xl border px-4 py-12"
      >
        <p className="text-muted-foreground text-xs">
          {consoleErrorMessage(error, c)}
        </p>
        {onRetry && (
          <Button variant="outline" size="sm" onClick={onRetry}>
            {c.retry}
          </Button>
        )}
      </div>
    );
  }

  const runs = page?.runs ?? [];

  return (
    <div className="bg-card/60 overflow-hidden rounded-xl border">
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="text-muted-foreground border-b">
            <tr>
              <SortableHeader
                label={c.colCreatedAt}
                sortKey="created_at"
                activeKey={sortBy}
                direction={sortDir}
                onSort={onSort}
              />
              <th className="px-3 py-2.5 text-left font-medium">{c.colTask}</th>
              {/* Owner is admin-only: a regular user would see only themselves. */}
              {isAdmin && (
                <th className="px-3 py-2.5 text-left font-medium">
                  {c.colUser}
                </th>
              )}
              <SortableHeader
                label={c.colStatus}
                sortKey="status"
                activeKey={sortBy}
                direction={sortDir}
                onSort={onSort}
              />
              <th className="px-3 py-2.5 text-left font-medium">
                {c.colModel}
              </th>
              <SortableHeader
                label={c.colTokens}
                sortKey="total_tokens"
                activeKey={sortBy}
                direction={sortDir}
                onSort={onSort}
                align="right"
              />
              <th className="px-3 py-2.5 text-right font-medium">
                {c.colCost}
              </th>
              <th className="px-3 py-2.5 text-right font-medium">
                {c.colDuration}
              </th>
              <th className="px-3 py-2.5 text-left font-medium">
                {c.colError}
              </th>
            </tr>
          </thead>
          <tbody>
            {runs.length === 0 ? (
              <tr>
                <td
                  colSpan={isAdmin ? 9 : 8}
                  className="text-muted-foreground px-3 py-12 text-center"
                >
                  {c.noRuns}
                </td>
              </tr>
            ) : (
              runs.map((run) => (
                <tr
                  key={run.run_id}
                  className="hover:bg-muted/40 border-b last:border-0"
                  data-testid={`console-run-${run.run_id}`}
                >
                  <td className="text-muted-foreground px-3 py-2.5 whitespace-nowrap">
                    {formatDateTime(run.created_at)}
                  </td>
                  <td className="max-w-[220px] px-3 py-2.5">
                    <div
                      className="truncate"
                      title={run.thread_title ?? run.thread_id}
                    >
                      {run.thread_title ?? run.thread_id}
                    </div>
                    {run.assistant_id && (
                      <div className="text-muted-foreground truncate text-[10px]">
                        {run.assistant_id}
                      </div>
                    )}
                  </td>
                  {isAdmin && (
                    <td className="max-w-[160px] px-3 py-2.5">
                      <span
                        className="block truncate"
                        title={run.user_email ?? run.user_id ?? ""}
                      >
                        {run.user_email ?? run.user_id ?? "—"}
                      </span>
                    </td>
                  )}
                  <td className="px-3 py-2.5">
                    <span
                      className={cn(
                        "inline-block rounded px-1.5 py-0.5 text-[10px] font-medium",
                        STATUS_TONES[run.status] ??
                          "bg-muted text-muted-foreground",
                      )}
                    >
                      {run.status}
                    </span>
                    {run.stop_reason && (
                      <span
                        className="text-muted-foreground ml-1 text-[10px]"
                        title={c.stopReasonHint}
                      >
                        {run.stop_reason}
                      </span>
                    )}
                  </td>
                  <td className="text-muted-foreground max-w-[140px] px-3 py-2.5">
                    <span
                      className="block truncate"
                      title={run.model_name ?? ""}
                    >
                      {run.model_name ?? "—"}
                    </span>
                  </td>
                  <td className="px-3 py-2.5 text-right whitespace-nowrap tabular-nums">
                    <span
                      className="inline-flex items-center gap-1"
                      title={`${formatInteger(run.total_tokens)}${
                        run.subagent_tokens > 0
                          ? `\n${c.subagentTokens}: ${formatInteger(run.subagent_tokens)}`
                          : ""
                      }`}
                    >
                      {run.over_threshold && (
                        <TriangleAlertIcon
                          size={11}
                          className="text-amber-500"
                          data-testid={`over-threshold-${run.run_id}`}
                          aria-label={c.overThreshold}
                        />
                      )}
                      {formatCompactNumber(run.total_tokens)}
                    </span>
                  </td>
                  <td className="px-3 py-2.5 text-right whitespace-nowrap tabular-nums">
                    {formatCost(run.cost, currency)}
                  </td>
                  <td className="text-muted-foreground px-3 py-2.5 text-right whitespace-nowrap tabular-nums">
                    {formatDuration(run.duration_seconds)}
                  </td>
                  <td className="max-w-[240px] px-3 py-2.5">
                    {run.error ? (
                      <span
                        className="text-muted-foreground block truncate"
                        title={run.error}
                      >
                        {run.error}
                      </span>
                    ) : (
                      <span className="text-muted-foreground">—</span>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function SortableHeader({
  label,
  sortKey,
  activeKey,
  direction,
  onSort,
  align = "left",
}: {
  label: string;
  sortKey: ConsoleRunSortKey;
  activeKey: ConsoleRunSortKey;
  direction: SortDirection;
  onSort: (key: ConsoleRunSortKey) => void;
  align?: "left" | "right";
}) {
  const active = activeKey === sortKey;
  return (
    <th
      className={cn(
        "px-3 py-2.5 font-medium",
        align === "right" ? "text-right" : "text-left",
      )}
      // aria-sort belongs on the column header, not on the button inside it —
      // the role that supports it is columnheader.
      aria-sort={
        active ? (direction === "asc" ? "ascending" : "descending") : "none"
      }
    >
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className={cn(
          "hover:text-foreground inline-flex items-center gap-1 transition-colors",
          active && "text-foreground",
        )}
        data-testid={`sort-${sortKey}`}
      >
        {label}
        {active &&
          (direction === "asc" ? (
            <ArrowUpIcon size={11} />
          ) : (
            <ArrowDownIcon size={11} />
          ))}
      </button>
    </th>
  );
}
