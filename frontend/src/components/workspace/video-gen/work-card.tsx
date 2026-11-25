"use client";

import { useState } from "react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/core/i18n/hooks";
import { formatDateTime, parseTimestamp } from "@/core/utils/datetime";
import { useRecomposeTask } from "@/core/video-gen/hooks";
import { recomposeActionFor } from "@/core/video-gen/resume-actions";
import { videoTaskTypeLabel } from "@/core/video-gen/task-type-label";
import type { VideoTaskBrief } from "@/core/video-gen/types";
import type { WorkFile, WorkSummary } from "@/core/works/types";
import { cn } from "@/lib/utils";

import { StatusBadge } from "./status-badge";
import { RecomposeConfirmDialog } from "./video-gen-recompose-dialog";
import { formatFileSize, WorkMedia } from "./work-media";

function AgentDeliveryBadge({ work }: { work: WorkSummary }) {
  const { t } = useI18n();
  const state = work.agentDeliveryState ?? "unknown";
  const labels = {
    delivered: t.workspace.works.deliveredState,
    not_delivered: t.workspace.works.notDeliveredState,
    partial: t.workspace.works.partialState,
    missing: t.workspace.works.missingState,
    unknown: t.workspace.works.fileInfoUnavailable,
  };
  return (
    <span
      data-testid="agent-delivery-state"
      data-delivery-state={state}
      className={cn(
        "shrink-0 rounded-full px-2 py-0.5 text-xs font-medium",
        state === "delivered"
          ? "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400"
          : state === "partial"
            ? "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400"
            : "bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300",
      )}
    >
      {labels[state]}
    </span>
  );
}

function WorkFileSummary({ work }: { work: WorkSummary }) {
  const { t } = useI18n();
  const task = work.source.kind === "video_task" ? work.source.task : null;
  const file = work.primaryFile ?? work.files[0] ?? null;
  if (work.agentDeliveryState === "unknown") {
    return (
      <div className="bg-muted text-muted-foreground rounded-lg px-3 py-2 text-xs">
        {t.workspace.works.fileInfoUnavailable}
      </div>
    );
  }
  return (
    <div className="bg-muted min-w-0 rounded-lg px-3 py-2 text-xs">
      <div className="truncate font-medium" title={file?.name ?? undefined}>
        {file?.name ??
          (task?.artifact_missing
            ? t.workspace.works.fileMissing
            : t.workspace.works.emptyFiles)}
      </div>
      <div className="text-muted-foreground mt-1 flex flex-wrap gap-x-2">
        <span>{formatFileSize(file?.size ?? null)}</span>
        {task?.artifact_missing ? <span>产物已清理</span> : null}
        {!task && file && !file.exists ? (
          <span>{t.workspace.works.fileMissing}</span>
        ) : null}
        {work.files.length > 1 ? (
          <span>{t.workspace.works.filesCount(work.files.length)}</span>
        ) : null}
        {!file?.name && work.primaryFile ? (
          <span>{t.workspace.works.byDownloadResponse}</span>
        ) : null}
      </div>
    </div>
  );
}

function AgentWorkActions({
  work,
  onOpen,
  onContinue,
}: {
  work: WorkSummary;
  onOpen: () => void;
  onContinue: (work: WorkSummary) => void;
}) {
  const { t } = useI18n();
  const file = work.primaryFile;
  return (
    <>
      {file?.exists && (file.previewUrl ?? file.downloadUrl) ? (
        <Button size="sm" onClick={onOpen}>
          {t.workspace.works.view}
        </Button>
      ) : null}
      {file?.exists && file.downloadUrl ? (
        <Button size="sm" variant="ghost" asChild>
          <a href={file.downloadUrl} download={file.name ?? ""}>
            {t.common.download}
          </a>
        </Button>
      ) : null}
      <Button
        size="sm"
        variant={file ? "ghost" : "outline"}
        onClick={() => onContinue(work)}
      >
        {t.workspace.works.open}
      </Button>
    </>
  );
}

export function TaskWorkActions({
  task,
  file,
  onOpen,
  onTaskNavigate,
  onDelete,
}: {
  task: VideoTaskBrief;
  file: WorkFile | null;
  onOpen: () => void;
  onTaskNavigate: (task: VideoTaskBrief, path?: "storyboard") => void;
  onDelete: (id: string) => void;
}) {
  const { t } = useI18n();
  const [confirmRetry, setConfirmRetry] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const recomposeMut = useRecomposeTask(task.id, {
    onEditStoryboard: () => onTaskNavigate(task, "storyboard"),
  });
  const retryAction = recomposeActionFor(task);
  const canView = Boolean(
    file?.exists && (file.previewUrl ?? file.downloadUrl),
  );

  return (
    <>
      <Button size="sm" disabled={!canView} onClick={onOpen}>
        {t.workspace.works.view}
      </Button>
      {file?.exists && file.downloadUrl ? (
        <Button size="sm" variant="ghost" asChild>
          <a href={file.downloadUrl} download={file.name ?? ""}>
            {t.common.download}
          </a>
        </Button>
      ) : null}
      <Button size="sm" variant="ghost" onClick={() => onTaskNavigate(task)}>
        {t.workspace.works.details}
      </Button>
      {task.status === "draft" && task.progress >= 100 ? (
        <Button
          size="sm"
          variant="outline"
          onClick={() => onTaskNavigate(task, "storyboard")}
        >
          {t.workspace.works.continueEditing}
        </Button>
      ) : null}
      {retryAction.visible && task.status === "failed" ? (
        <Button
          size="sm"
          variant="outline"
          disabled={recomposeMut.isPending || retryAction.disabled}
          onClick={() => setConfirmRetry(true)}
        >
          {recomposeMut.isPending ? "重试中…" : retryAction.label}
        </Button>
      ) : null}
      <Button
        size="sm"
        variant="ghost"
        aria-label={t.common.delete}
        className="text-muted-foreground hover:text-red-500"
        onClick={() => setConfirmDelete(true)}
      >
        {t.common.delete}
      </Button>
      <RecomposeConfirmDialog
        open={confirmRetry}
        onOpenChange={setConfirmRetry}
        isFailed
        onConfirm={() => recomposeMut.mutate()}
      />
      <AlertDialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t.workspace.works.deleteConfirmTitle}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t.workspace.works.deleteConfirmDescription}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t.common.cancel}</AlertDialogCancel>
            <AlertDialogAction
              className="bg-red-600 hover:bg-red-700"
              onClick={() => onDelete(task.id)}
            >
              {t.common.delete}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

export function WorkCard({
  work,
  onOpen,
  onContinue,
  onTaskNavigate,
  onDelete,
}: {
  work: WorkSummary;
  onOpen: () => void;
  onContinue: (work: WorkSummary) => void;
  onTaskNavigate: (task: VideoTaskBrief, path?: "storyboard") => void;
  onDelete: (id: string) => void;
}) {
  const { t } = useI18n();
  const task = work.source.kind === "video_task" ? work.source.task : null;
  const thread = work.source.kind === "agent" ? work.source.thread : null;
  const title = work.title.length > 0 ? work.title : t.workspace.works.unnamed;
  const parsedSortAt = work.sortAt ? parseTimestamp(work.sortAt) : null;
  const validSortAt = parsedSortAt && Number.isFinite(parsedSortAt.getTime());
  const typeLabel = task
    ? videoTaskTypeLabel(task.task_type)
    : t.workspace.home.courseItems[work.capability].title;

  return (
    <article
      data-testid="work-card"
      data-work-id={work.id}
      data-task-id={task?.id}
      data-thread-id={thread?.thread_id}
      className="bg-background flex h-full min-w-0 flex-col overflow-hidden rounded-2xl border shadow-sm"
    >
      <WorkMedia work={work} onOpen={onOpen} />
      <div className="flex flex-1 flex-col space-y-3 p-4">
        <div className="flex items-start justify-between gap-2">
          <span className="text-muted-foreground text-xs font-medium">
            {typeLabel}
          </span>
          {task ? (
            <StatusBadge status={task.status} progress={task.progress} />
          ) : (
            <AgentDeliveryBadge work={work} />
          )}
        </div>
        <h3 className="line-clamp-2 min-h-10 text-sm leading-5 font-semibold">
          {title}
        </h3>
        <WorkFileSummary work={work} />
        <p className="text-muted-foreground text-xs">
          {validSortAt && work.sortAt
            ? `${t.workspace.works.updated} ${formatDateTime(work.sortAt)}`
            : t.workspace.works.updatedUnknown}
        </p>
        {task?.error_message ? (
          <p className="text-xs break-words whitespace-pre-wrap text-red-600 dark:text-red-400">
            {task.error_message}
          </p>
        ) : null}
        <div
          data-testid="work-card-actions"
          className="mt-auto flex flex-wrap items-center gap-2 pt-1"
        >
          {task ? (
            <TaskWorkActions
              task={task}
              file={work.primaryFile}
              onOpen={onOpen}
              onTaskNavigate={onTaskNavigate}
              onDelete={onDelete}
            />
          ) : (
            <AgentWorkActions
              work={work}
              onOpen={onOpen}
              onContinue={onContinue}
            />
          )}
        </div>
      </div>
    </article>
  );
}
