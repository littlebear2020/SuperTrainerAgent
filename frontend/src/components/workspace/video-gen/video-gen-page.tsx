"use client";

import {
  CheckCircle2,
  Loader2,
  Pencil,
  Play,
  RefreshCw,
  Trash2,
  XCircle,
} from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

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
import { AgentConversationGrid } from "@/components/workspace/video-gen/agent-conversation-grid";
import { CourseProjectPage } from "@/components/workspace/video-gen/course-project-page";
import { RecomposeConfirmDialog } from "@/components/workspace/video-gen/video-gen-recompose-dialog";
import { useI18n } from "@/core/i18n/hooks";
import { formatDateTime } from "@/core/utils/datetime";
import { formatCost } from "@/core/utils/format";
import {
  useDeleteVideoTask,
  useRecomposeTask,
  useVideoTasks,
  getStallWarning,
} from "@/core/video-gen/hooks";
import { recomposeActionFor } from "@/core/video-gen/resume-actions";
import {
  getVideoTaskStatusLabel,
  isTerminalVideoTaskStatus,
} from "@/core/video-gen/task-status-label";
import { videoTaskTypeLabel } from "@/core/video-gen/task-type-label";
import type { VideoTaskBrief } from "@/core/video-gen/types";
import {
  buildVideoAssetsHref,
  parseVideoAssetsRoute,
  type WorkCapability,
} from "@/core/workspace/video-assets-route";
import { WORK_CAPABILITIES } from "@/core/workspace/work-capabilities";
import { cn } from "@/lib/utils";

import { CompletedVideoGrid } from "./completed-video-grid";
import { TaskThumbnail } from "./task-thumbnail";
export { CreateTaskDialog, TYPE_CONFIG } from "./video-gen-create-dialog";
export { TaskThumbnail } from "./task-thumbnail";

const TABS = [
  { value: "works", label: "我的作品" },
  { value: "assets", label: "我的资产" },
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

function VideoWorkPanel({ workType }: { workType: "ai_image" | "ai_video" }) {
  const router = useRouter();
  const tasksQuery = useVideoTasks();
  const deleteTask = useDeleteVideoTask();
  return (
    <CompletedVideoGrid
      workType={workType}
      tasks={tasksQuery.data ?? []}
      isLoading={tasksQuery.isLoading}
      isError={tasksQuery.isError}
      onRetry={() => void tasksQuery.refetch()}
      onDelete={(id) => deleteTask.mutate(id)}
      onNavigate={(id, path) =>
        router.push(
          `/workspace/video-gen/${id}${path === "storyboard" ? "/storyboard" : ""}`,
        )
      }
    />
  );
}

export function VideoGenPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { t } = useI18n();
  const route = parseVideoAssetsRoute(searchParams);
  const tab = route.tab;
  const canonicalHref = buildVideoAssetsHref(route);
  const selectedCapability =
    route.tab === "works"
      ? (WORK_CAPABILITIES.find((item) => item.id === route.capability) ??
        WORK_CAPABILITIES[0]!)
      : null;

  useEffect(() => {
    const currentHref = `${window.location.pathname}${window.location.search}`;
    if (currentHref !== canonicalHref) {
      router.replace(canonicalHref);
    }
  }, [canonicalHref, router, searchParams]);

  const selectTab = (next: (typeof TABS)[number]["value"]) => {
    const nextRoute =
      next === "assets"
        ? { tab: next }
        : {
            tab: next,
            capability:
              route.tab === "works" ? route.capability : "course-video",
          };
    router.push(buildVideoAssetsHref(nextRoute));
    document.getElementById(`video-assets-tab-${next}`)?.focus();
  };

  const selectCapability = (next: WorkCapability) => {
    router.push(buildVideoAssetsHref({ tab: "works", capability: next }));
    document.getElementById(`work-capability-tab-${next}`)?.focus();
  };

  return (
    <div className="scrollbar-none flex size-full flex-col overflow-y-auto p-4 sm:p-6">
      <div className="mx-auto w-full max-w-6xl space-y-6">
        <h1 className="text-lg font-bold">视频资产</h1>
        <div
          role="tablist"
          aria-label="视频资产视图"
          className="border-border/50 bg-muted/30 flex w-fit max-w-full gap-1 rounded-lg border p-1"
        >
          {TABS.map((item) => (
            <button
              key={item.value}
              id={`video-assets-tab-${item.value}`}
              role="tab"
              aria-selected={tab === item.value}
              aria-controls={`video-assets-panel-${item.value}`}
              tabIndex={tab === item.value ? 0 : -1}
              onClick={() => selectTab(item.value)}
              onKeyDown={(event) => {
                let next: (typeof TABS)[number]["value"];
                if (event.key === "Home") next = "works";
                else if (event.key === "End") next = "assets";
                else if (
                  event.key === "ArrowLeft" ||
                  event.key === "ArrowRight"
                )
                  next = tab === "works" ? "assets" : "works";
                else return;
                event.preventDefault();
                selectTab(next);
              }}
              className={cn(
                "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                tab === item.value
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {item.label}
            </button>
          ))}
        </div>
        <div
          id={`video-assets-panel-${tab}`}
          role="tabpanel"
          aria-labelledby={`video-assets-tab-${tab}`}
        >
          {tab === "works" && selectedCapability ? (
            <div className="space-y-5">
              <div
                id="work-capability-tabs"
                role="tablist"
                aria-label={t.workspace.works.tabsLabel}
                className="border-border/50 bg-muted/30 flex w-full gap-1 overflow-x-auto rounded-lg border p-1"
              >
                {WORK_CAPABILITIES.map((item, index) => (
                  <button
                    key={item.id}
                    id={`work-capability-tab-${item.id}`}
                    type="button"
                    role="tab"
                    aria-selected={selectedCapability.id === item.id}
                    aria-controls={`work-capability-panel-${item.id}`}
                    tabIndex={selectedCapability.id === item.id ? 0 : -1}
                    onClick={() => selectCapability(item.id)}
                    onKeyDown={(event) => {
                      let nextIndex: number;
                      if (event.key === "Home") nextIndex = 0;
                      else if (event.key === "End")
                        nextIndex = WORK_CAPABILITIES.length - 1;
                      else if (event.key === "ArrowLeft")
                        nextIndex =
                          (index - 1 + WORK_CAPABILITIES.length) %
                          WORK_CAPABILITIES.length;
                      else if (event.key === "ArrowRight")
                        nextIndex = (index + 1) % WORK_CAPABILITIES.length;
                      else return;
                      event.preventDefault();
                      const next = WORK_CAPABILITIES[nextIndex];
                      if (next) selectCapability(next.id);
                    }}
                    className={cn(
                      "shrink-0 rounded-md px-3 py-1.5 text-sm font-medium whitespace-nowrap transition-colors",
                      selectedCapability.id === item.id
                        ? "bg-background text-foreground shadow-sm"
                        : "text-muted-foreground hover:text-foreground",
                    )}
                  >
                    {t.workspace.home.courseItems[item.id].title}
                  </button>
                ))}
              </div>
              <div
                id={`work-capability-panel-${selectedCapability.id}`}
                role="tabpanel"
                aria-labelledby={`work-capability-tab-${selectedCapability.id}`}
              >
                {selectedCapability.kind === "agent" ? (
                  <AgentConversationGrid
                    agentName={selectedCapability.agentName}
                    label={
                      t.workspace.home.courseItems[selectedCapability.id].title
                    }
                  />
                ) : (
                  <VideoWorkPanel
                    workType={selectedCapability.initialTaskType}
                  />
                )}
              </div>
            </div>
          ) : (
            <CourseProjectPage />
          )}
        </div>
      </div>
    </div>
  );
}
