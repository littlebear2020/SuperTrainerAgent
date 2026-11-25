"use client";

import { RefreshCw, Search } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useI18n } from "@/core/i18n/hooks";
import type { VideoTaskBrief } from "@/core/video-gen/types";
import { selectAndFilterWorks } from "@/core/works/model";
import type { WorkSummary } from "@/core/works/types";
import type { WorkCapability } from "@/core/workspace/video-assets-route";

import { WorkCard } from "./work-card";
import { WorkViewerDialog } from "./work-viewer-dialog";

const WORK_PAGE_SIZE = 6;

export function WorkGrid({
  works,
  capability = works[0]?.capability ?? "course-video",
  isLoading = false,
  isError = false,
  onRetry,
  onContinue,
  onTaskNavigate,
  onDelete,
}: {
  works: WorkSummary[];
  capability?: WorkCapability;
  isLoading?: boolean;
  isError?: boolean;
  onRetry: () => void | Promise<void>;
  onContinue: (work: WorkSummary) => void;
  onTaskNavigate: (task: VideoTaskBrief, path?: "storyboard") => void;
  onDelete: (id: string) => void;
}) {
  const { t } = useI18n();
  const [query, setQuery] = useState("");
  const [visibleCount, setVisibleCount] = useState(WORK_PAGE_SIZE);
  const [selectedWorkId, setSelectedWorkId] = useState<string | null>(null);
  const [refreshPending, setRefreshPending] = useState(false);
  const refreshLock = useRef(false);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const viewerWasOpenRef = useRef(false);
  useEffect(() => {
    if (selectedWorkId === null && viewerWasOpenRef.current) {
      returnFocusRef.current?.focus();
    }
    viewerWasOpenRef.current = selectedWorkId !== null;
  }, [selectedWorkId]);
  const filteredWorks = useMemo(
    () => selectAndFilterWorks(works, query),
    [query, works],
  );
  const selectedWork = works.find((work) => work.id === selectedWorkId) ?? null;
  const capabilityLabel = t.workspace.home.courseItems[capability].title;

  const handleRefresh = async () => {
    if (refreshLock.current) return;
    refreshLock.current = true;
    setRefreshPending(true);
    try {
      await onRetry();
    } catch {
      // Query errors are surfaced through isError; do not leave an unhandled rejection.
    } finally {
      refreshLock.current = false;
      setRefreshPending(false);
    }
  };

  const openWork = (workId: string) => {
    returnFocusRef.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    setSelectedWorkId(workId);
  };

  let content;
  if (isLoading && works.length === 0) {
    content = (
      <div
        role="status"
        className="rounded-2xl border border-dashed p-10 text-center"
      >
        {t.workspace.works.loading}
      </div>
    );
  } else if (isError && works.length === 0) {
    content = (
      <div
        role="alert"
        className="rounded-2xl border border-dashed p-10 text-center"
      >
        <p>{t.workspace.works.loadError}</p>
        <Button
          variant="outline"
          className="mt-4"
          disabled={refreshPending}
          onClick={() => void handleRefresh()}
        >
          {t.workspace.works.retry}
        </Button>
      </div>
    );
  } else if (works.length === 0) {
    content = (
      <div className="rounded-2xl border border-dashed p-10 text-center">
        {t.workspace.works.empty(capabilityLabel)}
      </div>
    );
  } else if (filteredWorks.length === 0) {
    content = (
      <div className="rounded-2xl border border-dashed p-10 text-center">
        <p>{t.workspace.works.noResults}</p>
        <Button variant="link" onClick={() => setQuery("")}>
          {t.workspace.works.clearSearch}
        </Button>
      </div>
    );
  } else {
    content = (
      <>
        <div
          data-testid="work-grid"
          className="grid auto-rows-fr grid-cols-1 justify-start gap-5 sm:grid-cols-2 xl:grid-cols-3"
        >
          {filteredWorks.slice(0, visibleCount).map((work) => (
            <WorkCard
              key={work.id}
              work={work}
              onOpen={() => openWork(work.id)}
              onContinue={onContinue}
              onTaskNavigate={onTaskNavigate}
              onDelete={onDelete}
            />
          ))}
        </div>
        {visibleCount < filteredWorks.length ? (
          <div className="mt-5 text-center">
            <Button
              variant="outline"
              onClick={() => setVisibleCount((count) => count + WORK_PAGE_SIZE)}
            >
              {t.common.loadMore}
            </Button>
          </div>
        ) : null}
      </>
    );
  }

  return (
    <section data-testid="works-view" aria-busy={refreshPending}>
      <div className="mb-4 space-y-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
          <div className="relative min-w-0 flex-1">
            <Search className="text-muted-foreground absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2" />
            <Input
              type="search"
              value={query}
              aria-label={t.workspace.works.searchLabel}
              placeholder={t.workspace.works.searchPlaceholder}
              onChange={(event) => setQuery(event.target.value)}
              className="pl-9"
            />
          </div>
          <Button
            variant="outline"
            size="sm"
            disabled={refreshPending}
            onClick={() => void handleRefresh()}
          >
            <RefreshCw
              className={
                refreshPending ? "mr-2 h-4 w-4 animate-spin" : "mr-2 h-4 w-4"
              }
            />
            {refreshPending
              ? t.workspace.works.refreshing
              : isError
                ? t.workspace.works.retry
                : t.workspace.works.refresh}
          </Button>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-muted-foreground text-sm">
            {t.workspace.works.count(filteredWorks.length)}
          </span>
          {isError && works.length > 0 ? (
            <p role="alert" className="text-sm text-red-600 dark:text-red-400">
              {t.workspace.works.loadError}
            </p>
          ) : null}
        </div>
      </div>

      {content}
      <WorkViewerDialog
        work={selectedWork}
        open={selectedWork !== null}
        onOpenChange={(open) => {
          if (!open) setSelectedWorkId(null);
        }}
        onContinue={onContinue}
        onTaskNavigate={onTaskNavigate}
      />
    </section>
  );
}
