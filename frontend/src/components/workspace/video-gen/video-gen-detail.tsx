"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Film,
  Loader2,
  Pencil,
  RefreshCw,
  XCircle,
} from "lucide-react";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  SpeedSliderControl,
  VolumeSliderControl,
} from "@/components/workspace/video-gen/audio-sliders";
import { StatusBadge } from "@/components/workspace/video-gen/video-gen-page";
import { RecomposeConfirmDialog } from "@/components/workspace/video-gen/video-gen-recompose-dialog";
import { formatDateTime } from "@/core/utils/datetime";
import { formatCost } from "@/core/utils/format";
import { getStoryboard, getVideoDownloadUrl } from "@/core/video-gen/api";
import { resolveBgmLabel } from "@/core/video-gen/bgm-filter";
import {
  useBGMList,
  useCourseProject,
  useRegenerateAllAudio,
  useRedoVimaxShot,
  useRecomposeTask,
  useReRenderTask,
  useRenderFromDraft,
  useReorderFrames,
  useStoryboardWithAudioPolling,
  useSubtitle,
  useTaskEvents,
  useUpdateStoryboardConfig,
  useUpdateSubtitle,
  useVideoTask,
  useVideoVoices,
  getStallWarning,
  STORYBOARD_KEY,
  VIDEO_KEY,
} from "@/core/video-gen/hooks";
import { parseUnavailableFrames } from "@/core/video-gen/recomposeError";
import {
  clearFrameRegenState,
  isRegenResolved,
  isRegenTimedOut,
} from "@/core/video-gen/regenTrack";
import { recomposeActionFor } from "@/core/video-gen/resume-actions";
import {
  hasVimaxShotManifest,
  resolveStoryboardCounts,
} from "@/core/video-gen/storyboard-counts";
import { carryLabelFor } from "@/core/video-gen/style-presets";
import {
  SUBTITLE_STYLES,
  isSubtitleStyle,
  subtitleStyleLabel,
  subtitleStylePreviewClass,
} from "@/core/video-gen/subtitle-styles";
import {
  getVideoTaskStatusLabel,
  isTerminalVideoTaskStatus,
} from "@/core/video-gen/task-status-label";
import { videoTaskTypeLabel } from "@/core/video-gen/task-type-label";
import type {
  RefInjectionRecord,
  StoryboardData,
} from "@/core/video-gen/types";
import { usePreviewTracker } from "@/core/video-gen/usePreviewTracker";
import { cn } from "@/lib/utils";

import { AudioPreviewBar } from "./audio-preview-bar";
import { SceneGroupProgress } from "./scene-group-progress";
import { AudioSettingsSection } from "./video-gen-detail-audio-settings";
import { SubtitleEditSection } from "./video-gen-detail-subtitle-edit";
import { DetailWarningBanner } from "./video-gen-detail-warning-banner";
import { FrameOpsPanel } from "./video-gen-frame-ops-panel";
import { FrameStrip } from "./video-gen-frame-strip";
import { VimaxStageIndicator } from "./vimax-stage-indicator";

// ── Info row ──────────────────────────────────────────────────────────────

/** C7（决策 5/14）：effective_*_source 留痕枚举翻译。旧任务缺键 → null（InfoRow 显示「—」）。 */
function effectiveSourceLabel(v: unknown): string | null {
  const labels: Record<string, string> = {
    project: "跟随项目",
    task: "任务自选",
    lang_default: "语言默认",
    config: "系统默认",
    default: "默认",
    none: "默认",
  };
  if (typeof v !== "string" || !v) return null;
  return labels[v] ?? v;
}

function InfoRow({
  label,
  value,
}: {
  label: string;
  value: string | number | null | undefined;
}) {
  if (value == null || value === "") return null;
  return (
    <div className="flex items-center gap-2 text-sm">
      <span className="text-muted-foreground w-20 shrink-0">{label}</span>
      <span className="font-medium">{String(value)}</span>
    </div>
  );
}

// ── Task 17: ViMax 逐镜重做 ───────────────────────────────────────────────
function VimaxShotRedo({
  taskId,
  shots,
  shotCount,
  referenceImage,
}: {
  taskId: string;
  shots: Record<string, { prompt?: string }>;
  shotCount: number;
  referenceImage?: string;
}) {
  const redoMut = useRedoVimaxShot(taskId);
  const entries = Object.entries(shots);
  return (
    <div className="space-y-2 rounded-lg border p-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">
          ViMax 镜头（{shotCount}，可逐镜重做）
        </h2>
        {/* Task 20b: 项目角色参考缩略图，肉眼校验跨镜一致性 */}
        {referenceImage && (
          <span
            className="text-muted-foreground inline-flex items-center gap-1 text-[10px]"
            title="项目角色参考。角色不像？点「重做此镜」"
          >
            <img
              loading="lazy"
              src={referenceImage}
              alt="角色参考"
              className="h-6 w-6 rounded object-cover"
            />
            角色参考
          </span>
        )}
      </div>
      <div className="space-y-1">
        {entries.map(([id, shot]) => {
          const shotId = Number(id);
          return (
            <div
              key={id}
              className="flex items-center gap-2 rounded-md border p-2 text-xs"
            >
              <span className="font-medium">镜 {shotId + 1}</span>
              <span className="text-muted-foreground flex-1 truncate">
                {shot.prompt ?? ""}
              </span>
              <Button
                size="sm"
                variant="outline"
                className="h-6 text-xs"
                disabled={redoMut.isPending}
                onClick={() => redoMut.mutate(shotId)}
              >
                <RefreshCw className="mr-1 h-3 w-3" />
                重做此镜
              </Button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────

export function VideoGenDetailPage() {
  const params = useParams<{ taskId: string }>();
  const router = useRouter();
  const [audioGenerating, setAudioGenerating] = useState(false);
  // M8: recompose 宽限轮询——派发后 60s 内持续 2s 轮询直到 status 翻 composing
  // （翻过去之后由既有 composing 轮询接管；composing→ready 跳变由 F4 effect 刷新视频）。
  const recomposeGraceUntilRef = useRef(0);
  const {
    data: task,
    isLoading,
    isError,
  } = useVideoTask(params.taskId, {
    pollWhile: () => Date.now() < recomposeGraceUntilRef.current,
  });
  // 曲库：把 payload 里的裸 bgm id 映射成中文名，并识别已下架的旧曲
  const bgmList = useBGMList();
  // Task 20b: ViMax 分镜重做旁展示项目角色参考，肉眼校验跨镜一致性。
  const projectQ = useCourseProject(task?.course_project_id ?? null);
  const courseId = task?.course_project_id;
  // A4/F3: <img> 不能直接读绝对本地路径；用 serve-by-index GET route（i2i 参考后端从项目 store 解析）。
  const characterFrontUrl =
    // B3 修复（复核阻断项）：serve 已改 id 寻址（M1），下标 "0" 会被当 id 查 → 恒 404。
    // 用 sheets[0].id（存量项目无 id 时后端 store 读路径已固化回填 id，B1）。
    projectQ.data?.character_sheets?.[0]?.id && courseId
      ? `/api/video/projects/${courseId}/character-sheets/${encodeURIComponent(projectQ.data.character_sheets[0].id)}/front`
      : undefined;
  const showTimeline =
    task?.status === "ready" ||
    task?.status === "draft" ||
    task?.status === "composing";
  const qc = useQueryClient();
  // H4: 单帧重生追踪（复用 preview 的泛型 Set tracker）——RQv5 共享 mutation 实例只跟踪
  // 最新一次 mutate；重生改异步后，释放由 storyboard 数据（regen_state）驱动。
  // 注：须声明在 storyboard 轮询之前——第 4 参读 regenPending.size（TDZ）。
  const {
    pending: regenPending,
    track: trackRegenRaw,
    release: releaseRegen,
  } = usePreviewTracker();
  const regenStartedAtRef = useRef(new Map<number, number>());
  const trackRegen = useCallback(
    (i: number) => {
      regenStartedAtRef.current.set(i, Date.now());
      qc.setQueryData<StoryboardData>([STORYBOARD_KEY, params.taskId], (old) =>
        clearFrameRegenState(old, i),
      );
      trackRegenRaw(i);
    },
    [qc, params.taskId, trackRegenRaw],
  );
  // FP1-10: draft 任务音频在后台生成——audioGenerating=false 时也要轮询到就绪，
  // 否则详情页 AudioPreviewBar 永远显示「旁白音频未准备」。
  // storyboardRefetchInterval 在 audio_ready/audio_error 后自停，不会空转。
  const { data: storyboard } = useStoryboardWithAudioPolling(
    showTimeline ? params.taskId : undefined,
    audioGenerating || task?.status === "draft",
    false,
    regenPending.size > 0,
  );
  // 2026-10-20 Task 4：帧数走服务端摘要（盘面 storyboard），镜头数来自渲染 manifest。
  const storyboardCounts = resolveStoryboardCounts(task, storyboard);
  const reorderMut = useReorderFrames(params.taskId);
  const renderFromDraftMut = useRenderFromDraft(params.taskId);
  // P2-4/BUG-007：409 引导 action 的导航回调注入（hooks 层不直接引 router）
  const recomposeMut = useRecomposeTask(params.taskId, {
    onEditStoryboard: () =>
      router.push(`/workspace/video-gen/${params.taskId}/storyboard`),
  });
  const rerenderMut = useReRenderTask(params.taskId, {
    onEditStoryboard: () =>
      router.push(`/workspace/video-gen/${params.taskId}/storyboard`),
  });
  const { previewFrames } = useTaskEvents(
    task?.status === "running" || task?.status === "composing"
      ? params.taskId
      : undefined,
  );

  // F7: 分镜条联动——播放头时间 + 选中帧 + 播放器 ref
  const [currentTime, setCurrentTime] = useState(0);
  const [selectedFrame, setSelectedFrame] = useState<number | null>(null);
  // 2026-08-20: 脚本/话题长文本默认收起 3 行（>160 字符时出现切换按钮）
  const [scriptExpanded, setScriptExpanded] = useState(false);
  // P1/BUG-008（2026-09-14）：详情页三处 recompose 触发按钮（failed 重试 / 降级面板
  // 重新合成 / 分镜条待合成）统一先弹确认层——修前无确认直调（分镜页有、详情页没有）。
  const [confirmRecompose, setConfirmRecompose] = useState(false);
  // Task 3: recompose/re-render 409 冲突帧高亮（解析错误文案得 0-based index）
  const [conflictFrames, setConflictFrames] = useState<number[]>([]);

  // Task 3: recompose/re-render 409 -> 解析帧号 -> 高亮 + 滚动定位 + 打开面板
  const handleRecomposeError = useCallback((err: unknown) => {
    const msg = err instanceof Error ? err.message : String(err);
    const frames = parseUnavailableFrames(msg);
    if (frames.length) {
      setConflictFrames(frames);
      setSelectedFrame(frames[0]!);
    }
  }, []);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  // 2026-08-19（彩条点击定位）：点分组 → FrameStrip 选中组首帧；ready 态视频 seek 到
  // 组起点（组首帧之前各帧 duration 累加）。此前的点击滚动修复（D2）只覆盖了分镜编辑器。
  const handleGroupNav = useCallback(
    (gi: number) => {
      const g = storyboard?.scene_groups?.[gi];
      const first = g?.frame_indices?.[0];
      if (first === undefined) return;
      setSelectedFrame(first);
      if (task?.status === "ready" && videoRef.current) {
        const t = (storyboard?.frames ?? [])
          .filter((f) => f.index < first)
          .reduce((s, f) => s + (f.duration ?? 0), 0);
        videoRef.current.currentTime = t;
      }
    },
    [storyboard, task?.status],
  );
  // B1: 缩略图破缓存版本号——重生/上传成功后 bump（同名本地文件被覆盖时强制 <img> 重取）
  const [thumbVersion, setThumbVersion] = useState(() => Date.now());

  // F4: refresh video src when composing → ready transition (new mp4 on same URL)
  const [videoTimestamp, setVideoTimestamp] = useState(() => Date.now());
  const prevStatusRef = useRef<string | undefined>(undefined);
  useEffect(() => {
    const prev = prevStatusRef.current;
    const current = task?.status;
    if (prev === "composing" && current === "ready") {
      setVideoTimestamp(Date.now());
    }
    prevStatusRef.current = current;
  }, [task?.status]);

  // Stop polling once audio is ready.
  useEffect(() => {
    if (storyboard?.audio_ready && audioGenerating) {
      setAudioGenerating(false);
    }
  }, [storyboard?.audio_ready, audioGenerating]);

  // H4: 重生释放——storyboard 轮询到 regen_state 终态（done/error）即结案；
  // done 时破缩略图缓存 + 刷 task（dirty_frames 驱动「重新合成」按钮）；超时兜底。
  useEffect(() => {
    if (regenPending.size === 0) return;
    const frames = storyboard?.frames ?? [];
    for (const idx of Array.from(regenPending)) {
      const f = frames.find((fr) => fr.index === idx);
      if (isRegenResolved(f)) {
        regenStartedAtRef.current.delete(idx);
        releaseRegen(idx);
        if (f?.regen_state === "error") {
          toast.error(
            `帧 ${idx + 1} 重新生成失败：${f.regen_error ?? "未知错误"}`,
          );
          // P2-2: 重生失败 -> 高亮保留（口径与后端 D1 拦截一致：error 仍拦）
        } else {
          setThumbVersion(Date.now());
          // P2-2: 重生成功 -> 精确移除该帧的冲突高亮（不清全部，避免误伤其他待修帧）
          setConflictFrames((prev) => prev.filter((x) => x !== idx));
          void qc.invalidateQueries({
            queryKey: [VIDEO_KEY, params.taskId],
          });
        }
        continue;
      }
      const startedAt = regenStartedAtRef.current.get(idx) ?? Date.now();
      if (isRegenTimedOut(startedAt, Date.now())) {
        regenStartedAtRef.current.delete(idx);
        releaseRegen(idx);
        toast.error(`帧 ${idx + 1} 重新生成超时，请重试`);
      }
    }
  }, [storyboard, regenPending, releaseRegen, qc, params.taskId]);

  if (isLoading) {
    return (
      <div className="flex size-full items-center justify-center">
        <Loader2 className="text-muted-foreground h-6 w-6 animate-spin" />
      </div>
    );
  }

  if (isError || !task) {
    return (
      <div className="flex size-full flex-col items-center justify-center gap-4">
        <p className="text-muted-foreground text-sm">任务不存在或加载失败</p>
        <Button variant="outline" size="sm" onClick={() => router.back()}>
          <ArrowLeft className="mr-1 h-4 w-4" />
          返回
        </Button>
      </div>
    );
  }

  const payload =
    (task.payload as Record<
      string,
      string | number | boolean | null | undefined
    >) ?? {};
  // T-E9（F8）：remotion_course——成片播放轴相对内容轴的 intro 偏移（秒）；
  // ready 后帧条缩略图渲真实成片画面（still 管线），draft 期用版式示意图
  //（素材≠成片画面，且未引用素材不顶替——F8 第二点）。
  const remotionIntroSec = (() => {
    if (task.task_type !== "remotion_course") return 0;
    const props = (payload as Record<string, unknown>).remotion_props as
      | {
          brand?: { intro?: { durationInFrames?: number } };
          scenes?: unknown[];
        }
      | undefined;
    const introFrames = props?.brand?.intro?.durationInFrames;
    return typeof introFrames === "number" ? introFrames / 30 : 0;
  })();
  const stillMode =
    task.task_type === "remotion_course"
      ? task.status === "ready"
        ? ("render" as const)
        : ("m0" as const)
      : undefined;
  // 脚本/话题文本（2026-08-20 收起/展开）：空串穿透——topic 默认存空字符串（非 null），
  // ?? 会把 "" 穿透成空白，必须 ||（与下方标题行 truthy 判断同口径）。
  // eslint-disable-next-line @typescript-eslint/prefer-nullish-coalescing -- intentional: 空串须穿透到 script
  const scriptText = String(payload.topic || payload.script || "");
  const scriptCollapsible = scriptText.length > 160;
  // BGM 展示名：老任务可能指向已下架旧曲（后端静默降级为无 BGM），此处显式告知用户
  const bgmInfo = resolveBgmLabel(
    payload.bgm == null ? null : String(payload.bgm),
    [...(bgmList.data?.built_in ?? []), ...(bgmList.data?.custom ?? [])],
  );
  // T-E5（R19）：类型显示名单一事实源（修前 remotion_course 落到"未知"）
  const typeLabel = videoTaskTypeLabel(task.task_type);

  // F1: segments-type engines support per-frame edit + timeline; vimax/remotion don't
  const isSegmentedEngine =
    task.task_type !== "vimax" && task.task_type !== "remotion";

  return (
    <div className="scrollbar-none flex size-full flex-col overflow-y-auto p-6">
      <div className="mx-auto w-full max-w-4xl space-y-6">
        {/* Header */}
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="sm" onClick={() => router.back()}>
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <div className="flex items-center gap-2">
            <Film className="h-5 w-5 text-indigo-500" />
            <h1 className="text-lg font-bold">任务详情</h1>
          </div>
          <StatusBadge status={task.status} progress={task.progress} />
          {(() => {
            // E8: pending/running/composing 长时间无进展 → 降级提示（不停轮询）
            const stall = getStallWarning(task);
            return stall ? (
              <span className="ml-auto text-xs font-medium text-amber-600 dark:text-amber-400">
                {stall}
              </span>
            ) : null;
          })()}
          {task.status === "draft" && (task.progress ?? 0) >= 100 && (
            <Button
              onClick={() =>
                router.push(`/workspace/video-gen/${task.id}/storyboard`)
              }
              className="ml-auto bg-indigo-600 hover:bg-indigo-700"
            >
              <Pencil className="mr-1 h-4 w-4" />
              编辑分镜
            </Button>
          )}
          {/* failed 主行动:重试合成(复用盘面素材断点续跑)+ 编辑分镜入口。
              修前 failed 无任何可达续跑入口(dirty_frames 门控把分镜条按钮灰掉,
              警告盒需 result 警告字段,20c 需 course_project) → 只剩删除一条路。 */}
          {task.status === "failed" && isSegmentedEngine && (
            <div className="ml-auto flex items-center gap-2">
              <Button
                variant="outline"
                onClick={() =>
                  router.push(`/workspace/video-gen/${task.id}/storyboard`)
                }
              >
                <Pencil className="mr-1 h-4 w-4" />
                编辑分镜
              </Button>
              <Button
                disabled={recomposeMut.isPending}
                onClick={() => setConfirmRecompose(true)}
                className="bg-indigo-600 hover:bg-indigo-700"
              >
                <RefreshCw className="mr-1 h-4 w-4" />
                {recomposeMut.isPending
                  ? "重试合成中..."
                  : "重试合成（渲染中断可断点续跑）"}
              </Button>
            </div>
          )}
        </div>

        {/* Task 17: ViMax 生成后逐镜重做 */}
        {task.task_type === "vimax" &&
          (() => {
            const r = task.result ?? {};
            const shots = r.vimax_shots as
              | Record<string, { prompt?: string }>
              | undefined;
            // failed/recompose/QC 任务也可能保留可用 manifest——有非空 manifest
            // 就展示逐镜重做，只有无 manifest 才显示空态（任务状态不参与）。
            if (!hasVimaxShotManifest(shots)) {
              // 未渲染完成：明确空态，不把 0 当「已生成 0 镜」。
              return (
                <div className="space-y-2 rounded-lg border p-4">
                  <h2 className="text-sm font-semibold">
                    ViMax 分镜（可逐镜重做）
                  </h2>
                  <p className="text-muted-foreground text-xs">
                    渲染完成后可逐镜重做
                  </p>
                </div>
              );
            }
            return (
              <VimaxShotRedo
                taskId={task.id}
                shots={shots}
                shotCount={
                  storyboardCounts.shotCount ?? Object.keys(shots).length
                }
                referenceImage={characterFrontUrl}
              />
            );
          })()}

        {/* Task 18-20: 失败态可见——字幕失败/丢帧/时长偏差 */}
        <DetailWarningBanner
          task={task}
          recomposePending={recomposeMut.isPending}
          onRecompose={() => setConfirmRecompose(true)}
        />

        {/* Task 20c: 项目已更新 → 重新生成（force 刷新项目角色/风格，重跑视觉）。
            T13（2026-09-10 独立管线计划）：remotion_course 语境改为品牌包/版式——
            该管线无角色/风格注入，重跑画面=重跑槽位配图+模板渲染。 */}
        {task.course_project_id &&
          isSegmentedEngine &&
          (task.status === "ready" || task.status === "failed") && (
            <div className="flex items-center justify-between rounded-lg border border-blue-300/50 bg-blue-50 p-3 dark:bg-blue-950/30">
              <p className="text-xs text-blue-700 dark:text-blue-300">
                {task.task_type === "remotion_course" ? (
                  <>
                    绑定项目「{task.course_project_id}
                    」的品牌包（配色/LOGO/片头片尾）或版式已更新？重新渲染让新品牌生效
                    （复用旁白/配音，重跑槽位配图与模板渲染）。
                  </>
                ) : (
                  <>
                    绑定项目「{task.course_project_id}
                    」的角色/风格已更新？重新生成让新角色/风格生效（复用旁白/分镜，重跑画面）。
                  </>
                )}
              </p>
              <Button
                size="sm"
                variant="outline"
                className="h-7 border-blue-400 text-xs text-blue-700 hover:bg-blue-100 dark:text-blue-300"
                disabled={rerenderMut.isPending}
                onClick={() =>
                  rerenderMut.mutate(undefined, {
                    onError: handleRecomposeError,
                  })
                }
              >
                <RefreshCw className="mr-1 h-3 w-3" />
                {rerenderMut.isPending
                  ? "正在重新生成画面..."
                  : task.task_type === "remotion_course"
                    ? "项目已更新，重新渲染"
                    : "项目已更新，重新生成画面"}
              </Button>
            </div>
          )}

        {/* Basic info */}
        <div
          data-testid="basic-info-section"
          className="border-border/50 space-y-2 rounded-lg border p-4"
        >
          <InfoRow label="类型" value={typeLabel} />
          <InfoRow label="状态" value={task.status} />
          <InfoRow
            label="创建时间"
            value={task.created_at ? formatDateTime(task.created_at) : null}
          />
          <InfoRow
            label="更新时间"
            value={task.updated_at ? formatDateTime(task.updated_at) : null}
          />
          {/* P3-1 (2026-08-18, 注释 2026-08-19 更正)：与列表页 D5 同闸——进行中任务
              会产生并附带**部分花费**（LLM/媒体调用逐笔计价，attach_task_costs 不按
              终态过滤），提前展示易被当作最终花费；终态闸将展示推迟到任务结束。
              （原注释"cost 恒 null / 无条件渲染花费 —"不实：InfoRow 对 null 短路，
              修前 cost=null 本就不渲染行；"—" 是列表页 formatCost(null) 行为。）
              终态对齐后端 TERMINAL_STATUSES = {ready, failed, cancelled}：被取消
              的任务可能已产生真实花费，仍须可见。字面量收拢为
              isTerminalVideoTaskStatus（反馈1-项2）。 */}
          {isTerminalVideoTaskStatus(task.status) && (
            <InfoRow
              label="花费"
              value={
                task.cost != null ? formatCost(task.cost, task.currency) : null
              }
            />
          )}
          {payload.aspect && (
            <InfoRow label="画面比例" value={String(payload.aspect)} />
          )}
          {payload.style && (
            <InfoRow
              label="风格"
              // C7 顺带修：原始 key 翻译为 label（复用风格表承载项查表，未知值原样）
              value={carryLabelFor(String(payload.style))}
            />
          )}
          {/*
            C7（决策 5/14 留痕）：三行实际生效来源。旧任务 payload 无
            effective_*_source 键 → 显示「—」。remotion 口径：风格下拉整体
            排除，source=project 仅表示绑定了项目，按此口径展示。
          */}
          <InfoRow
            label="风格来源"
            value={effectiveSourceLabel(payload.effective_style_source)}
          />
          <InfoRow
            label="音色来源"
            value={effectiveSourceLabel(payload.effective_voice_source)}
          />
          <InfoRow
            label="字幕来源"
            value={effectiveSourceLabel(payload.effective_subtitle_source)}
          />
          {/*
            C7（决策 3，v7 确认）：全断任务常驻徽标——提示仍一次（表单内警告条），
            徽标是状态展示，让「未使用项目资产」的后果持续可见。
          */}
          {payload.effective_style_source === "task" &&
            !!payload.course_project_id && (
              <div className="rounded-lg border border-amber-300/50 bg-amber-50 p-3 dark:bg-amber-950/30">
                <p className="text-xs font-medium text-amber-700 dark:text-amber-300">
                  本任务未使用项目资产
                </p>
                <p className="mt-0.5 text-[11px] text-amber-600 dark:text-amber-400">
                  创建时显式选择了非项目风格——风格前缀、角色三视图与风格锚图均未注入。
                </p>
              </div>
            )}
          {(storyboard?.config?.subtitle_style ?? payload.subtitle_style) && (
            <InfoRow
              label="字幕样式"
              // 读 storyboard.config 优先于创建 payload——payload 是用户当初的选择，
              // 不反映 _restore_subtitle_style_if_none 的自动回填与详情页改动。
              value={subtitleStyleLabel(
                storyboard?.config?.subtitle_style ??
                  (payload.subtitle_style as string | undefined),
              )}
            />
          )}
          {payload.bgm && (
            <div className="flex items-center gap-2 text-sm">
              <span className="text-muted-foreground w-20 shrink-0">BGM</span>
              <span
                className={cn(
                  "font-medium",
                  bgmInfo.missing && "text-amber-600 dark:text-amber-500",
                )}
              >
                {bgmInfo.label}
              </span>
            </div>
          )}
        </div>

        {/* Script / Topic */}
        {(payload.script ?? payload.topic) && (
          <div className="border-border/50 space-y-2 rounded-lg border p-4">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold">
                {payload.topic ? "话题" : "脚本"}
              </h3>
              {scriptCollapsible && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 px-2 text-xs"
                  onClick={() => setScriptExpanded((v) => !v)}
                >
                  {scriptExpanded ? (
                    <>
                      收起
                      <ChevronUp className="ml-1 size-3.5" />
                    </>
                  ) : (
                    <>
                      展开全部
                      <ChevronDown className="ml-1 size-3.5" />
                    </>
                  )}
                </Button>
              )}
            </div>
            <p
              className={cn(
                "text-muted-foreground text-sm whitespace-pre-wrap",
                scriptCollapsible && !scriptExpanded && "line-clamp-3",
              )}
            >
              {scriptText}
            </p>
          </div>
        )}

        {/* Error */}
        {task.error_message && (
          <div className="rounded-lg border border-red-200 bg-red-50 p-4 dark:border-red-900/50 dark:bg-red-950/30">
            <p className="text-sm font-medium text-red-700 dark:text-red-400">
              错误信息
            </p>
            <p className="text-muted-foreground mt-1 text-sm">
              {task.error_message}
            </p>
            {/* P2-4/BUG-007：失败态静态出路指引（J20.1 错误信息位） */}
            {task.status === "failed" && isSegmentedEngine && (
              <p className="mt-2 text-xs text-red-600/80 dark:text-red-300/80">
                若重试提示缺素材/缺音频，请点「编辑分镜」走全量渲染
              </p>
            )}
          </div>
        )}

        {/* Progress (A2: composing 期也显示，消除盲区；合并原 :702 合成提示) */}
        {(task.status === "running" || task.status === "composing") && (
          <div className="space-y-2">
            <div className="text-muted-foreground flex justify-between text-xs">
              <span>
                {task.status === "composing" ? "视频合成中" : "处理中"}
              </span>
              <span>{task.progress}%</span>
            </div>
            <div className="bg-muted h-2 overflow-hidden rounded-full">
              <div
                className="h-full rounded-full bg-indigo-500 transition-all duration-300"
                style={{ width: `${task.progress}%` }}
              />
            </div>
            {/* ViMax live stage (3.3): shows what the render is doing beyond a stuck % */}
            <VimaxStageIndicator result={task.result} />
          </div>
        )}

        {/* Frame preview thumbnails (SSE progressive preview; A2: composing 期也显示) */}
        {(task.status === "running" || task.status === "composing") &&
          previewFrames.size > 0 && (
            <div className="space-y-2">
              <h3 className="text-sm font-semibold">画面预览</h3>
              <div className="scrollbar-none flex gap-2 overflow-x-auto pb-2">
                {Array.from(previewFrames.values())
                  .sort((a, b) => a.frame_index - b.frame_index)
                  .map((pf) => (
                    <div
                      key={pf.frame_index}
                      className="relative shrink-0 overflow-hidden rounded-md border"
                    >
                      {pf.visual_type === "video" ? (
                        <video
                          src={pf.visual_url}
                          className="h-20 w-32 object-cover"
                          muted
                          playsInline
                          autoPlay
                          loop
                        />
                      ) : (
                        <img
                          loading="lazy"
                          src={pf.visual_url}
                          alt={`Frame ${pf.frame_index + 1}`}
                          className="h-20 w-32 object-cover"
                        />
                      )}
                      <span className="absolute bottom-0 left-0 bg-black/60 px-1 text-[10px] text-white">
                        {pf.frame_index + 1}
                      </span>
                    </div>
                  ))}
              </div>
            </div>
          )}

        {/* Audio preview for draft tasks (AudioPreviewBar 统一显示 ready/error 状态) */}
        {task.status === "draft" && (
          <AudioPreviewBar
            taskId={task.id}
            audioReady={storyboard?.audio_ready ?? false}
            audioError={storyboard?.audio_error}
            degradationWarning={storyboard?.audio_degradation_warning}
            totalDuration={storyboard?.total_duration}
            frameCount={storyboardCounts.frameCount ?? 0}
            onGenerateStart={() => setAudioGenerating(true)}
            onGenerateError={() => setAudioGenerating(false)}
          />
        )}

        {/* Scene Group progress */}
        {storyboard?.scene_groups && storyboard.scene_groups.length > 0 ? (
          <SceneGroupProgress
            groups={storyboard.scene_groups}
            frames={storyboard.frames}
            onGroupClick={handleGroupNav}
          />
        ) : // P2（2026-08-19）：彩条空态骨架——groups 产自锚定调用、落盘晚于帧出现，
        // 占位消除跳变（remotion 无分组概念，不显示）。
        (storyboard?.frames?.length ?? 0) > 0 &&
          ["ai_image", "ai_video"].includes(
            storyboard?.config?.video_type ?? "ai_image",
          ) ? (
          <div className="bg-muted/30 text-muted-foreground flex h-8 w-full items-center justify-center rounded-md border text-xs">
            内容分组生成中…
          </div>
        ) : null}

        {/* Video player (A2: composing 期不渲染播放器，避免 /download 409；ready 才显示。
            F7: 上移到分镜条上方——播放器是主角，分镜条是它的操作台） */}
        {task.status === "ready" && (
          <>
            {payload.quality === "preview" && (
              <div className="flex items-center justify-between rounded-lg border border-amber-300 bg-amber-50 px-4 py-2 text-sm dark:border-amber-700 dark:bg-amber-900/20">
                <span className="text-amber-700 dark:text-amber-400">
                  当前为预览画质（360p）
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={renderFromDraftMut.isPending}
                  onClick={() => {
                    renderFromDraftMut.mutate({ quality: "standard" });
                  }}
                >
                  {renderFromDraftMut.isPending ? "渲染中…" : "正式渲染"}
                </Button>
              </div>
            )}
            {task.artifact_missing === true ? (
              /* 2026-09-13 死链修复：假就绪任务（ready 但产物已失）不再渲染
                 <video>——修前必打 /download 404。提示块给出去路。 */
              <div
                data-testid="artifact-missing-notice"
                className="flex flex-col items-center justify-center gap-1 rounded-lg border border-dashed border-amber-300 bg-amber-50/50 px-4 py-10 text-center dark:border-amber-700 dark:bg-amber-900/20"
              >
                <span className="text-sm font-medium text-amber-700 dark:text-amber-400">
                  产物文件已被清理或丢失
                </span>
                <span className="text-muted-foreground text-xs">
                  可删除该任务后重新创建
                </span>
              </div>
            ) : (
              <div className="border-border/50 overflow-hidden rounded-lg border bg-black">
                <video
                  ref={videoRef}
                  controls
                  className="max-h-[50vh] w-full"
                  src={`${getVideoDownloadUrl(task.id)}?t=${videoTimestamp}`}
                  onTimeUpdate={(e) =>
                    setCurrentTime(e.currentTarget.currentTime)
                  }
                />
              </div>
            )}
          </>
        )}

        {/* 分镜导航条 + 选中帧操作面板（F7：替代旧假三轨时间线与帧操作网格） */}
        {isSegmentedEngine &&
          storyboard?.frames &&
          storyboard.frames.length > 0 && (
            <div data-testid="storyboard-strip-section" className="space-y-3">
              <div className="flex items-center justify-end gap-2">
                {(task.dirty_frames ?? []).length > 0 && (
                  <span className="text-xs text-amber-600 dark:text-amber-400">
                    {(task.dirty_frames ?? []).length} 处修改待合成
                  </span>
                )}
                {(() => {
                  const action = recomposeActionFor(task, {
                    // 字幕手改置锁也是「有待合成变更」（T6：曾只认 dirty_frames，
                    // 改字幕后按钮恒灰，新字幕烧不进成片）
                    subtitleLocked: storyboard?.subtitle_locked === true,
                  });
                  if (!action.visible) return null;
                  return (
                    <Button
                      size="sm"
                      disabled={action.disabled || recomposeMut.isPending}
                      onClick={() => setConfirmRecompose(true)}
                    >
                      {recomposeMut.isPending ? (
                        <>
                          <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                          {task.status === "failed"
                            ? "重试合成中…"
                            : "重新合成中…"}
                        </>
                      ) : (
                        <>
                          <RefreshCw className="mr-1 h-4 w-4" />
                          {action.label}
                        </>
                      )}
                    </Button>
                  );
                })()}
              </div>
              <FrameStrip
                frames={storyboard.frames}
                taskId={task.id}
                currentTime={task.status === "ready" ? currentTime : undefined}
                selectedIndex={selectedFrame}
                dirtyFrames={task.dirty_frames ?? []}
                highlightIndices={conflictFrames}
                thumbVersion={thumbVersion}
                taskType={task.task_type}
                stillMode={stillMode}
                seekOffsetSec={remotionIntroSec}
                onSelect={(i) =>
                  setSelectedFrame(i === selectedFrame ? null : i)
                }
                onSeek={(s) => {
                  if (videoRef.current) videoRef.current.currentTime = s;
                }}
                onReorder={
                  task.status === "draft"
                    ? (from, to) => {
                        // 重排后同一 index 指向另一条帧——面板若开着，key=index 未变会
                        // 复用旧实例、残留上一条帧的草稿（H1 同型 bleed），先关面板。
                        setSelectedFrame(null);
                        reorderMut.mutate({ fromIndex: from, toIndex: to });
                      }
                    : undefined
                }
              />
              {selectedFrame !== null &&
                (() => {
                  const f = storyboard.frames.find(
                    (fr) => fr.index === selectedFrame,
                  );
                  // M5: 任务详情响应并入的留痕按帧匹配（分镜级「参考资产」标签数据源）
                  const refRecord = (
                    (task.result?.ref_injection ?? []) as RefInjectionRecord[]
                  ).find((r) => r?.frame_index === f?.index);
                  return f ? (
                    <FrameOpsPanel
                      key={f.index}
                      taskId={task.id}
                      frame={f}
                      taskStatus={task.status}
                      videoType={task.task_type}
                      thumbVersion={thumbVersion}
                      refRecord={refRecord}
                      regenPending={regenPending.has(f.index)}
                      onRegenStart={trackRegen}
                      onRegenSyncError={releaseRegen}
                      onVisualChanged={() => {
                        setThumbVersion(Date.now());
                        // P3-2: 上传修复也是有效修复，精确移除该帧的冲突高亮
                        setConflictFrames((prev) =>
                          prev.filter((x) => x !== f.index),
                        );
                      }}
                      onClose={() => setSelectedFrame(null)}
                    />
                  ) : null;
                })()}
            </div>
          )}

        {/* 音频设置 + 字幕编辑（2026-07-28 产品评审：放分镜块外下方——
            看帧→改帧→片级调→合成 的动线；不放块内——isSegmentedEngine
            排除 vimax/remotion，移入会隐藏 vimax 字幕编辑器） */}
        {storyboard && (
          <AudioSettingsSection
            taskId={task.id}
            storyboard={storyboard}
            taskStatus={task.status}
          />
        )}
        <SubtitleEditSection taskId={task.id} taskStatus={task.status} />

        {/* P1/BUG-008：recompose 确认弹层（详情页三处触发共用；文案对齐分镜页）。 */}
        <RecomposeConfirmDialog
          open={confirmRecompose}
          onOpenChange={setConfirmRecompose}
          isFailed={task.status === "failed"}
          onConfirm={() => {
            recomposeGraceUntilRef.current = Date.now() + 60_000;
            recomposeMut.mutate(undefined, {
              onError: handleRecomposeError,
            });
          }}
        />
      </div>
    </div>
  );
}

// 2026-10-06 拆分批：音频设置/字幕编辑移独立文件，本模块 re-export 保既有导入方
export { AudioSettingsSection } from "./video-gen-detail-audio-settings";
export {
  isValidSubtitleTs,
  subtitleTsToSeconds,
  subtitleCueIssues,
  subtitleCuesHaveIssues,
  subtitleCuesEqual,
} from "./video-gen-detail-subtitle-edit";
