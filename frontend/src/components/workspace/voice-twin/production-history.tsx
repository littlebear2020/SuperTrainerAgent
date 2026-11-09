"use client";

import {
  ChevronUp,
  Download,
  FileAudio,
  Film,
  Loader2,
  Play,
  RotateCcw,
  Trash2,
  XCircle,
} from "lucide-react";
import { useCallback, useState } from "react";
import { toast } from "sonner";

import {
  buildConfirmOptions,
  useConfirm,
} from "@/components/workspace/dialog-provider";
import { useI18n } from "@/core/i18n/hooks";
import type { Translations } from "@/core/i18n/locales/types";
import { useGlassStyle } from "@/core/theme/glass";
import { formatFileSize as formatFileSizeShared } from "@/core/utils/format";
import { formatCost } from "@/core/utils/format";
import { getProductionDownloadURL } from "@/core/voice-twin/production-api";
import { getProductionStreamURL } from "@/core/voice-twin/production-api";
import {
  useDeleteProductionTask,
  useProductionTasks,
  useResumePptToVideo,
} from "@/core/voice-twin/production-hooks";
import type { ProductionTask } from "@/core/voice-twin/production-types";
import { productionTaskStatusLabel } from "@/core/voice-twin/status-labels";

function formatSize(bytes: number | null | undefined): string {
  return bytes ? formatFileSizeShared(bytes) : "";
}

function statusIcon(status: ProductionTask["status"]) {
  switch (status) {
    case "ready":
      return <FileAudio className="h-4 w-4 text-green-500" />;
    case "failed":
      return <XCircle className="h-4 w-4 text-red-500" />;
    default:
      return <Loader2 className="h-4 w-4 animate-spin text-indigo-500" />;
  }
}

function statusLabel(
  status: ProductionTask["status"],
  progress: number,
  vt: Translations["voiceTwin"],
): string {
  const label = productionTaskStatusLabel(status, vt);
  // Show the localized status label; append progress % while still processing.
  if (status === "ready" || status === "failed") return label;
  return `${label} ${progress}%`;
}

export function ProductionHistory() {
  const { t } = useI18n();
  const vt = t.voiceTwin;
  const { glassStyle } = useGlassStyle();
  const { data: tasks, isLoading } = useProductionTasks();
  const deleteTask = useDeleteProductionTask();
  const resumeTask = useResumePptToVideo();
  const [previewTaskId, setPreviewTaskId] = useState<string | null>(null);
  const confirm = useConfirm();

  const deleteConfirmMsg = vt.deleteTaskConfirm ?? "确定删除这个任务吗？";
  const deletedMsg = vt.taskDeleted ?? "任务已删除";

  const handleDelete = useCallback(
    async (taskId: string) => {
      // Confirm before deleting — a PPT video task can run for minutes and is
      // unrecoverable once removed. Matches voice-card.tsx delete behavior.
      const confirmed = await confirm(
        buildConfirmOptions(deleteConfirmMsg, { variant: "destructive" }),
      );
      if (!confirmed) return;
      if (previewTaskId === taskId) setPreviewTaskId(null);
      deleteTask.mutate(taskId, {
        onSuccess: () => toast.success(deletedMsg),
        onError: (e) =>
          toast.error(e instanceof Error ? e.message : "删除失败"),
      });
    },
    [confirm, deleteTask, previewTaskId, deleteConfirmMsg, deletedMsg],
  );

  const handleResume = useCallback(
    (taskId: string) => {
      resumeTask.mutate(taskId, {
        onError: (e) =>
          toast.error(e instanceof Error ? e.message : "继续生成失败"),
      });
    },
    [resumeTask],
  );

  if (isLoading) {
    return (
      <div className="rounded-xl p-4" style={glassStyle()}>
        <div className="space-y-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <div
              key={i}
              className="h-12 animate-pulse rounded-lg bg-slate-200/30 dark:bg-slate-700/20"
            />
          ))}
        </div>
      </div>
    );
  }

  if (!tasks || tasks.length === 0) {
    return null;
  }

  const previewTask = previewTaskId
    ? tasks.find((t) => t.id === previewTaskId)
    : null;

  return (
    <div className="rounded-xl p-4" style={glassStyle()}>
      <h3 className="mb-3 text-sm font-bold">
        {vt.productionHistory ?? "制作历史"}
      </h3>

      {/* Inline media player */}
      {previewTask?.status === "ready" && (
        <div className="mb-3">
          <MediaPlayerInline
            task={previewTask}
            onClose={() => setPreviewTaskId(null)}
          />
        </div>
      )}

      <div className="space-y-2">
        {tasks.map((task) => (
          <div
            key={task.id}
            className="flex items-center gap-3 rounded-lg border border-white/5 px-3 py-2"
          >
            {task.task_type === "ppt_to_video" &&
            task.status !== "ready" &&
            task.status !== "failed" ? (
              // PPT tasks in progress: show the spinner so the user sees
              // activity instead of a static icon (was always a flat Film icon).
              statusIcon(task.status)
            ) : task.task_type === "ppt_to_video" ? (
              <Film className="h-4 w-4 text-slate-400" />
            ) : (
              statusIcon(task.status)
            )}
            <div className="min-w-0 flex-1">
              <p className="truncate text-xs font-medium">
                {task.title ?? task.voice_name}
                {task.task_type === "ppt_to_video" ? " (视频)" : " (音频)"}
              </p>
              <p className="text-xs text-slate-400">
                {formatSize(task.file_size)}
                {task.created_at
                  ? ` · ${new Date(task.created_at).toLocaleString()}`
                  : ""}
                {/* D5 (2026-08-14): 花费只在终态显示，与状态段逻辑对齐 */}
                {(task.status === "ready" || task.status === "failed") &&
                  ` · ${t.console.colCost} ${formatCost(task.cost, task.currency)}`}
                {task.status !== "ready" &&
                  task.status !== "failed" &&
                  ` · ${statusLabel(task.status, task.progress, vt)}`}
                {task.status === "failed" && (
                  <span>
                    {" · "}
                    {statusLabel(task.status, task.progress, vt)}
                  </span>
                )}
                {task.status === "failed" && task.error_message && (
                  <span className="text-red-400">
                    {" "}
                    ·{" "}
                    {task.error_message
                      .replace(/^missing-font-detected:\s*/, "")
                      .slice(0, 80)}
                  </span>
                )}
              </p>
            </div>
            <div className="flex items-center gap-1">
              {task.status === "failed" &&
                task.error_message?.includes("missing-font-detected") && (
                  <button
                    type="button"
                    onClick={() => handleResume(task.id)}
                    disabled={resumeTask.isPending}
                    className="flex items-center gap-1 rounded px-2 py-1 text-xs text-amber-400 transition-colors hover:bg-amber-500/10 disabled:opacity-40"
                  >
                    {resumeTask.isPending ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <RotateCcw className="h-3 w-3" />
                    )}
                    <span>
                      {vt.acceptLayoutDiff ?? "接受版式差异，继续生成"}
                    </span>
                  </button>
                )}
              {task.status === "ready" && (
                <>
                  <button
                    type="button"
                    onClick={() =>
                      setPreviewTaskId(
                        previewTaskId === task.id ? null : task.id,
                      )
                    }
                    className="rounded p-1 text-slate-400 transition-colors hover:bg-white/5 hover:text-indigo-400"
                  >
                    {previewTaskId === task.id ? (
                      <ChevronUp className="h-4 w-4" />
                    ) : (
                      <Play className="h-4 w-4" />
                    )}
                  </button>
                  <a
                    href={getProductionDownloadURL(task.id)}
                    download
                    className="rounded p-1 text-slate-400 transition-colors hover:bg-white/5 hover:text-indigo-400"
                  >
                    <Download className="h-4 w-4" />
                  </a>
                </>
              )}
              <button
                type="button"
                onClick={() => handleDelete(task.id)}
                className="rounded p-1 text-slate-400 transition-colors hover:bg-white/5 hover:text-red-400"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function MediaPlayerInline({
  task,
  onClose,
}: {
  task: ProductionTask;
  onClose: () => void;
}) {
  const isVideo = task.task_type === "ppt_to_video";
  const streamUrl = getProductionStreamURL(task.id);

  return (
    <div className="rounded-lg border border-white/10 bg-black/20 p-2">
      <div className="mb-2 flex items-center justify-between">
        <p className="truncate text-xs text-slate-400">
          {task.title ?? task.voice_name}
          {isVideo ? " (视频)" : " (音频)"}
        </p>
        <button
          type="button"
          onClick={onClose}
          className="rounded p-0.5 text-slate-400 hover:text-white"
        >
          <ChevronUp className="h-3 w-3" />
        </button>
      </div>
      {isVideo ? (
        <video src={streamUrl} controls className="w-full rounded" />
      ) : (
        <audio src={streamUrl} controls className="w-full" />
      )}
    </div>
  );
}
