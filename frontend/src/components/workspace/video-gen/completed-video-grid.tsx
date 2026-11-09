"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { formatDateTime, parseTimestamp } from "@/core/utils/datetime";
import { getVideoDownloadUrl } from "@/core/video-gen/api";
import { getVideoTaskStatusLabel } from "@/core/video-gen/task-status-label";
import type { VideoTaskBrief } from "@/core/video-gen/types";

import { TaskThumbnail } from "./task-thumbnail";

export const COMPLETED_WORK_PAGE_SIZE = 6;
export const COMPLETED_WORK_LIMIT = 100;

export function selectCompletedWorks(
  tasks: readonly VideoTaskBrief[],
): VideoTaskBrief[] {
  return tasks
    .filter(
      (task) =>
        task.status === "ready" &&
        (task.task_type === "ai_image" || task.task_type === "ai_video"),
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

function workTypeLabel(taskType: VideoTaskBrief["task_type"]): string {
  return taskType === "ai_video" ? "AI视频" : "图片动效";
}

function workUpdatedAt(task: VideoTaskBrief): string {
  const value = task.updated_at ?? task.created_at;
  if (!value || !Number.isFinite(parseTimestamp(value).getTime())) {
    return "更新时间未知";
  }
  return `最近更新 ${formatDateTime(value)}`;
}

export function CompletedVideoGrid({
  tasks,
  isLoading,
  isError,
  onRetry,
}: {
  tasks: VideoTaskBrief[];
  isLoading?: boolean;
  isError?: boolean;
  onRetry?: () => void;
}) {
  const [visibleCount, setVisibleCount] = useState(COMPLETED_WORK_PAGE_SIZE);
  const [previewTaskId, setPreviewTaskId] = useState<string | null>(null);
  const [failedPreviews, setFailedPreviews] = useState<ReadonlySet<string>>(
    () => new Set(),
  );

  if (isLoading) {
    return (
      <div
        role="status"
        className="rounded-2xl border border-dashed p-10 text-center"
      >
        正在加载作品…
      </div>
    );
  }
  if (isError) {
    return (
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
  }

  const works = selectCompletedWorks(tasks);
  if (works.length === 0) {
    return (
      <div
        role="status"
        className="rounded-2xl border border-dashed p-10 text-center"
      >
        暂无已完成作品
      </div>
    );
  }

  return (
    <div>
      <div
        data-testid="completed-work-grid"
        className="grid grid-cols-1 justify-start gap-5 sm:grid-cols-2 xl:grid-cols-3"
      >
        {works.slice(0, visibleCount).map((task) => {
          const actionable = canPreviewOrDownload(task);
          const previewOpen = previewTaskId === task.id && actionable;
          const previewFailed = failedPreviews.has(task.id);
          return (
            <article
              key={task.id}
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
                  <span className="shrink-0 rounded-full bg-emerald-50 px-2 py-1 text-xs text-emerald-700">
                    {getVideoTaskStatusLabel(task.status, task.progress)}
                  </span>
                </div>
                <p className="text-muted-foreground text-sm">
                  {workTypeLabel(task.task_type)}
                </p>
                <p className="text-muted-foreground text-xs">
                  {workUpdatedAt(task)}
                </p>
                <div className="flex items-center gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={!actionable}
                    onClick={() =>
                      setPreviewTaskId(previewOpen ? null : task.id)
                    }
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
                </div>
                {previewOpen ? (
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
                      onError={() =>
                        setFailedPreviews((current) =>
                          new Set(current).add(task.id),
                        )
                      }
                    />
                  )
                ) : null}
              </div>
            </article>
          );
        })}
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
    </div>
  );
}
