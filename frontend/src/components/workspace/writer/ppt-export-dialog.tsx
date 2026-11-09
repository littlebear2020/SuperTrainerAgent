"use client";

import {
  CheckCircle2,
  Download,
  FileText,
  Loader2,
  Video,
  XCircle,
} from "lucide-react";
import { useRouter } from "next/navigation";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { getPptDownloadUrl } from "@/core/writer/api";
import { useContentTask, useSubmitPptExport } from "@/core/writer/hooks";

// ── Templates ────────────────────────────────────────────────────────────

const TEMPLATES = [
  { value: "default", label: "默认" },
  { value: "dark", label: "深色" },
  { value: "minimal", label: "极简" },
  { value: "training", label: "培训" },
] as const;

// ── Props ────────────────────────────────────────────────────────────────

interface PptExportDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  docId: string;
  docTitle: string;
}

// ── Component ────────────────────────────────────────────────────────────

export function PptExportDialog({
  open,
  onOpenChange,
  docId,
  docTitle,
}: PptExportDialogProps) {
  const router = useRouter();
  const [template, setTemplate] = useState<string>("default");
  const submitMut = useSubmitPptExport();
  const taskId = submitMut.data?.id;
  const taskQuery = useContentTask(taskId);

  const handleSubmit = useCallback(() => {
    submitMut.mutate({
      doc_id: docId,
      template: template as "default" | "dark" | "minimal" | "training",
      title: docTitle,
    });
  }, [submitMut, docId, template, docTitle]);

  const handleClose = useCallback(() => {
    submitMut.reset();
    onOpenChange(false);
  }, [submitMut, onOpenChange]);

  const status = taskQuery.data?.status;
  const progress = taskQuery.data?.progress ?? 0;
  const isComplete = status === "ready";
  const isFailed = status === "failed";
  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <FileText className="h-5 w-5 text-indigo-500" />
            导出 PPT
          </DialogTitle>
          <DialogDescription>
            选择模板风格，生成 PowerPoint 演示文稿
          </DialogDescription>
        </DialogHeader>

        {!taskId ? (
          <>
            <div className="space-y-3 py-4">
              <div className="space-y-2">
                <label className="text-sm font-medium">模板</label>
                <Select value={template} onValueChange={setTemplate}>
                  <SelectTrigger>
                    <SelectValue placeholder="选择模板" />
                  </SelectTrigger>
                  <SelectContent>
                    {TEMPLATES.map((t) => (
                      <SelectItem key={t.value} value={t.value}>
                        {t.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <p className="text-muted-foreground text-xs">
                文档标题：{docTitle}
              </p>
              {submitMut.isError && (
                <p className="text-xs text-red-500">
                  {submitMut.error instanceof Error
                    ? submitMut.error.message
                    : "生成失败，请稍后重试"}
                </p>
              )}
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={handleClose}>
                取消
              </Button>
              <Button onClick={handleSubmit} disabled={submitMut.isPending}>
                {submitMut.isPending && (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                )}
                {submitMut.isError ? "重试" : "生成 PPT"}
              </Button>
            </DialogFooter>
          </>
        ) : isComplete ? (
          <div className="flex flex-col items-center gap-4 py-8">
            <CheckCircle2 className="h-12 w-12 text-green-500" />
            <p className="font-medium">生成完成</p>
            <Button
              onClick={() => {
                window.open(
                  getPptDownloadUrl(taskId),
                  "_blank",
                  "noopener,noreferrer",
                );
              }}
            >
              <Download className="mr-2 h-4 w-4" />
              下载 PPT
            </Button>
            <Button
              variant="outline"
              onClick={() => {
                onOpenChange(false);
                router.push("/workspace/video-gen");
              }}
            >
              <Video className="mr-2 h-4 w-4" />
              转视频
            </Button>
          </div>
        ) : isFailed ? (
          <div className="flex flex-col items-center gap-4 py-8">
            <XCircle className="h-12 w-12 text-red-500" />
            <p className="font-medium">生成失败</p>
            <p className="text-muted-foreground text-xs">
              {taskQuery.data?.error_message ?? "未知错误"}
            </p>
            <Button variant="outline" onClick={handleClose}>
              关闭
            </Button>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-4 py-8">
            <Loader2 className="h-12 w-12 animate-spin text-indigo-500" />
            <p className="font-medium">正在生成...</p>
            <div className="bg-muted h-2 w-full max-w-xs overflow-hidden rounded-full">
              <div
                className="h-full rounded-full bg-indigo-500 transition-all duration-300"
                style={{ width: `${progress}%` }}
              />
            </div>
            <p className="text-muted-foreground text-xs">{progress}%</p>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
