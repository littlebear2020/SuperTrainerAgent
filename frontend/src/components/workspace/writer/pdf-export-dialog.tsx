"use client";

import {
  CheckCircle2,
  Download,
  FileText,
  Loader2,
  XCircle,
} from "lucide-react";
import { useCallback, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { formatCost } from "@/core/utils/format";
import { getPdfDownloadUrl, submitPdfExport } from "@/core/writer/api";
import { useContentTask } from "@/core/writer/hooks";

// ── Props ────────────────────────────────────────────────────────────────

interface PdfExportDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  docId: string;
  docTitle: string;
}

// ── Component ────────────────────────────────────────────────────────────

/**
 * B4-T3: PDF export dialog. Mirrors PptExportDialog but simpler (no template
 * selection). Uses submitPdfExport + useContentTask + getPdfDownloadUrl.
 */
export function PdfExportDialog({
  open,
  onOpenChange,
  docId,
  docTitle,
}: PdfExportDialogProps) {
  const [taskId, setTaskId] = useState<string | undefined>(undefined);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const taskQuery = useContentTask(taskId);

  const handleSubmit = useCallback(async () => {
    setIsSubmitting(true);
    setSubmitError(null);
    try {
      const task = await submitPdfExport({ doc_id: docId, title: docTitle });
      setTaskId(task.id);
    } catch (err) {
      setSubmitError(
        err instanceof Error ? err.message : "提交失败，请稍后重试",
      );
    } finally {
      setIsSubmitting(false);
    }
  }, [docId, docTitle]);

  const handleClose = useCallback(() => {
    setTaskId(undefined);
    setSubmitError(null);
    onOpenChange(false);
  }, [onOpenChange]);

  const status = taskQuery.data?.status;
  const isComplete = status === "ready";
  const isFailed = status === "failed";

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <FileText className="h-5 w-5 text-rose-500" />
            导出 PDF
          </DialogTitle>
          <DialogDescription>
            将文档导出为 PDF 文件，支持中文、表格和图片
          </DialogDescription>
        </DialogHeader>

        {!taskId ? (
          <>
            <div className="space-y-3 py-4">
              <p className="text-muted-foreground text-xs">
                文档标题：{docTitle}
              </p>
              {submitError && (
                <p className="text-xs text-red-500">{submitError}</p>
              )}
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={handleClose}>
                取消
              </Button>
              <Button onClick={handleSubmit} disabled={isSubmitting}>
                {isSubmitting && (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                )}
                生成 PDF
              </Button>
            </DialogFooter>
          </>
        ) : isComplete ? (
          <div className="flex flex-col items-center gap-4 py-8">
            <CheckCircle2 className="h-12 w-12 text-green-500" />
            <p className="font-medium">生成完成</p>
            <p className="text-muted-foreground text-xs">
              花费：{formatCost(taskQuery.data?.cost, taskQuery.data?.currency)}
            </p>
            <Button
              onClick={() => {
                if (taskId)
                  window.open(
                    getPdfDownloadUrl(taskId),
                    "_blank",
                    "noopener,noreferrer",
                  );
              }}
            >
              <Download className="mr-2 h-4 w-4" />
              下载 PDF
            </Button>
            <Button variant="outline" size="sm" onClick={handleClose}>
              关闭
            </Button>
          </div>
        ) : isFailed ? (
          <div className="flex flex-col items-center gap-4 py-8">
            <XCircle className="h-12 w-12 text-red-500" />
            <p className="font-medium">生成失败</p>
            <p className="text-muted-foreground text-xs">
              {taskQuery.data?.error_message ?? "请稍后重试"}
            </p>
            <Button variant="outline" size="sm" onClick={handleClose}>
              关闭
            </Button>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-4 py-8">
            <Loader2 className="h-12 w-12 animate-spin text-indigo-500" />
            <p className="font-medium">正在生成 PDF...</p>
            <p className="text-muted-foreground text-xs">
              {taskQuery.data?.progress ?? 0}%
            </p>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
