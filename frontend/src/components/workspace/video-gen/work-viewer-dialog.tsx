"use client";

import { Download, FileText, Files } from "lucide-react";
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
import { cn } from "@/lib/utils";

import { formatFileSize } from "./work-media";

function FileDetails({ file }: { file: WorkFile }) {
  const { t } = useI18n();
  const lowerName = file.name?.toLocaleLowerCase() ?? "";
  const isPresentation =
    lowerName.endsWith(".pptx") || lowerName.endsWith(".ppt");
  return (
    <div className="bg-muted flex min-h-56 flex-col items-center justify-center rounded-xl border px-6 py-10 text-center">
      <FileText
        className="text-muted-foreground h-10 w-10"
        aria-hidden="true"
      />
      <p className="mt-4 max-w-full text-sm font-medium break-all">
        {file.name ?? t.workspace.works.byDownloadResponse}
      </p>
      <p className="text-muted-foreground mt-2 text-sm">
        {isPresentation
          ? t.workspace.works.powerPointHint
          : t.workspace.works.unsupportedPreview}
      </p>
    </div>
  );
}

function CurrentFileMedia({
  file,
  failed,
  onError,
}: {
  file: WorkFile;
  failed: boolean;
  onError: () => void;
}) {
  const { t } = useI18n();
  if (!file.exists) {
    return (
      <div
        role="status"
        className="bg-muted rounded-xl border p-10 text-center text-sm"
      >
        {t.workspace.works.fileMissing}
      </div>
    );
  }
  if (failed) {
    return (
      <div
        role="status"
        className="bg-muted rounded-xl border p-10 text-center text-sm"
      >
        {t.workspace.works.mediaError}
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
        onError={onError}
        className="aspect-video max-h-[60vh] w-full rounded-xl bg-black"
      />
    );
  }
  if (file.mediaKind === "audio" && file.previewUrl) {
    return (
      <audio
        key={file.id}
        src={file.previewUrl}
        controls
        preload="metadata"
        onError={onError}
        className="w-full"
      />
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
  return <FileDetails file={file} />;
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
          file={currentFile}
          failed={failedFileId === currentFile.id}
          onError={() => setFailedFileId(currentFile.id)}
        />
      ) : (
        <div className="bg-muted rounded-xl border border-dashed p-10 text-center text-sm">
          {t.workspace.works.emptyFiles}
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
            {work.files.map((file) => (
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
                <FileText className="h-4 w-4 shrink-0" aria-hidden="true" />
                <span className="min-w-0 flex-1 truncate">
                  {file.name ?? t.workspace.works.byDownloadResponse}
                </span>
                <span className="shrink-0 text-xs">
                  {file.modifiedAt
                    ? formatDateTime(file.modifiedAt)
                    : t.workspace.works.updatedUnknown}
                </span>
              </button>
            ))}
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
