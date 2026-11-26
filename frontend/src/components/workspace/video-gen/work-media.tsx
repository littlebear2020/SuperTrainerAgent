"use client";

import {
  AudioLines,
  File as FileIcon,
  FileText,
  FileWarning,
  Image as ImageIcon,
  Video,
  type LucideIcon,
} from "lucide-react";

import { useI18n } from "@/core/i18n/hooks";
import type { WorkMediaKind, WorkSummary } from "@/core/works/types";

const MEDIA_ICONS: Record<WorkMediaKind, LucideIcon> = {
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
  const file = work.primaryFile;
  const task = work.source.kind === "video_task" ? work.source.task : null;
  const taskThumbnail =
    task?.status === "ready" && !task.artifact_missing
      ? task.thumbnail_url
      : null;
  const imageUrl =
    taskThumbnail ??
    (file?.exists && file.mediaKind === "image" ? file.previewUrl : null);
  const missing =
    task?.artifact_missing === true ||
    file?.exists === false ||
    (work.agentDeliveryState === "missing" && !file);
  const emptyLabel =
    work.agentDeliveryState === "unknown"
      ? t.workspace.works.fileInfoUnavailable
      : missing
        ? t.workspace.works.fileMissing
        : t.workspace.works.emptyFiles;
  const placeholderLabel =
    file?.name ??
    (file?.exists ? t.workspace.works.byDownloadResponse : emptyLabel);
  const Icon = missing
    ? FileWarning
    : file
      ? MEDIA_ICONS[file.mediaKind]
      : FileIcon;
  const title = work.title ? work.title : t.workspace.works.unnamed;
  const className =
    "bg-muted text-muted-foreground flex aspect-video w-full shrink-0 items-center justify-center overflow-hidden rounded-none";

  const content = imageUrl ? (
    <img
      src={imageUrl}
      alt={file?.name ?? title}
      loading="lazy"
      className={
        taskThumbnail
          ? "h-full w-full object-cover"
          : "h-full w-full object-contain"
      }
    />
  ) : (
    <div className="flex min-w-0 flex-col items-center justify-center gap-3 px-4 text-center">
      <Icon className="h-9 w-9" aria-hidden="true" />
      <span className="max-w-full truncate text-xs" title={placeholderLabel}>
        {placeholderLabel}
      </span>
    </div>
  );

  if (file?.exists && (file.previewUrl || file.downloadUrl)) {
    return (
      <button
        type="button"
        data-testid="work-media"
        data-media-kind={file.mediaKind}
        aria-label={t.workspace.works.viewWork(title)}
        onClick={onOpen}
        className={`${className} focus-visible:ring-ring cursor-pointer text-left focus-visible:ring-2 focus-visible:outline-none`}
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
