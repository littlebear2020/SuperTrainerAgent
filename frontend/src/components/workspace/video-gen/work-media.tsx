"use client";

import {
  AudioLines,
  File as FileIcon,
  FileText,
  Image as ImageIcon,
  Video,
  type LucideIcon,
} from "lucide-react";
import { useState } from "react";

import { useI18n } from "@/core/i18n/hooks";
import { selectWorkCoverFile } from "@/core/works/model";
import type { WorkMediaKind, WorkSummary } from "@/core/works/types";

import { WorkTypeCover } from "./work-type-cover";

export const MEDIA_ICONS: Record<WorkMediaKind, LucideIcon> = {
  video: Video,
  image: ImageIcon,
  audio: AudioLines,
  document: FileText,
  other: FileIcon,
};

export function formatFileSize(size: number | null): string {
  if (size === null) return "—";
  if (size < 1024) return `${size} B`;
  const units = ["KB", "MB", "GB", "TB"] as const;
  let value = size / 1024;
  let unit: (typeof units)[number] = units[0];
  for (let index = 1; index < units.length && value >= 1024; index += 1) {
    value /= 1024;
    unit = units[index]!;
  }
  return `${Number(value.toFixed(1))} ${unit}`;
}

export function WorkMedia({
  work,
  onOpen,
}: {
  work: WorkSummary;
  onOpen: () => void;
}) {
  const { t } = useI18n();
  // 按 URL 记录加载失败（复用 TaskThumbnail 的 failedUrl 模式）：同一 URL
  // 不循环重试，URL 变化后允许重新加载。
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const file = work.primaryFile;
  const task = work.source.kind === "video_task" ? work.source.task : null;
  const missing =
    task?.artifact_missing === true ||
    file?.exists === false ||
    (work.agentDeliveryState === "missing" && !file);
  const unknown = work.agentDeliveryState === "unknown";
  const coverFile = selectWorkCoverFile(work);
  // ready 且 artifact_missing 时在计算任何 URL 之前拦截：缩略图/成片/下载零请求。
  const taskThumbnail =
    !missing && task?.status === "ready" && !task.artifact_missing
      ? task.thumbnail_url
      : null;
  const imageUrl = missing
    ? null
    : (taskThumbnail ?? coverFile?.previewUrl ?? null);
  const failed = imageUrl !== null && failedUrl === imageUrl;
  const coverState = missing
    ? "missing"
    : unknown
      ? "unknown"
      : !file
        ? "empty"
        : "default";
  const title = work.title ? work.title : t.workspace.works.unnamed;
  const className =
    "bg-muted text-muted-foreground flex aspect-video w-full shrink-0 items-center justify-center overflow-hidden rounded-none";

  const content =
    imageUrl && !failed ? (
      <img
        src={imageUrl}
        alt={
          coverFile
            ? (coverFile.name ?? t.workspace.works.coverImageAlt(title))
            : (file?.name ?? "")
        }
        loading="lazy"
        decoding="async"
        onError={() => setFailedUrl(imageUrl)}
        className={
          taskThumbnail
            ? "h-full w-full object-cover"
            : "h-full w-full object-contain"
        }
      />
    ) : (
      <WorkTypeCover
        capability={work.capability}
        file={coverFile ?? file}
        state={coverState}
      />
    );

  if (file?.exists && (file.previewUrl || file.downloadUrl)) {
    return (
      <button
        type="button"
        data-testid="work-media"
        data-media-kind={file.mediaKind}
        aria-label={t.workspace.works.viewWork(title)}
        onClick={onOpen}
        className={`${className} cursor-pointer text-left focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:outline-none focus-visible:ring-inset`}
      >
        {content}
      </button>
    );
  }
  return (
    <div
      data-testid="work-media"
      data-media-kind={file?.mediaKind ?? "other"}
      className={className}
    >
      {content}
    </div>
  );
}
