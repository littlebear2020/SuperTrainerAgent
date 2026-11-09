"use client";

import {
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Film,
  Loader2,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Trash2,
  Upload,
  Volume2,
  XCircle,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

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
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { BetaBadge } from "@/components/workspace/beta-badge";
import {
  SpeedSliderControl,
  VolumeSliderControl,
} from "@/components/workspace/video-gen/audio-sliders";
import { CourseProjectPage } from "@/components/workspace/video-gen/course-project-page";
import { CourseProjectSelect } from "@/components/workspace/video-gen/course-project-select";
import { RecomposeConfirmDialog } from "@/components/workspace/video-gen/video-gen-recompose-dialog";
import { formatDateTime } from "@/core/utils/datetime";
import { formatCost } from "@/core/utils/format";
import { getVideoDownloadUrl } from "@/core/video-gen/api";
import {
  useBGMList,
  useProviders,
  useBGMUpload,
  useCourseProjects,
  useDefaultVoice,
  useDeleteVideoTask,
  useExtractScript,
  useRecomposeTask,
  useSubmitVideo,
  useVideoTasks,
  useVideoVoices,
  getStallWarning,
} from "@/core/video-gen/hooks";
import {
  OUTPUT_FORMAT_OPTIONS,
  type UiOutputFormat,
} from "@/core/video-gen/output-formats";
import {
  buildProviderOptions,
  DEFAULT_PROVIDER_VALUE,
  providerFieldForTaskType,
  providerValueToPayload,
} from "@/core/video-gen/provider-options";
import { recomposeActionFor } from "@/core/video-gen/resume-actions";
import {
  STYLE_PRESETS,
  styleAfterTypeChange,
  stylePreviewImage,
  taskStyleOptions,
} from "@/core/video-gen/style-presets";
import {
  SUBTITLE_STYLES,
  type SubtitleStyle,
  isSubtitleStyle,
} from "@/core/video-gen/subtitle-styles";
import {
  getVideoTaskStatusLabel,
  isTerminalVideoTaskStatus,
} from "@/core/video-gen/task-status-label";
import { videoTaskTypeLabel } from "@/core/video-gen/task-type-label";
import type { VideoTaskBrief } from "@/core/video-gen/types";
import { cn } from "@/lib/utils";

import { shouldShowStyleSelector } from "./style-visibility";
import { TaskThumbnail } from "./task-thumbnail";
import { CreateTaskDialog } from "./video-gen-create-dialog";
export { CreateTaskDialog, TYPE_CONFIG } from "./video-gen-create-dialog";
export { TaskThumbnail } from "./task-thumbnail";

// ── Task type tabs ──────────────────────────────────────────────────────

const TABS = [
  { value: "all", label: "全部" },
  { value: "ai_image", label: "AI 生图" },
  { value: "ai_video", label: "AI 生视频" },
  // T-F1（D1，2026-09-09 feedback-round1）：入口归一——「文字动效」提交即走
  // remotion_course 管线（course-standard 模板渲染）；原 chat-handoff 入口
  // 彻底隐藏（不留二级入口），Agent Chat 能力保留在 Agent 工作台。
  { value: "remotion_course", label: "文字动效" },
  // R2-2（2026-10-07，D4 拍板）：「文字动效（旧）」tab 整体下线——存量
  // chat-handoff 任务在「全部」tab 仍可见（task_type 联合类型与
  // task-type-label 的「文字动效（旧）」标注保留，防存量任务渲染崩溃）。
  { value: "vimax", label: "ViMax 故事" },
  { value: "projects", label: "项目" },
] as const;

// ── Status badges ───────────────────────────────────────────────────────

/** E16: 活跃态（running/pending/composing）的状态标签。composing 须显示"重新合成中"
 * （修前 fallback 一律显示"排队中"，recompose 时列表页误导）。纯函数便于测试。 */
export function getActiveStatusLabel(status: string): string {
  if (status === "running") return "处理中";
  if (status === "composing") return "重新合成中";
  return "排队中";
}

export function StatusBadge({
  status,
  progress,
}: {
  status: string;
  progress?: number;
}) {
  // Variant (color + icon) selection kept here; the LABEL string comes from the
  // shared helper so list + detail can never diverge.
  const draftReady = status === "draft" && (progress ?? 0) >= 100;
  const variant =
    status === "ready"
      ? "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400"
      : status === "failed"
        ? "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"
        : draftReady
          ? "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400"
          : "bg-indigo-100 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-400";
  const Icon =
    status === "ready"
      ? CheckCircle2
      : status === "failed"
        ? XCircle
        : draftReady
          ? Pencil
          : Loader2;
  const spin = !(status === "ready" || status === "failed" || draftReady);
  return (
    <span
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

// ── Task card ───────────────────────────────────────────────────────────

export function TaskCard({
  task,
  onDelete,
  previewTaskId,
  setPreviewTaskId,
  onNavigate,
}: {
  task: VideoTaskBrief;
  onDelete: (id: string) => void;
  previewTaskId: string | null;
  setPreviewTaskId: (id: string | null) => void;
  onNavigate: (id: string, path?: string) => void;
}) {
  // review-⑤/⑥（2026-09-14）：opts 注入 409 引导 action 的导航回调（onNavigate
  // 与「继续编辑分镜」同一路径——hooks 层不引 router 的口径不变）。
  const recomposeMut = useRecomposeTask(task.id, {
    onEditStoryboard: () => onNavigate(task.id, "storyboard"),
  });
  const retryAction = recomposeActionFor(task);
  // review-⑤：failed 重试先弹确认层（对齐 detail 页/分镜页口径）。
  const [confirmRetry, setConfirmRetry] = useState(false);
  // T-E5（R19）：类型显示名单一事实源（修前 remotion_course 落到"Remotion"混称）
  const typeLabel = videoTaskTypeLabel(task.task_type);

  return (
    <div
      className="border-border/50 flex cursor-pointer items-center justify-between rounded-lg border px-4 py-3 transition-colors hover:border-indigo-300 hover:bg-indigo-50/50 dark:hover:bg-indigo-950/10"
      onClick={() =>
        onNavigate(
          task.id,
          task.status === "draft" && task.progress >= 100
            ? "storyboard"
            : undefined,
        )
      }
    >
      <div className="flex items-center gap-3">
        <TaskThumbnail
          url={task.thumbnail_url}
          artifactMissing={task.artifact_missing}
        />
        <div>
          <div className="flex items-center gap-2">
            <p className="text-sm font-medium">{typeLabel}</p>
            <StatusBadge status={task.status} progress={task.progress} />
          </div>
          <p className="text-muted-foreground text-xs">
            {task.created_at ? formatDateTime(task.created_at) : ""}
            {/* D5 (2026-08-14): 花费只在终态显示——进行中恒显示「花费 —」误导。
                终态对齐后端 task_framework TERMINAL_STATUSES = {ready, failed,
                cancelled}：被取消的任务可能已产生真实花费，仍须可见。
                2026-08-19：字面量收拢为 isTerminalVideoTaskStatus（反馈1-项2）。 */}
            {isTerminalVideoTaskStatus(task.status) &&
              ` · 花费 ${formatCost(task.cost, task.currency)}`}
            {task.error_message && (
              <span className="ml-2 text-red-500">{task.error_message}</span>
            )}
          </p>
          {/* E8: 列表页也展示卡死降级提示（用户不点进详情也能看到） */}
          {(() => {
            const stall = getStallWarning(task);
            return stall ? (
              <p className="text-xs font-medium text-amber-600 dark:text-amber-400">
                {stall}
              </p>
            ) : null;
          })()}
        </div>
      </div>
      <div
        className="flex items-center gap-2"
        onClick={(e) => e.stopPropagation()}
      >
        {task.status === "running" && (
          <div className="bg-muted h-1.5 w-20 overflow-hidden rounded-full">
            <div
              className="h-full rounded-full bg-indigo-500 transition-all duration-300"
              style={{ width: `${task.progress}%` }}
            />
          </div>
        )}
        {task.status === "ready" && (
          <Button
            variant="ghost"
            size="sm"
            className="h-7 text-xs"
            onClick={() =>
              setPreviewTaskId(previewTaskId === task.id ? null : task.id)
            }
          >
            <Play className="mr-1 h-3 w-3" />
            {previewTaskId === task.id ? "收起" : "播放"}
          </Button>
        )}
        {/* failed 主行动:重试合成(断点续跑,复用盘面已生成素材)。
            修前列表对 failed 只有删除 → 用户被迫放弃全部已生成视觉。
            ⚠️ useRecomposeTask 必须留在 TaskCard 内(惰性 mutation,每卡片一实例);
            严禁提升到列表父组件——共享 taskId 会让所有卡片串到同一张。
            review-⑤（2026-09-14）：点击先弹确认层（修前为本仓唯一无确认直调的
            recompose 触发点）。 */}
        {retryAction.visible && task.status === "failed" && (
          <Button
            variant="ghost"
            size="sm"
            className="h-7 text-xs"
            disabled={recomposeMut.isPending}
            onClick={() => setConfirmRetry(true)}
          >
            <RefreshCw className="mr-1 h-3 w-3" />
            {recomposeMut.isPending ? "重试中…" : retryAction.label}
          </Button>
        )}
        {task.status === "draft" && task.progress >= 100 && (
          <Button
            size="sm"
            className="h-7 text-xs"
            onClick={() => onNavigate(task.id, "storyboard")}
          >
            <Pencil className="mr-1 h-3 w-3" />
            继续编辑分镜
          </Button>
        )}
        {task.status === "draft" && (task.progress ?? 0) < 100 && (
          <span className="text-muted-foreground flex items-center gap-1 text-xs">
            <Loader2 className="h-3 w-3 animate-spin" />
            生成分镜
          </span>
        )}
        <AlertDialog>
          <AlertDialogTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              className="text-muted-foreground h-7 hover:text-red-500"
            >
              <Trash2 className="h-3 w-3" />
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

        {/* review-⑤：failed 重试二次确认——共享弹层（与 detail 页同源） */}
        <RecomposeConfirmDialog
          open={confirmRetry}
          onOpenChange={setConfirmRetry}
          isFailed
          onConfirm={() => recomposeMut.mutate()}
        />
      </div>
    </div>
  );
}

// 2026-10-06 拆分批：CreateTaskDialog（含 StyleThumb/StylePreviewPanel 与 TABS/TYPE_CONFIG
// 常量）移至 video-gen-create-dialog.tsx；本模块 re-export 保既有导入方（测试/e2e）。
// ── Main page ───────────────────────────────────────────────────────────

export function VideoGenPage() {
  const router = useRouter();
  const [tab, setTab] = useState<string>("all");
  const [createOpen, setCreateOpen] = useState(false);
  const [previewTaskId, setPreviewTaskId] = useState<string | null>(null);
  const tasksQuery = useVideoTasks();
  const deleteMut = useDeleteVideoTask();

  // Templates/projects tabs render their content IN-PLACE within the shell
  // (see Tab content below) — do NOT early-return, that drops the header/tabs.

  const filtered =
    tab === "all"
      ? tasksQuery.data
      : tasksQuery.data?.filter((t) => t.task_type === tab);

  return (
    <div className="scrollbar-none flex size-full flex-col overflow-y-auto p-6">
      <div className="mx-auto w-full max-w-4xl space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="flex items-center gap-2 text-lg font-bold">
              <Film className="h-5 w-5 text-indigo-500" />
              视频
              <BetaBadge />
            </h1>
            <p className="text-muted-foreground mt-1 text-xs">
              AI 生图、AI 生视频、Remotion 渲染、ViMax 故事
            </p>
          </div>
          <Button onClick={() => setCreateOpen(true)} size="sm">
            <Plus className="mr-1 h-4 w-4" />
            新建任务
          </Button>
        </div>

        {/* Tabs */}
        <div className="border-border/50 bg-muted/30 flex gap-1 rounded-lg border p-1">
          {TABS.map((t) => (
            <button
              key={t.value}
              onClick={() => setTab(t.value)}
              className={cn(
                "flex items-center gap-1 rounded-md px-3 py-1.5 text-xs font-medium transition-colors",
                tab === t.value
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {t.label}
            </button>
          ))}
        </div>

        {/* Tab content — shell (header/tabs/new-task) always renders; only this area swaps */}
        {tab === "projects" ? (
          <CourseProjectPage
            onOpenTask={(taskId) =>
              router.push(`/workspace/video-gen/${taskId}`)
            }
          />
        ) : (
          <div className="space-y-2">
            {tasksQuery.isLoading && (
              <p className="text-muted-foreground py-8 text-center text-sm">
                加载中...
              </p>
            )}
            {filtered?.length === 0 && (
              <p className="text-muted-foreground py-8 text-center text-sm">
                暂无任务，点击&ldquo;新建任务&rdquo;开始
              </p>
            )}
            {filtered?.map((task) => (
              <div key={task.id}>
                <TaskCard
                  task={task}
                  onDelete={(id) => deleteMut.mutate(id)}
                  previewTaskId={previewTaskId}
                  setPreviewTaskId={setPreviewTaskId}
                  onNavigate={(id, path) =>
                    router.push(
                      `/workspace/video-gen/${id}${path ? `/${path}` : ""}`,
                    )
                  }
                />
                {previewTaskId === task.id && task.status === "ready" && (
                  <div className="border-border/50 mt-2 overflow-hidden rounded-lg border bg-black">
                    <video
                      controls
                      className="max-h-[50vh] w-full"
                      src={getVideoDownloadUrl(task.id)}
                    />
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      <CreateTaskDialog open={createOpen} onOpenChange={setCreateOpen} />
    </div>
  );
}
