"use client";

import { Download, Files } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useI18n } from "@/core/i18n/hooks";
import { formatDateTime } from "@/core/utils/datetime";
import type { VideoTaskBrief } from "@/core/video-gen/types";
import type { WorkFile, WorkSummary } from "@/core/works/types";
import type { WorkCapability } from "@/core/workspace/video-assets-route";
import { cn } from "@/lib/utils";

import { formatFileSize, MEDIA_ICONS } from "./work-media";
import { WorkTypeCover, type WorkCoverState } from "./work-type-cover";

const DETAILS_SURFACE =
  "bg-muted/30 flex min-h-56 flex-col items-center justify-center gap-4 rounded-xl border px-6 py-8 text-center";

function FileDetails({
  file,
  capability,
  state,
}: {
  file: WorkFile;
  capability: WorkCapability;
  state: WorkCoverState;
}) {
  const { t } = useI18n();
  const lowerName = file.name?.toLocaleLowerCase() ?? "";
  const isPresentation =
    lowerName.endsWith(".pptx") || lowerName.endsWith(".ppt");
  return (
    <div className={DETAILS_SURFACE}>
      <div className="w-full max-w-md">
        <WorkTypeCover
          capability={capability}
          file={file}
          state={state}
          className="rounded-xl"
        />
      </div>
      <div>
        <p className="max-w-full text-sm font-medium break-all">
          {file.name ?? t.workspace.works.byDownloadResponse}
        </p>
        <p className="text-muted-foreground mt-2 text-sm">
          {isPresentation
            ? t.workspace.works.powerPointHint
            : t.workspace.works.unsupportedPreview}
        </p>
      </div>
    </div>
  );
}

function CurrentFileMedia({
  file,
  capability,
  failed,
  onError,
}: {
  file: WorkFile;
  capability: WorkCapability;
  failed: boolean;
  onError: () => void;
}) {
  const { t } = useI18n();
  const [legacyImageFallback, setLegacyImageFallback] = useState(false);
  if (!file.exists) {
    return (
      <div role="status" className={DETAILS_SURFACE}>
        <div className="w-full max-w-md">
          <WorkTypeCover
            capability={capability}
            file={file}
            state="missing"
            className="rounded-xl"
          />
        </div>
      </div>
    );
  }
  if (failed) {
    return (
      <div role="status" className={DETAILS_SURFACE}>
        <div className="w-full max-w-md">
          <WorkTypeCover
            capability={capability}
            file={file}
            state="default"
            className="rounded-xl"
          />
        </div>
        <p className="text-sm text-amber-700 dark:text-amber-400">
          {t.workspace.works.mediaError}
        </p>
      </div>
    );
  }
  if (
    legacyImageFallback &&
    file.mediaKindGuessed === true &&
    file.previewUrl
  ) {
    return (
      <div className="flex max-h-[60vh] justify-center overflow-hidden rounded-xl bg-black">
        <img
          src={file.previewUrl}
          alt={t.workspace.works.byDownloadResponse}
          onError={onError}
          className="max-h-[60vh] max-w-full object-contain"
        />
      </div>
    );
  }
  if (file.mediaKind === "video" && file.previewUrl) {
    return (
      <video
        key={file.id}
        src={file.previewUrl}
        controls
        preload="metadata"
        onError={
          file.mediaKindGuessed === true
            ? () => setLegacyImageFallback(true)
            : onError
        }
        className="aspect-video max-h-[60vh] w-full rounded-xl bg-black"
      />
    );
  }
  if (file.mediaKind === "audio" && file.previewUrl) {
    return (
      <div className="flex flex-col gap-4">
        <div className="mx-auto w-full max-w-md">
          <WorkTypeCover
            capability={capability}
            file={file}
            state="default"
            className="rounded-xl"
          />
        </div>
        <audio
          key={file.id}
          src={file.previewUrl}
          controls
          preload="metadata"
          onError={onError}
          className="w-full"
        />
      </div>
    );
  }
  if (file.mediaKind === "image" && file.previewUrl) {
    return (
      <div className="flex max-h-[60vh] justify-center overflow-hidden rounded-xl bg-black">
        <img
          key={file.id}
          src={file.previewUrl}
          alt={file.name ?? ""}
          onError={onError}
          className="max-h-[60vh] max-w-full object-contain"
        />
      </div>
    );
  }
  return <FileDetails file={file} capability={capability} state="default" />;
}

function WorkViewerContent({
  work,
  onContinue,
  onTaskNavigate,
}: {
  work: WorkSummary;
  onContinue: (work: WorkSummary) => void;
  onTaskNavigate: (task: VideoTaskBrief) => void;
}) {
  const { t } = useI18n();
  const [selectedFileId, setSelectedFileId] = useState<string | null>(null);
  const [failedFileId, setFailedFileId] = useState<string | null>(null);
  const currentFile =
    work.files.find((file) => file.id === selectedFileId) ??
    work.primaryFile ??
    work.files[0] ??
    null;
  const rawTitle = work.title;
  const title = rawTitle.length > 0 ? rawTitle : t.workspace.works.unnamed;

  return (
    <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-4xl">
      <DialogHeader className="pr-8">
        <DialogTitle className="text-xl">{title}</DialogTitle>
        <DialogDescription className="break-all">
          {currentFile
            ? `${currentFile.name ?? t.workspace.works.byDownloadResponse} · ${formatFileSize(currentFile.size)}`
            : t.workspace.works.emptyFiles}
        </DialogDescription>
      </DialogHeader>

      {currentFile ? (
        <CurrentFileMedia
          key={currentFile.id}
          file={currentFile}
          capability={work.capability}
          failed={failedFileId === currentFile.id}
          onError={() => setFailedFileId(currentFile.id)}
        />
      ) : (
        <div className="bg-muted/30 rounded-xl border border-dashed p-6 text-center text-sm">
          <div className="mx-auto w-full max-w-md">
            <WorkTypeCover
              capability={work.capability}
              file={null}
              state="empty"
              className="rounded-xl"
            />
          </div>
          <p className="text-muted-foreground mt-3">
            {t.workspace.works.emptyFiles}
          </p>
        </div>
      )}

      {work.files.length > 1 ? (
        <div className="rounded-xl border p-3">
          <div className="mb-2 flex items-center gap-2 px-1 text-sm font-medium">
            <Files className="h-4 w-4" aria-hidden="true" />
            {t.workspace.works.deliveryFiles}
            <span className="text-muted-foreground text-xs">
              {t.workspace.works.filesCount(work.files.length)}
            </span>
          </div>
          <div className="space-y-1">
            {work.files.map((file) => {
              // 按 mediaKind 取图标：按扩展名猜会把 SVG 显示成图片图标。
              const FileIcon = MEDIA_ICONS[file.mediaKind];
              return (
                <button
                  key={file.id}
                  type="button"
                  aria-pressed={currentFile?.id === file.id}
                  onClick={() => setSelectedFileId(file.id)}
                  className={cn(
                    "flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm",
                    currentFile?.id === file.id
                      ? "bg-accent text-accent-foreground"
                      : "text-muted-foreground hover:bg-accent/50",
                  )}
                >
                  <FileIcon className="h-4 w-4 shrink-0" aria-hidden="true" />
                  <span className="min-w-0 flex-1 truncate">
                    {file.name ?? t.workspace.works.byDownloadResponse}
                  </span>
                  <span className="shrink-0 text-xs">
                    {file.modifiedAt
                      ? formatDateTime(file.modifiedAt)
                      : t.workspace.works.updatedUnknown}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      ) : null}

      <DialogFooter className="border-t pt-4">
        {work.source.kind === "agent" ? (
          <Button variant="ghost" onClick={() => onContinue(work)}>
            {t.workspace.works.open}
          </Button>
        ) : (
          <Button
            variant="ghost"
            onClick={() => {
              if (work.source.kind === "video_task") {
                onTaskNavigate(work.source.task);
              }
            }}
          >
            {t.workspace.works.details}
          </Button>
        )}
        {currentFile?.exists && currentFile.downloadUrl ? (
          <Button asChild>
            <a href={currentFile.downloadUrl} download={currentFile.name ?? ""}>
              <Download className="mr-2 h-4 w-4" aria-hidden="true" />
              {t.workspace.works.downloadCurrent}
            </a>
          </Button>
        ) : null}
      </DialogFooter>
    </DialogContent>
  );
}

export function WorkViewerDialog({
  work,
  open,
  onOpenChange,
  onContinue,
  onTaskNavigate,
}: {
  work: WorkSummary | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onContinue: (work: WorkSummary) => void;
  onTaskNavigate: (task: VideoTaskBrief) => void;
}) {
  return (
    <Dialog open={open && work !== null} onOpenChange={onOpenChange}>
      {open && work ? (
        <WorkViewerContent
          key={work.id}
          work={work}
          onContinue={onContinue}
          onTaskNavigate={onTaskNavigate}
        />
      ) : null}
    </Dialog>
  );
}
