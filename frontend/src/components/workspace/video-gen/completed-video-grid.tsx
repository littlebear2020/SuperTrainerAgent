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
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { formatDateTime, parseTimestamp } from "@/core/utils/datetime";
import { getVideoDownloadUrl } from "@/core/video-gen/api";
import { useRecomposeTask } from "@/core/video-gen/hooks";
import { recomposeActionFor } from "@/core/video-gen/resume-actions";
import { getVideoTaskStatusLabel } from "@/core/video-gen/task-status-label";
import { videoTaskTypeLabel } from "@/core/video-gen/task-type-label";
import type { VideoTaskBrief } from "@/core/video-gen/types";
import { cn } from "@/lib/utils";

import { TaskThumbnail } from "./task-thumbnail";
import { RecomposeConfirmDialog } from "./video-gen-recompose-dialog";

export const COMPLETED_WORK_PAGE_SIZE = 6;
export const COMPLETED_WORK_LIMIT = 100;

type WorkType = "ai_image" | "ai_video";

export function selectCompletedWorks(
  tasks: readonly VideoTaskBrief[],
): VideoTaskBrief[] {
  return tasks
    .filter((task) =>
      ["ai_image", "ai_visual", "ai_video"].includes(task.task_type),
    )
    .sort((a, b) => {
      const aTime = a.created_at
        ? parseTimestamp(a.created_at).getTime()
        : Number.NaN;
      const bTime = b.created_at
        ? parseTimestamp(b.created_at).getTime()
        : Number.NaN;
      const aValid = Number.isFinite(aTime);
      const bValid = Number.isFinite(bTime);
      if (aValid && bValid && aTime !== bTime) return bTime - aTime;
      if (aValid !== bValid) return aValid ? -1 : 1;
      return a.id.localeCompare(b.id);
    })
    .slice(0, COMPLETED_WORK_LIMIT);
}

export function canPreviewOrDownload(
  task: Pick<VideoTaskBrief, "status" | "artifact_missing">,
): boolean {
  return task.status === "ready" && task.artifact_missing !== true;
}

function workUpdatedAt(task: VideoTaskBrief): string {
  const value = task.updated_at ?? task.created_at;
  if (!value || !Number.isFinite(parseTimestamp(value).getTime())) {
    return "更新时间未知";
  }
  return `最近更新 ${formatDateTime(value)}`;
}

function CompletedWorkCard({
  task,
  previewOpen,
  previewFailed,
  onPreview,
  onPreviewError,
  onDelete,
  onNavigate,
}: {
  task: VideoTaskBrief;
  previewOpen: boolean;
  previewFailed: boolean;
  onPreview: () => void;
  onPreviewError: () => void;
  onDelete: (id: string) => void;
  onNavigate: (id: string, path?: "storyboard") => void;
}) {
  const recomposeMut = useRecomposeTask(task.id, {
    onEditStoryboard: () => onNavigate(task.id, "storyboard"),
  });
  const retryAction = recomposeActionFor(task);
  const [confirmRetry, setConfirmRetry] = useState(false);
  const actionable = canPreviewOrDownload(task);

  return (
    <article
      data-testid="completed-work-card"
      data-task-id={task.id}
      className="bg-background min-w-0 overflow-hidden rounded-2xl border shadow-sm"
    >
      <TaskThumbnail
        url={task.thumbnail_url}
        artifactMissing={task.artifact_missing}
        className="aspect-video h-auto w-full rounded-none"
      />
      <div className="space-y-3 p-4">
        <div className="flex items-start justify-between gap-3">
          <h3 className="min-w-0 truncate font-semibold">
            {task.title?.trim() ? task.title : "未命名作品"}
          </h3>
          <span
            className={cn(
              "shrink-0 rounded-full px-2 py-1 text-xs",
              task.status === "failed"
                ? "bg-red-50 text-red-700"
                : "bg-emerald-50 text-emerald-700",
            )}
          >
            {getVideoTaskStatusLabel(task.status, task.progress)}
          </span>
        </div>
        <p className="text-muted-foreground text-sm">
          {videoTaskTypeLabel(task.task_type)}
        </p>
        <p className="text-muted-foreground text-xs">{workUpdatedAt(task)}</p>
        {task.error_message ? (
          <p className="text-xs text-red-600">{task.error_message}</p>
        ) : null}
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={!actionable}
            onClick={onPreview}
          >
            查看
          </Button>
          {actionable ? (
            <Button variant="ghost" size="sm" asChild>
              <a href={getVideoDownloadUrl(task.id)} download>
                下载
              </a>
            </Button>
          ) : null}
          <Button variant="ghost" size="sm" onClick={() => onNavigate(task.id)}>
            详情
          </Button>
          {task.status === "draft" && task.progress >= 100 ? (
            <Button
              variant="outline"
              size="sm"
              onClick={() => onNavigate(task.id, "storyboard")}
            >
              继续编辑分镜
            </Button>
          ) : null}
          {retryAction.visible && task.status === "failed" ? (
            <Button
              variant="outline"
              size="sm"
              disabled={recomposeMut.isPending}
              onClick={() => setConfirmRetry(true)}
            >
              {recomposeMut.isPending ? "重试中…" : retryAction.label}
            </Button>
          ) : null}
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button
                variant="ghost"
                size="sm"
                aria-label="删除"
                className="text-muted-foreground hover:text-red-500"
              >
                删除
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>确认删除</AlertDialogTitle>
                <AlertDialogDescription>
                  确定要删除这个视频任务吗？此操作无法撤销。
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>取消</AlertDialogCancel>
                <AlertDialogAction
                  onClick={() => onDelete(task.id)}
                  className="bg-red-600 hover:bg-red-700"
                >
                  删除
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>
        {previewOpen && actionable ? (
          previewFailed ? (
            <div
              role="status"
              className="bg-muted rounded-lg p-4 text-center text-sm"
            >
              视频预览暂不可用
            </div>
          ) : (
            <video
              className="aspect-video w-full rounded-lg bg-black"
              src={getVideoDownloadUrl(task.id)}
              controls
              preload="none"
              onError={onPreviewError}
            />
          )
        ) : null}
        <RecomposeConfirmDialog
          open={confirmRetry}
          onOpenChange={setConfirmRetry}
          isFailed
          onConfirm={() => recomposeMut.mutate()}
        />
      </div>
    </article>
  );
}

function CompletedVideoGridContent({
  workType,
  tasks,
  isLoading,
  isError,
  onRetry,
  onDelete,
  onNavigate,
}: {
  workType: WorkType;
  tasks: VideoTaskBrief[];
  isLoading?: boolean;
  isError?: boolean;
  onRetry?: () => void;
  onDelete: (id: string) => void;
  onNavigate: (id: string, path?: "storyboard") => void;
}) {
  const [visibleCount, setVisibleCount] = useState(COMPLETED_WORK_PAGE_SIZE);
  const [previewTaskId, setPreviewTaskId] = useState<string | null>(null);
  const [failedPreviews, setFailedPreviews] = useState<ReadonlySet<string>>(
    () => new Set(),
  );

  const allWorks = selectCompletedWorks(tasks);
  const works = allWorks.filter((task) =>
    workType === "ai_image"
      ? task.task_type === "ai_image" || task.task_type === "ai_visual"
      : task.task_type === "ai_video",
  );

  let content;
  if (isLoading) {
    content = (
      <div
        role="status"
        className="rounded-2xl border border-dashed p-10 text-center"
      >
        正在加载作品…
      </div>
    );
  } else if (isError) {
    content = (
      <div
        role="alert"
        className="rounded-2xl border border-dashed p-10 text-center"
      >
        <p>作品加载失败，请稍后重试</p>
        {onRetry ? (
          <Button variant="outline" className="mt-4" onClick={onRetry}>
            重试
          </Button>
        ) : null}
      </div>
    );
  } else if (works.length === 0) {
    content = (
      <div
        role="status"
        className="rounded-2xl border border-dashed p-10 text-center"
      >
        暂无{videoTaskTypeLabel(workType)}作品
      </div>
    );
  } else {
    content = (
      <>
        <div
          data-testid="completed-work-grid"
          className="grid grid-cols-1 justify-start gap-5 sm:grid-cols-2 xl:grid-cols-3"
        >
          {works.slice(0, visibleCount).map((task) => (
            <CompletedWorkCard
              key={task.id}
              task={task}
              previewOpen={previewTaskId === task.id}
              previewFailed={failedPreviews.has(task.id)}
              onPreview={() =>
                setPreviewTaskId(previewTaskId === task.id ? null : task.id)
              }
              onPreviewError={() =>
                setFailedPreviews((current) => new Set(current).add(task.id))
              }
              onDelete={onDelete}
              onNavigate={onNavigate}
            />
          ))}
        </div>
        {visibleCount < works.length ? (
          <div className="mt-6 text-center">
            <Button
              variant="outline"
              onClick={() =>
                setVisibleCount((count) =>
                  Math.min(
                    count + COMPLETED_WORK_PAGE_SIZE,
                    COMPLETED_WORK_LIMIT,
                  ),
                )
              }
            >
              加载更多
            </Button>
          </div>
        ) : null}
      </>
    );
  }

  return <div>{content}</div>;
}

export function CompletedVideoGrid(props: {
  workType: WorkType;
  tasks: VideoTaskBrief[];
  isLoading?: boolean;
  isError?: boolean;
  onRetry?: () => void;
  onDelete: (id: string) => void;
  onNavigate: (id: string, path?: "storyboard") => void;
}) {
  return <CompletedVideoGridContent key={props.workType} {...props} />;
}
