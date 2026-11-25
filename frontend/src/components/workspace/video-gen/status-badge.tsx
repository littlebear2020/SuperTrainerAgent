import { Ban, CheckCircle2, Loader2, Pencil, XCircle } from "lucide-react";

import { getVideoTaskStatusLabel } from "@/core/video-gen/task-status-label";
import { cn } from "@/lib/utils";

/** 活跃态标签；通用 composing 显示「合成中」，主动重合成按钮自行显示 pending 文案。 */
export function getActiveStatusLabel(status: string): string {
  if (status === "running") return "处理中";
  if (status === "composing") return "合成中";
  return "排队中";
}

export function StatusBadge({
  status,
  progress,
}: {
  status: string;
  progress?: number;
}) {
  const draftReady = status === "draft" && (progress ?? 0) >= 100;
  const variant =
    status === "ready"
      ? "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400"
      : status === "failed"
        ? "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"
        : status === "cancelled"
          ? "bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300"
          : draftReady
            ? "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400"
            : "bg-indigo-100 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-400";
  const Icon =
    status === "ready"
      ? CheckCircle2
      : status === "failed"
        ? XCircle
        : status === "cancelled"
          ? Ban
          : draftReady
            ? Pencil
            : Loader2;
  const spin = !(
    status === "ready" ||
    status === "failed" ||
    status === "cancelled" ||
    draftReady
  );
  return (
    <span
      data-testid="work-status-badge"
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium",
        variant,
      )}
    >
      <Icon className={cn("h-3 w-3", spin && "animate-spin")} />
      {getVideoTaskStatusLabel(status, progress)}
    </span>
  );
}
