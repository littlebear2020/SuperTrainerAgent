"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  ImageIcon,
  Loader2,
  Pause,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Rocket,
  Sparkles,
  Trash2,
  Upload,
} from "lucide-react";
import { useRouter } from "next/navigation";
import {
  memo,
  type ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
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
import { Textarea } from "@/components/ui/textarea";
import {
  buildConfirmOptions,
  useConfirm,
} from "@/components/workspace/dialog-provider";
import { RecomposeConfirmDialog } from "@/components/workspace/video-gen/video-gen-recompose-dialog";
import {
  formatCostEstimate,
  shouldRequestCostEstimate,
} from "@/core/video-gen/cost-estimate";
import {
  useCourseProject,
  usePreviewFrame,
  usePreviewAll,
  useAddFrame,
  useDeleteFrame,
  useRegenerateFramePrompt,
  useRenderFromDraft,
  useReRenderTask,
  useRecomposeTask,
  useStoryboardWithAudioPolling,
  useBulkDisableGraphic,
  useUpdateFrameVisualSource,
  useUpdateFrameLayout,
  useUpdateStoryboardFrame,
  useUploadFrameVisual,
  useVideoTask,
  useCostEstimate,
  STORYBOARD_KEY,
} from "@/core/video-gen/hooks";
import {
  clearFramePreviewError,
  clearFramePreviewState,
  failedPreviewCount,
  frameVisualFingerprint,
  isLegacyBackendPreviewUpdate,
  isPreviewResolved,
  isPreviewTimedOut,
  pendingPreviewFrames,
  unresolvedPreviewCount,
} from "@/core/video-gen/previewAll";
import { deriveDisplayGroups } from "@/core/video-gen/scene-groups";
import type {
  ShotDirectionData,
  StoryboardData,
  StoryboardFrameData,
} from "@/core/video-gen/types";
import { usePreviewTracker } from "@/core/video-gen/usePreviewTracker";
import { visualUrlVersion } from "@/core/video-gen/visual-version";
import { cn } from "@/lib/utils";

import { AudioPreviewBar } from "./audio-preview-bar";
import { FrameAudioButton } from "./frame-audio-button";
import { LayoutSwitcher } from "./layout-switcher";
import { SceneGroupCard } from "./scene-group-card";
import { SceneGroupProgress } from "./scene-group-progress";
import { StillPreview } from "./still-preview";
import { VisualSourceSwitcher } from "./visual-source-switcher";

/** 新增/插入帧的占位旁白——hasPlaceholderNarration 的占位检测匹配同一字符串，抽成单一来源。 */
const NEW_FRAME_NARRATION = "（新分镜，请编辑旁白）";

// ── Frame audio button (per-frame narration preview) ────────────────────

// ── Frame card ──────────────────────────────────────────────────────────

/**
 * E9: 判定一次帧字段编辑是否应真正发出 PUT。
 *
 * 防两个真实缺陷：(1) debounce(1500ms) 已 fire 后 blur 再触发一次 mutate → 冗余重复同值提交；
 * (2) 无 in-flight 守卫。统一判定：值与乐观缓存现值相同（无变化）不发；与上次提交同字段同值
 * （debounce-fire-then-blur 重复 / in-flight）不发。纯函数便于测试。
 */
export function shouldCommitEdit(
  field: string,
  value: string,
  original: string,
  lastSubmitted: { field: string; value: string } | null,
): boolean {
  if (value === original) return false;
  if (lastSubmitted?.field === field && lastSubmitted.value === value) {
    return false;
  }
  return true;
}

// ── content-match T8④: graphic spec 只读摘要（纯函数，便于单测） ─────────

export function graphicSpecTypeLabel(
  type?: string,
): "大数字" | "步骤" | "要点" | "图文" {
  if (type === "big_number") return "大数字";
  if (type === "steps") return "步骤";
  if (type === "text_card") return "要点";
  return "图文";
}

export function graphicSpecSummary(spec: {
  title?: string;
  number?: string;
  unit?: string;
  points?: string[];
  steps?: string[];
}): string {
  if (spec.number) return `${spec.number}${spec.unit ?? ""}`;
  if (spec.steps?.length) return `${spec.steps.length} 步`;
  if (spec.points?.length) return `${spec.points.length} 点`;
  return spec.title ?? "";
}

export function graphicSpecTooltip(spec: {
  title?: string;
  description?: string;
  points?: string[];
  steps?: string[];
}): string {
  const lines: string[] = [];
  if (spec.title) lines.push(spec.title);
  if (spec.description) lines.push(spec.description);
  if (spec.points?.length) lines.push(...spec.points.map((p) => `• ${p}`));
  if (spec.steps?.length)
    lines.push(...spec.steps.map((s, i) => `${i + 1}. ${s}`));
  return lines.join("\n") || "graphic spec（无内容）";
}

function commitFieldKey(
  field: string,
): "narration" | "search_term" | "image_prompt" {
  if (field === "narration") return "narration";
  if (field === "search_term") return "search_term";
  return "image_prompt";
}

// ── S6（2026-08-26 调度表计划）：调度行只读显示 ─────────────────────────────
// 枚举中文术语与后端 planner._SHOT_SIZE_TERMS/_CAMERA_MOVE_TERMS 同款口径
// （compose_video_prompt 拼进提示词的同一套词——UI 与产物描述一致）。
const SHOT_SIZE_TERMS: Record<string, string> = {
  wide: "远景",
  medium: "中景",
  close: "特写",
};
const CAMERA_MOVE_TERMS: Record<string, string> = {
  static: "固定机位",
  push_in: "缓慢推近",
  pull_out: "缓慢拉远",
  pan: "横向摇移",
  tilt: "纵向摇移",
};

/** 调度行小字「{景别} · {机位} · {人物位置}」；未知枚举回退原值（后端扩枚举不炸 UI）。 */
function shotDirectionLine(row: ShotDirectionData): string {
  return [
    SHOT_SIZE_TERMS[row.shot_size] ?? row.shot_size,
    CAMERA_MOVE_TERMS[row.camera_move] ?? row.camera_move,
    row.subject_position,
  ]
    .filter(Boolean)
    .join(" · ");
}

/** 单帧重生按钮 title 的调度表说明（景别/机位跨重生不变，防误报 bug——S6 v2.2）。 */
const SHOT_TABLE_REGEN_NOTE =
  "景别与机位由调度表决定，不随重生变化（如需调整请整表重算）";

function FrameCardInner({
  frame,
  videoType,
  taskVideoType,
  taskId,
  referenceImage,
  previewMut,
  regenPromptMut,
  pending,
  onDelete,
  onInsertAfter,
  insertPending,
  highlight,
  shotDirection,
  audioReady,
  onGenerateStart,
}: {
  frame: StoryboardFrameData;
  /** 帧级生效类型（visual_source ?? 任务类型）——驱动素材切换器取值/预览按钮等帧级行为。 */
  videoType: string;
  /**
   * T-A4（R4）：任务级 video_type——版式切换/静帧预览等「任务类型功能门控」必须
   * 用任务级值。修前门控读帧级 visual_source 覆盖值（remotion_course 帧被
   * visual_routing 归一为 ai_image/graphic）→ `videoType === "remotion_course"`
   * 永假，LayoutSwitcher/静帧预览在真实数据下整体不可见。缺省回落 videoType
   * （存量调用方兼容）。
   */
  taskVideoType?: string;
  taskId: string;
  referenceImage?: string;
  previewMut: ReturnType<typeof usePreviewFrame>;
  regenPromptMut: ReturnType<typeof useRegenerateFramePrompt>;
  pending: Set<number>;
  onDelete?: (index: number) => void;
  /** 「在 N 镜后插入」：点击调 addFrame(index=N+1)；draft 态才由父级传入。 */
  onInsertAfter?: (index: number) => void;
  /** addFrame in-flight：禁用插入按钮防连点 index 漂移。 */
  insertPending?: boolean;
  /** 插入/追加落地后 ring 高亮 2s（定位反馈）。 */
  highlight?: boolean;
  /** S6：本帧调度行（shot_table 帧绑定行；undefined = 无表/无行帧，不显示调度内容）。 */
  shotDirection?: ShotDirectionData;
  /** B3 复审 F1：ready 态帧卡试听 toast 不出重触发 action（后端 already_ready 短路）。 */
  audioReady?: boolean;
  /** B3 复审 F2：帧卡试听 toast 的重触发须启动任务级音频轮询（setAudioGenerating）。 */
  onGenerateStart?: () => void;
}) {
  const [editingField, setEditingField] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  // M4: 本地帧缩略图破缓存——同名文件（frame_NNN.jpg/img_NNNN.png）被覆盖后 src
  // 字符串不变，<img loading="lazy"> 不重取。版本号由 frame.visual_url 派生（每次重生成必变，
  // 2026-07-27 预览回归修复）；上传路径不产生新 URL，用 uploadTick bump。
  const [uploadTick, setUploadTick] = useState(0);
  const visualVersion = visualUrlVersion(frame.visual_url, uploadTick);
  const [imgFailed, setImgFailed] = useState(false);
  // 本地文件缺失/URL 过期裂图后，visualVersion 变化（重生成功新图落盘）→
  // 复位重试本地源（产品评审 P2-6：不复位会永久落到 visual_url 分支绕路）。
  useEffect(() => {
    setImgFailed(false);
  }, [visualVersion]);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // E9: 记录最近一次已提交的 {field, value}，用于 dedup（debounce-fire-then-blur 重复 / in-flight）
  const lastSubmittedRef = useRef<{ field: string; value: string } | null>(
    null,
  );
  const uploadRef = useRef<HTMLInputElement | null>(null);
  const updateMut = useUpdateStoryboardFrame(taskId);
  // F5（2026-09-28 edits 修复批）：提交失败被 hooks onError 回滚盘面缓存后，复位
  // lastSubmittedRef——否则 shouldCommitEdit 的同值拦截被 ref 残留卡死，用户改回
  // 同值再提交永远静默失败（分镜页编辑静默丢失）。
  useEffect(() => {
    if (updateMut.isError) lastSubmittedRef.current = null;
  }, [updateMut.isError]);
  const uploadMut = useUploadFrameVisual(taskId);
  const sourceMut = useUpdateFrameVisualSource(taskId);
  // T7：remotion_course 版式写回 + 静帧预览
  const layoutMut = useUpdateFrameLayout(taskId);

  // remotion_course（T4）：帧画面词需要展示/编辑（ai_image/ai_video 素材生成消费）
  const showImagePrompt = videoType !== "remotion";

  // S6：单帧重生（视觉/画面描述）按钮 title 补调度表说明——景别/机位跨重生不变，
  // 防「重生不改景别」被误报 bug（仅覆盖帧追加说明）。
  const regenBaseTitle =
    !frame.visual_url && !!frame.local_visual_path
      ? "上传素材无法重新生成"
      : !(frame.image_prompt ?? "").trim()
        ? "请先生成或填写画面描述"
        : "用当前提示词重新生成画面";
  const regenTitle = shotDirection
    ? `${regenBaseTitle}。${SHOT_TABLE_REGEN_NOTE}`
    : regenBaseTitle;

  const startEdit = (field: string, value: string) => {
    setEditingField(field);
    setEditValue(value ?? "");
  };

  const doSubmit = useCallback(
    (field: string, value: string) => {
      const original =
        frame[field as "narration" | "image_prompt" | "search_term"] ?? "";
      // E9: 统一判定——无变化不发；与上次提交同值不发（debounce-fire-then-blur 去重 / in-flight）
      if (!shouldCommitEdit(field, value, original, lastSubmittedRef.current)) {
        return;
      }
      lastSubmittedRef.current = { field, value };
      updateMut.mutate({
        frameIndex: frame.index,
        updates: { [commitFieldKey(field)]: value },
      });
    },
    [frame, updateMut],
  );

  const commitEdit = useCallback(
    (field: string) => {
      if (debounceRef.current) {
        clearTimeout(debounceRef.current);
        debounceRef.current = null;
      }
      doSubmit(field, editValue);
      setEditingField(null);
    },
    [doSubmit, editValue],
  );

  const handleEditChange = (field: string, value: string) => {
    setEditValue(value);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      debounceRef.current = null;
      doSubmit(field, value); // E9: 与 blur 同源，经 shouldCommitEdit 去重
    }, 1500);
  };

  return (
    <div
      id={`frame-card-${frame.index}`}
      className={cn(
        "bg-card space-y-3 rounded-lg border p-4",
        highlight && "ring-primary ring-2 ring-offset-2",
      )}
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-muted-foreground text-xs font-medium">
            分镜 {frame.index + 1}
          </span>
          {/* Task 20b: 绑项目时帧预览旁展示角色参考图缩略图，肉眼校验一致性 */}
          {referenceImage && (
            <span
              className="text-muted-foreground inline-flex items-center gap-1 text-[10px]"
              title="本帧使用的角色参考。角色不像？点右侧「重新生成」"
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
        <Button
          variant="ghost"
          size="sm"
          className="h-7 text-xs"
          onClick={() => previewMut.mutate(frame.index)}
          disabled={
            pending.has(frame.index) ||
            (!frame.visual_url && !!frame.local_visual_path) ||
            !(frame.image_prompt ?? "").trim()
          }
          title={regenTitle}
        >
          {pending.has(frame.index) ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <RefreshCw className="h-3 w-3" />
          )}
          重新生成
        </Button>
        {onInsertAfter && (
          <Button
            variant="ghost"
            size="sm"
            className="h-7 text-xs"
            onClick={() => onInsertAfter(frame.index)}
            disabled={insertPending}
            title="在此镜后插入分镜"
          >
            <Plus className="h-3 w-3" />
          </Button>
        )}
        {onDelete && (
          <Button
            variant="ghost"
            size="sm"
            className="h-7 text-xs text-red-500 hover:text-red-600"
            onClick={() => onDelete(frame.index)}
            title="删除该分镜"
          >
            <Trash2 className="h-3 w-3" />
          </Button>
        )}
      </div>

      {/* S6（2026-08-26 调度表计划）：调度行只读小字——有行才渲染（存量任务/插帧待生成行
          零噪音降级）；title 悬浮看全行（构图/承接）。只读展示，编辑走整表重算。 */}
      {shotDirection && (
        <div
          className="text-muted-foreground text-[11px] leading-4"
          title={`镜头调度：景别 ${shotDirection.shot_size} · 机位 ${shotDirection.camera_move}${
            shotDirection.composition
              ? ` · 构图 ${shotDirection.composition}`
              : ""
          }${shotDirection.continuity ? ` · 承接 ${shotDirection.continuity}` : ""}`}
        >
          {shotDirectionLine(shotDirection)}
        </div>
      )}

      {/* Visual source switcher（ai_image/ai_video/graphic/remotion 才有逐帧切换；用 videoType 作默认值）。
          content-match：graphic 帧也允许切换（切离后旧 gfx 卡须重生，见 frames_to_regenerate） */}
      {/* remotion_course（T4）：逐帧切换同样开放（visual_routing 支持 ai_image/ai_video/graphic 三值；
          旧 "remotion" = chat-handoff 存量值保留，语义不同不得合并） */}
      {/* T7：remotion_course 版式切换 + 静帧预览（写回 frame.layout；D14 用户手选端）。
          T-A4（R4）：门控用任务级类型（taskVideoType）——帧级 visual_source 已被
          visual_routing 归一（ai_image/graphic），用它判 remotion_course 永假。 */}
      {(taskVideoType ?? videoType) === "remotion_course" && (
        <div className="flex flex-col gap-2">
          <LayoutSwitcher
            value={frame.layout}
            onChange={(v) =>
              layoutMut.mutate({ frameIndex: frame.index, layout: v })
            }
            disabled={layoutMut.isPending}
          />
          <StillPreview
            taskId={taskId}
            frameIndex={frame.index}
            layout={frame.layout}
          />
        </div>
      )}
      {[
        "ai_image",
        "ai_video",
        "graphic",
        "remotion",
        "remotion_course",
      ].includes(taskVideoType ?? videoType) && (
        <div className="flex items-center gap-2">
          <VisualSourceSwitcher
            value={frame.visual_source ?? videoType}
            onChange={(v) =>
              sourceMut.mutate({ frameIndex: frame.index, visualSource: v })
            }
            disabled={
              sourceMut.isPending &&
              sourceMut.variables?.frameIndex === frame.index
            }
          />
          {/* content-match T8④: graphic 帧 spec 只读摘要（D6：v1 不开放编辑） */}
          {frame.visual_source === "graphic" && frame.graphic_spec && (
            <span
              className="inline-flex max-w-full items-center gap-1 truncate rounded bg-sky-500/10 px-1.5 py-0.5 text-[10px] text-sky-600 dark:text-sky-400"
              title={graphicSpecTooltip(frame.graphic_spec)}
            >
              {graphicSpecTypeLabel(frame.graphic_spec.type)}
              <span className="text-muted-foreground truncate font-normal">
                {graphicSpecSummary(frame.graphic_spec)}
              </span>
            </span>
          )}
          {frame.visual_source === "graphic" && !frame.graphic_spec && (
            <span
              className="text-muted-foreground text-[10px]"
              title="无 spec 时按旁白自动生成要点卡（保底）"
            >
              无 spec（旁白保底）
            </span>
          )}
        </div>
      )}

      {/* 视觉预览 */}
      <div className="bg-muted/50 relative aspect-video overflow-hidden rounded-md">
        {/* 有图帧的重生失败角标（preview_error 在无图分支已另有展示位）——
            有图帧重生失败曾仍显旧图且零提示（2026-07-28 产品评审） */}
        {frame.preview_error &&
          (frame.local_visual_path ?? frame.visual_url) && (
            <span
              className="absolute top-1 right-1 h-2.5 w-2.5 rounded-full bg-red-500"
              title={`上次预览失败：${frame.preview_error}`}
            />
          )}
        {frame.local_visual_path && !imgFailed ? (
          frame.visual_type === "video" ? (
            <video
              src={`/api/video/tasks/${taskId}/frames/${frame.index}/visual?v=${visualVersion}`}
              controls
              className="h-full w-full object-cover"
            />
          ) : (
            <img
              loading="lazy"
              src={`/api/video/tasks/${taskId}/frames/${frame.index}/visual?v=${visualVersion}`}
              className="h-full w-full object-cover"
              alt={`分镜 ${frame.index + 1}`}
              onError={() => setImgFailed(true)}
            />
          )
        ) : frame.visual_url ? (
          frame.visual_type === "video" ? (
            <video
              src={frame.visual_url}
              controls
              className="h-full w-full object-cover"
            />
          ) : (
            <img
              loading="lazy"
              src={frame.visual_url}
              className="h-full w-full object-cover"
              alt={`分镜 ${frame.index + 1}`}
            />
          )
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-1">
            {["ai_image", "ai_video"].includes(videoType) ? (
              <>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="text-xs"
                  onClick={() => previewMut.mutate(frame.index)}
                  disabled={
                    pending.has(frame.index) ||
                    !(frame.image_prompt ?? "").trim()
                  }
                  title={
                    !(frame.image_prompt ?? "").trim()
                      ? "请先生成或填写画面描述"
                      : undefined
                  }
                >
                  {pending.has(frame.index) ? (
                    <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                  ) : (
                    <Sparkles className="mr-1 h-3 w-3" />
                  )}
                  生成预览图
                </Button>
                {frame.preview_error && !pending.has(frame.index) && (
                  <span className="px-2 text-center text-[10px] text-red-500">
                    {frame.preview_error}
                  </span>
                )}
              </>
            ) : (
              <div className="text-muted-foreground/60 flex h-full flex-col items-center justify-center gap-1 text-xs">
                <ImageIcon className="h-6 w-6" />
                <span>暂无画面</span>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Narration */}
      <div className="space-y-1">
        <div className="text-muted-foreground flex items-center gap-1 text-xs font-medium">
          <span>旁白</span>
          {editingField !== "narration" && (
            <button
              type="button"
              onClick={() => startEdit("narration", frame.narration)}
              className="hover:text-foreground"
            >
              <Pencil className="h-3 w-3" />
            </button>
          )}
        </div>
        {editingField === "narration" ? (
          <Textarea
            value={editValue}
            onChange={(e) => handleEditChange("narration", e.target.value)}
            onBlur={() => commitEdit("narration")}
            className="min-h-[60px] text-sm"
            autoFocus
          />
        ) : (
          <p className="text-sm leading-relaxed">{frame.narration}</p>
        )}
      </div>

      {/* Image prompt (ai_image/ai_video) */}
      {showImagePrompt && (
        <div className="space-y-1">
          <div className="text-muted-foreground flex items-center gap-1 text-xs font-medium">
            <span>画面提示词</span>
            {editingField !== "image_prompt" && frame.image_prompt && (
              <button
                type="button"
                onClick={() =>
                  startEdit("image_prompt", frame.image_prompt ?? "")
                }
                className="hover:text-foreground"
              >
                <Pencil className="h-3 w-3" />
              </button>
            )}
            {/* D3：新增帧默认空 image_prompt——铅笔块对空 prompt 不渲染，
                此按钮是空 prompt 帧获得画面描述的主通道。 */}
            {editingField !== "image_prompt" && (
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="h-5 px-1.5 text-[10px]"
                title={
                  shotDirection
                    ? `用 LLM 重新生成画面描述。${SHOT_TABLE_REGEN_NOTE}`
                    : "用 LLM 重新生成画面描述"
                }
                disabled={
                  // per-frame pending（同 sourceMut/uploadMut 先例）：共享 mutation 的
                  // isPending 是全局的，须与 variables 判等，否则全卡一起转圈。
                  (regenPromptMut.isPending &&
                    regenPromptMut.variables === frame.index) ||
                  !(frame.narration ?? "").trim()
                }
                onClick={() => regenPromptMut.mutate(frame.index)}
              >
                {regenPromptMut.isPending &&
                regenPromptMut.variables === frame.index
                  ? "生成中…"
                  : "生成画面描述"}
              </Button>
            )}
          </div>
          {editingField === "image_prompt" ? (
            <Textarea
              value={editValue}
              onChange={(e) => handleEditChange("image_prompt", e.target.value)}
              onBlur={() => commitEdit("image_prompt")}
              className="min-h-[60px] text-sm"
              autoFocus
            />
          ) : frame.image_prompt ? (
            <p className="text-muted-foreground text-xs leading-relaxed">
              {frame.image_prompt}
            </p>
          ) : (
            <p className="text-muted-foreground/60 text-xs leading-relaxed">
              （空——编辑旁白后点「生成画面描述」）
            </p>
          )}
        </div>
      )}

      {/* 每帧试听（audio_path 存在时） */}
      {frame.audio_path && (
        <FrameAudioButton
          taskId={taskId}
          frameIndex={frame.index}
          audioReady={audioReady}
          onGenerateStart={onGenerateStart}
        />
      )}

      {/* Visual status + upload */}
      <div className="flex items-center justify-between border-t pt-2">
        <div className="flex items-center gap-1.5 text-xs">
          <span
            className={cn(
              "inline-block h-2 w-2 rounded-full",
              frame.local_visual_path
                ? "bg-green-500"
                : frame.visual_url
                  ? "bg-blue-500"
                  : "bg-muted-foreground/40",
            )}
          />
          <span className="text-muted-foreground">
            {frame.local_visual_path
              ? "本地素材"
              : frame.visual_url
                ? "在线素材"
                : "待生成"}
          </span>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-6 text-xs"
          onClick={() => uploadRef.current?.click()}
          disabled={
            uploadMut.isPending &&
            uploadMut.variables?.frameIndex === frame.index
          }
        >
          {uploadMut.isPending &&
          uploadMut.variables?.frameIndex === frame.index ? (
            <Loader2 className="mr-1 h-3 w-3 animate-spin" />
          ) : (
            <Upload className="mr-1 h-3 w-3" />
          )}
          替换素材
        </Button>
        <input
          ref={uploadRef}
          type="file"
          accept="image/jpeg,image/png,image/webp,video/mp4,video/webm"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (!file) return;
            uploadMut.mutate(
              { frameIndex: frame.index, file },
              { onSuccess: () => setUploadTick((t) => t + 1) },
            );
            e.target.value = "";
          }}
        />
      </div>
    </div>
  );
}

// 2026-10-06 性能批：memo 化帧卡——流式/乐观更新重渲时未变帧跳过
const FrameCard = memo(FrameCardInner);

// ── Main storyboard editor ────────────────────────────────────────────

export function VideoGenStoryboard({ taskId }: { taskId: string }) {
  const router = useRouter();
  const confirm = useConfirm();
  const [audioGenerating, setAudioGenerating] = useState(false);

  // task↔storyboard 互相依赖：task 的 enabled 要 storyboardLoaded，
  // storyboard 的 interval 要 taskFailed。用 ref 跨渲染传 taskFailed（B1+B2）。
  const taskFailedRef = useRef(false);
  const taskFailed = taskFailedRef.current; // 上一渲染的值，供 storyboard interval 与渲染判定

  // E2: 共享 preview mutation（一个实例供所有 FrameCard 复用）。
  // M1-3: 配 per-frame pending tracker 驱动各帧 spinner——RQv5 共享实例只跟踪最新
  // mutate()，连点 #1/#2 会让 #1 spinner 假消失；tracker 的 Set 各帧独立转圈。
  // 预览异步化：release 改由 storyboard 数据驱动（visual_url/preview_error 出现或超时）。
  const {
    pending: previewPending,
    track: trackPreviewRaw,
    release: releasePreview,
  } = usePreviewTracker();
  const qc = useQueryClient();
  const previewStartedAtRef = useRef(new Map<number, number>());
  // 条目 34（2026-09-12 缺陷批）：旧后端（无 preview_state 状态机）探测——
  // track 时记画面指纹基线，轮询中指纹变化且 preview_state 持续缺席 →
  // 旧后端已写回新图，按画面已更新结案（提示页面级一次）。
  const previewBaselineRef = useRef(new Map<number, string>());
  const legacyBackendHintShownRef = useRef(false);
  const trackPreview = useCallback(
    (i: number) => {
      previewStartedAtRef.current.set(i, Date.now());
      {
        const data = qc.getQueryData<StoryboardData>([STORYBOARD_KEY, taskId]);
        previewBaselineRef.current.set(
          i,
          frameVisualFingerprint(data?.frames?.find((fr) => fr.index === i)),
        );
      }
      // 重试/重生时乐观双清缓存中该帧旧 preview_error + preview_state（2026-09-07）——
      // 否则 release effect 用缓存旧数据判 isPreviewResolved 立即 true → spinner 秒停
      // 并弹出上一次陈旧结果 toast（重生旧图帧时旧 visual_url 生成期间不清，结案只认
      // preview_state 终态）。双清后由后端 running→done/error 随 3s 轮询到达再正常结案。
      qc.setQueryData<StoryboardData>([STORYBOARD_KEY, taskId], (old) =>
        clearFramePreviewState(clearFramePreviewError(old, i), i),
      );
      trackPreviewRaw(i);
    },
    [trackPreviewRaw, qc, taskId],
  );
  const sharedPreviewMut = usePreviewFrame(taskId, { onMutate: trackPreview });
  // M1-2: 一键批量生成全部未预览帧（Task 21: 已移除「草稿加载自动触发一次」）。
  // 批量激活态：在途期间保持 storyboard 3s 轮询（帧逐一出图），全部结案后停。
  // 停滞兜底：记录最近一次 pending 数下降的时间，5min 无进展即停轮询
  // （服务端进程重启致后台循环蒸发时，pending 永不为 0 → 无限轮询，评审③）。
  const [bulkPreviewActive, setBulkPreviewActive] = useState(false);
  const [bulkTotal, setBulkTotal] = useState(0);
  const [bulkBannerDismissed, setBulkBannerDismissed] = useState(false);
  const bulkLastProgressAtRef = useRef(0);
  const bulkPrevPendingCountRef = useRef(-1);

  // W2（2026-08-17）：任务级一键关闭图文卡（计数在 storyboardQuery 定义后派生）。
  const bulkGraphicMut = useBulkDisableGraphic(taskId);

  // 先建 storyboard 查询（interval 读 taskFailed 判 B2 是否停轮询；
  // 第 4 参：在途预览/批量预览期间保持 3s 轮询取 visual_url/preview_error）
  const storyboardQuery = useStoryboardWithAudioPolling(
    taskId,
    audioGenerating,
    taskFailed,
    previewPending.size > 0 || bulkPreviewActive,
  );

  // PR-1 purity 修复：onStarted hoist 成 useCallback——内联传给 hook 的函数体里
  // ref 写 + Date.now() 会被 react-hooks/purity 保守判为渲染期不纯（hook 参数可能
  // 被同步调用，编译器证不了它只在事件后触发）；storyboardQuery 在此定义，故整块
  // 随迁（TDZ）。
  const handlePreviewAllStarted = useCallback(() => {
    bulkPrevPendingCountRef.current = -1;
    bulkLastProgressAtRef.current = Date.now();
    // 2026-07-28 产品评审：记录初始 pending 总数，banner 显示「已生成 X/N」
    setBulkTotal(
      pendingPreviewFrames(storyboardQuery.data?.frames ?? []).length,
    );
    setBulkPreviewActive(true);
  }, [storyboardQuery.data]);
  const previewAllMut = usePreviewAll(taskId, {
    onStarted: handlePreviewAllStarted,
  });
  // W2：graphic 帧计数（一键关闭入口显隐）
  const graphicCardCount = (storyboardQuery.data?.frames ?? []).filter(
    (f) => f.visual_source === "graphic",
  ).length;

  // E1: 用 frames 判 loaded——草稿生成期后端返 200 {status:generating}（无 frames），
  // 不应当已加载（否则进编辑器 sb.frames.map 崩）。无 frames → 显示「生成中」。
  const storyboardLoaded = !!storyboardQuery.data?.frames;

  // B1：storyboard 未加载时才轮询 task（只为区分「生成中」vs「真失败」）；
  // storyboard 加载后立即停（draft 是持久编辑态，否则每 2s 永久轮询 → QPS 爆炸）。
  const taskQuery = useVideoTask(taskId, {
    enabled: !storyboardLoaded && !taskFailed,
  });
  const projectQ = useCourseProject(taskQuery.data?.course_project_id ?? null);
  const courseId = taskQuery.data?.course_project_id;
  // Task 20b: 绑项目时帧预览旁并排展示角色 front_url 缩略图，肉眼校验一致性。
  // F3: 用 serve-by-index GET route（index=0 主角色）；i2i 角色参考由后端 _generate_frame_preview
  // 从项目 store 解析绝对路径，前端此变量仅用于展示。
  const characterFrontUrl =
    // B3 修复（复核阻断项）：serve 已改 id 寻址（M1），下标 "0" 会被当 id 查 → 恒 404。
    // 用 sheets[0].id（存量项目无 id 时后端 store 读路径已固化回填 id，B1）。
    projectQ.data?.character_sheets?.[0]?.id && courseId
      ? `/api/video/projects/${courseId}/character-sheets/${encodeURIComponent(projectQ.data.character_sheets[0].id)}/front`
      : undefined;
  const failedNow = taskQuery.data?.status === "failed";
  useEffect(() => {
    taskFailedRef.current = failedNow; // effect 里更新 ref，lint-safe
  }, [failedNow]);

  const renderMut = useRenderFromDraft(taskId);
  const rerenderMut = useReRenderTask(taskId);
  // review-⑥（2026-09-14）：确认动作先跳详情页再 mutate——409 toast 实际落在详情页，
  // 此时「去编辑分镜」action 是真实导航（修前未传 opts，toast 无出路按钮）。
  const recomposeMut = useRecomposeTask(taskId, {
    onEditStoryboard: () =>
      router.push(`/workspace/video-gen/${taskId}/storyboard`),
  });
  // Task 21（前端 D4）：三个花钱操作的二次确认。点下去即产生生图/TTS/合成计费，
  // 修复前无任何确认——误点一次 = N 次生图。null = 未打开。
  const [confirmBulkPreview, setConfirmBulkPreview] = useState(false);
  const [confirmRender, setConfirmRender] = useState(false);
  const [confirmRecompose, setConfirmRecompose] = useState(false);
  // 2026-10-20 Task 7：只在确认弹窗打开且为 ViMax 任务时请求预估。
  const isVimaxTask = taskQuery.data?.task_type === "vimax";
  const costEstimateQuery = useCostEstimate(taskId, {
    enabled: shouldRequestCostEstimate(
      confirmRender,
      taskQuery.data?.task_type,
    ),
  });
  const costText = formatCostEstimate(costEstimateQuery.data);
  let costEstimateBlock: ReactNode = null;
  if (isVimaxTask) {
    if (costEstimateQuery.isLoading || costEstimateQuery.isFetching) {
      costEstimateBlock = (
        <p className="text-muted-foreground">费用预估加载中…</p>
      );
    } else if (costEstimateQuery.isError) {
      costEstimateBlock = <p className="text-muted-foreground">暂无法预估</p>;
    } else {
      costEstimateBlock = (
        <>
          {costText.durationLine && <p>{costText.durationLine}</p>}
          <p>{costText.costLine}</p>
          {costText.spentLine && <p>{costText.spentLine}</p>}
          {costText.partialNote && (
            <p className="text-muted-foreground">{costText.partialNote}</p>
          )}
        </>
      );
    }
  }
  const pendingPreviewCount = pendingPreviewFrames(
    storyboardQuery.data?.frames ?? [],
  ).length;
  const failedPreviewCnt = failedPreviewCount(
    storyboardQuery.data?.frames ?? [],
  );
  // 批量进行中的已完成数（bulkTotal 在 onStarted 捕获；停滞兜底见 release effect）
  const bulkDone = Math.max(0, bulkTotal - pendingPreviewCount);
  // Task 21（D4）：此处原有一个 useEffect，draft 态且存在未预览帧时挂载后
  // 1500ms 自动 previewAllMutate()。它让「打开草稿看一眼」= N 次生图 + N 次
  // TTS 计费，用户零操作、零确认、无从取消（本轮也确认没有 cancel 端点）。
  // 已删除：批量预览改为纯显式——下方「一键生成全部预览」按钮是唯一入口。
  // 预览异步化：release 由 storyboard 数据驱动（visual_url/preview_error 出现），
  // 超时兜底防后台任务随进程重启蒸发后永远转圈。
  useEffect(() => {
    if (previewPending.size > 0) {
      const frames = storyboardQuery.data?.frames ?? [];
      for (const idx of Array.from(previewPending)) {
        const f = frames.find((fr) => fr.index === idx);
        if (isPreviewResolved(f)) {
          previewStartedAtRef.current.delete(idx);
          previewBaselineRef.current.delete(idx);
          releasePreview(idx);
          if (f?.preview_error) {
            toast.error(`分镜 ${idx + 1} 预览失败：${f.preview_error}`);
          } else {
            // 2026-07-28 产品评审：微调 prompt 后新旧图相近时，
            // 无反馈用户无法判断是否真更新（反复点浪费配额）
            toast.success(`分镜 ${idx + 1} 画面已更新`);
          }
          continue;
        }
        // 条目 34（2026-09-12 缺陷批）：旧后端探测——画面指纹变化且 preview_state
        // 持续缺席（新后端 POST 202 后立即写 running）→ 旧后端已写回新图，按画面
        // 已更新结案；提示页面级一次（勿逐帧重复）。同版部署走上方正常结案。
        if (
          isLegacyBackendPreviewUpdate(previewBaselineRef.current.get(idx), f)
        ) {
          previewStartedAtRef.current.delete(idx);
          previewBaselineRef.current.delete(idx);
          releasePreview(idx);
          if (!legacyBackendHintShownRef.current) {
            legacyBackendHintShownRef.current = true;
            toast.error("后端需升级以获得预览状态跟踪");
          }
          continue;
        }
        const startedAt = previewStartedAtRef.current.get(idx) ?? Date.now();
        if (isPreviewTimedOut(startedAt, Date.now())) {
          previewStartedAtRef.current.delete(idx);
          previewBaselineRef.current.delete(idx);
          releasePreview(idx);
          toast.error(`分镜 ${idx + 1} 预览超时，请重试`);
        }
      }
    }
    // 批量预览：全部帧结案（无待生成）→ 停轮询；停滞超时兜底。
    // 结案判定与停滞进展信号同源（unresolvedPreviewCount：成功有图 OR 失败有
    // error 均算结案）——曾结案判定修了、停滞兜底仍看 pendingPreviewCount
    // （!visual_url，失败帧不让它下降）→ 慢速失败（429/长重试）时在最后一帧
    // 落定前误报「批量预览超时」并提前停轮询（2026-07-28 评审二轮 Major）。
    if (bulkPreviewActive) {
      const unresolvedCnt = unresolvedPreviewCount(
        storyboardQuery.data?.frames ?? [],
      );
      if (unresolvedCnt === 0) {
        setBulkPreviewActive(false);
      } else {
        if (unresolvedCnt !== bulkPrevPendingCountRef.current) {
          bulkPrevPendingCountRef.current = unresolvedCnt;
          bulkLastProgressAtRef.current = Date.now();
        }
        if (isPreviewTimedOut(bulkLastProgressAtRef.current, Date.now())) {
          setBulkPreviewActive(false);
          toast.error("批量预览超时（5 分钟无进展），请重试");
        }
      }
    }
  }, [
    storyboardQuery.data,
    previewPending,
    releasePreview,
    bulkPreviewActive,
    pendingPreviewCount,
  ]);
  const [activeGroup, setActiveGroup] = useState(0);
  // D2（2026-08-19）：受控展开集合——修前 defaultExpanded 仅首次挂载生效，
  // 进度条点击改 activeGroup 无法展开目标组；配合 scene-group-{gi} 锚点滚动定位。
  const [expandedGroups, setExpandedGroups] = useState<Set<number>>(
    new Set([0]),
  );

  const handleGroupClick = useCallback((gi: number) => {
    setActiveGroup(gi);
    setExpandedGroups((prev) => new Set(prev).add(gi));
    requestAnimationFrame(() => {
      document
        .getElementById(`scene-group-${gi}`)
        ?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }, []);
  // M-UX: 分镜增删
  const addFrameMut = useAddFrame(taskId);
  const deleteFrameMut = useDeleteFrame(taskId);
  // D3（2026-08-19）：FrameCard「生成画面描述」——新增帧默认空 image_prompt，
  // 后端 regenerate_prompt 端点此前零前端调用。
  const regenPromptMut = useRegenerateFramePrompt(taskId);

  // 「在 N 镜后插入」/底部追加的落地定位：成功 → 等重拉多出一帧 → 展开所在组
  // + 滚动到新帧卡 + ring 高亮 2s。preInsertCountRef 记录插入前帧数：只有
  // frames 变长才认为重拉落地（防旧数据误触发——老帧恰好占着目标序号）。
  const [justInsertedIndex, setJustInsertedIndex] = useState<number | null>(
    null,
  );
  const preInsertCountRef = useRef(-1);
  const insertClearTimerRef = useRef<ReturnType<typeof setTimeout> | null>(
    null,
  );

  useEffect(() => {
    if (justInsertedIndex === null) return;
    const data = storyboardQuery.data;
    const frames = data?.frames;
    if (!frames || frames.length <= preInsertCountRef.current) return;
    preInsertCountRef.current = frames.length; // 只消费一次
    const gi = deriveDisplayGroups(data?.scene_groups ?? [], frames).findIndex(
      (g) => g.frame_indices.includes(justInsertedIndex),
    );
    if (gi >= 0) {
      setExpandedGroups((prev) => new Set(prev).add(gi));
    }
    requestAnimationFrame(() => {
      document
        .getElementById(`frame-card-${justInsertedIndex}`)
        ?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
    if (insertClearTimerRef.current) clearTimeout(insertClearTimerRef.current);
    insertClearTimerRef.current = setTimeout(
      () => setJustInsertedIndex(null),
      2000,
    );
  }, [justInsertedIndex, storyboardQuery.data]);

  // 卸载清高亮定时器（主 effect 不带 cleanup——deps 变化会误清 2s 计时）
  useEffect(
    () => () => {
      if (insertClearTimerRef.current)
        clearTimeout(insertClearTimerRef.current);
    },
    [],
  );

  // Stop polling once audio is ready.
  useEffect(() => {
    if (storyboardQuery.data?.audio_ready && audioGenerating) {
      setAudioGenerating(false);
    }
  }, [storyboardQuery.data?.audio_ready, audioGenerating]);

  // 渲染顺序（B3 正向信号）：失败且无分镜 → 失败页；失败但有分镜 → 编辑器(改 prompt 重渲)；
  // 生成中（含首屏加载）→ 真错误 → 编辑器
  if (taskFailed && !storyboardLoaded) {
    return (
      <div className="flex flex-col items-center gap-4 p-12">
        <p className="text-red-500">视频任务生成失败。</p>
        <Button variant="outline" onClick={() => router.back()}>
          返回
        </Button>
      </div>
    );
  }

  if (!storyboardLoaded) {
    // 首屏加载 + 生成中（storyboard.json 尚未落盘的 10-30s 窗口）统一显示「生成中」。
    // 极端情况：task 健康但 storyboard 端点真 500（非 404）也会暂显此态，可刷新恢复——
    // 优于当前「创建即报错页」，且 task failed 已由上一分支兜住。
    return (
      <div className="flex flex-col items-center gap-4 p-12">
        <Loader2 className="text-muted-foreground h-8 w-8 animate-spin" />
        <p className="text-muted-foreground">分镜生成中，请稍候…</p>
      </div>
    );
  }

  // 只有「首次加载就失败且无数据」才报错页；storyboard 已加载(数据在)时，即使
  // refetch 409（任务正在 rendering/composing，storyboard 不可编辑但仍可读旧数据）
  // 也进编辑器，不弹"加载失败"吓人。渲染中应去详情页看进度，不是卡在编辑器报错。
  // loadErrMsg 在 if 外取，避开 TS 对 react-query 判别联合的 narrowing 把 error 收成 never。
  const loadErrMsg = storyboardQuery.error?.message;
  if (storyboardQuery.isError && !storyboardLoaded) {
    return (
      <div className="flex flex-col items-center gap-4 p-12">
        <p className="text-red-500">加载分镜数据失败: {loadErrMsg}</p>
        <Button variant="outline" onClick={() => router.back()}>
          返回
        </Button>
      </div>
    );
  }

  const sb = storyboardQuery.data;
  if (!sb) return null;

  // M-UX: 分镜增删仅 draft 态允许（后端门控；ready 态 recompose 不重 TTS 会音画错位，
  // 故增删按钮仅在 draft 显示，ready/composing 不渲染）。
  const handleDeleteFrame = async (index: number) => {
    const confirmed = await confirm(
      buildConfirmOptions(`删除分镜 ${index + 1}？`, {
        variant: "destructive",
      }),
    );
    if (!confirmed) return;
    deleteFrameMut.mutate(index);
  };
  const handleAddFrame = () => {
    if (!sb) return;
    preInsertCountRef.current = sb.frames.length;
    addFrameMut.mutate(
      { narration: NEW_FRAME_NARRATION },
      {
        // 连击竞态（审查 #3）：先清上一轮已启动的高亮 timer——第二次插入的
        // effect 在「等重拉落地」早退分支不清 timer，旧 timer 到点会把新
        // 插入的定位/高亮清掉。锁用例：连击竞态测试。
        onSuccess: () => {
          if (insertClearTimerRef.current)
            clearTimeout(insertClearTimerRef.current);
          setJustInsertedIndex(sb.frames.length); // 追加→新帧落旧长度位
        },
      },
    );
  };
  const handleInsertAfter = (index: number) => {
    if (!sb) return;
    preInsertCountRef.current = sb.frames.length;
    addFrameMut.mutate(
      { narration: NEW_FRAME_NARRATION, index: index + 1 },
      {
        onSuccess: () => {
          if (insertClearTimerRef.current)
            clearTimeout(insertClearTimerRef.current);
          setJustInsertedIndex(index + 1);
        },
      },
    );
  };
  const canEditFrames = sb.status === "draft";

  const videoType = sb.config?.video_type ?? "ai_image";
  // P0：未分组兜底——漏组帧（历史/异常数据、同步缺口）合成尾部「未分组」组，
  // 防分组模式下帧卡不可见也删不掉（删除按钮长在帧卡上）。
  const sceneGroups = deriveDisplayGroups(
    sb?.scene_groups ?? [],
    sb?.frames ?? [],
  );
  const hasGroups = sceneGroups.length > 0;
  // S6：调度表帧绑定行索引（无表/存量任务 = 空 Map，帧卡零调度内容渲染）。
  const shotByIndex = new Map(
    (sb?.shot_table ?? []).map((r) => [r.frame_index, r] as const),
  );
  // failed 任务打开编辑器：改 prompt 后点「重新生成」走 /re-render（重跑视觉，复用分镜）。
  // draft 走 /render（resume_draft）。/render 对 failed 会 409，故按状态分流。
  const isFailedTask = taskFailed;
  const activeRenderMut = isFailedTask ? rerenderMut : renderMut;
  // P2-6: 有分镜旁白为空或仍是占位文本 → 禁用渲染按钮（后端 resume_draft 也会拦，前端先提示）
  const hasPlaceholderNarration = (sb?.frames ?? []).some(
    (f) =>
      !(f.narration ?? "").trim() ||
      (f.narration ?? "").includes(NEW_FRAME_NARRATION),
  );

  return (
    <div className="mx-auto min-h-full max-w-5xl space-y-6 p-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="space-y-1">
          <h2 className="text-lg font-semibold">分镜编辑器</h2>
          <p className="text-muted-foreground text-sm">{sb.title}</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={() => router.back()}>
            返回
          </Button>
          {/* failed 次行动:未改内容直接断点续跑(recompose 复用盘面素材,
              不重新生图)——主按钮「重新生成」仍保留给改了 prompt 的场景。 */}
          {isFailedTask && (
            <Button
              variant="outline"
              disabled={recomposeMut.isPending}
              onClick={() => setConfirmRecompose(true)}
            >
              <RefreshCw className="mr-2 h-4 w-4" />
              {recomposeMut.isPending
                ? "重试合成中…"
                : "重试合成（复用已生成画面）"}
            </Button>
          )}
          <Button
            onClick={() => {
              // 乐观跳转：POST 一发出即去详情页看进度（详情页本身轮询任务状态）。
              // 失败由 hook 层 onError toast 提示；不再等 200 才跳——此前预览长请求
              // 占满浏览器 6 连接时 POST 排队 → 按钮永远转圈。
              // （RQ v5 的 mutate() 回调选项不含 onMutate，直接同步跳转）
              // Task 21：跳转+发起挪到确认框的 onClick，此处只开确认框。
              setConfirmRender(true);
            }}
            disabled={activeRenderMut.isPending || hasPlaceholderNarration}
            title={
              hasPlaceholderNarration
                ? "有分镜旁白为空或仍是占位文本，请先编辑后再渲染"
                : undefined
            }
            className="bg-indigo-600 hover:bg-indigo-700"
          >
            {activeRenderMut.isPending ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Rocket className="mr-2 h-4 w-4" />
            )}
            {isFailedTask ? "重新生成" : "开始渲染"}
          </Button>
        </div>
      </div>

      {isFailedTask && (
        <div className="flex items-center gap-2 rounded-lg border border-amber-300/50 bg-amber-50 p-3 text-sm text-amber-700 dark:bg-amber-950/30 dark:text-amber-300">
          任务上次渲染失败。若只是渲染中断（画面已生成），点「重试合成」从断点续跑，不重新生图；
          若画面本身有问题，修改失败帧的 prompt
          后点「重新生成」重跑视觉（复用旁白/分镜）。
        </div>
      )}

      {/* Scene Group progress bar */}
      {hasGroups ? (
        <SceneGroupProgress
          groups={sceneGroups}
          frames={sb.frames}
          activeGroupIndex={activeGroup}
          onGroupClick={handleGroupClick}
        />
      ) : // P2（2026-08-19）：彩条空态骨架——groups 产自锚定调用、随 storyboard 落盘
      // 晚于帧出现，空态占位消除跳变（remotion 无分组概念，不显示）。
      sb.frames.length > 0 && ["ai_image", "ai_video"].includes(videoType) ? (
        <div className="bg-muted/30 text-muted-foreground flex h-8 w-full items-center justify-center rounded-md border text-xs">
          内容分组生成中…
        </div>
      ) : null}

      {/* Audio preview bar */}
      <AudioPreviewBar
        taskId={taskId}
        audioReady={sb.audio_ready ?? false}
        audioError={sb.audio_error}
        degradationWarning={sb.audio_degradation_warning}
        totalDuration={sb.total_duration}
        frameCount={sb.frames?.length ?? 0}
        onGenerateStart={() => setAudioGenerating(true)}
        onGenerateError={() => setAudioGenerating(false)}
      />

      {/* W2（2026-08-17）：图文卡任务级开关——一键关闭（D2 默认开的用户出口）。 */}
      {graphicCardCount > 0 && (
        <div className="border-border/60 flex items-center justify-between rounded-md border px-3 py-2">
          <span className="text-muted-foreground text-xs">
            图文镜：{graphicCardCount} 帧（数据/流程镜在画面中呈现文字）
          </span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-7 text-xs"
            disabled={bulkGraphicMut.isPending}
            onClick={() =>
              bulkGraphicMut.mutate(undefined, {
                onSuccess: (r) =>
                  toast.success(
                    `已关闭 ${r.disabled} 帧图文镜——点「重新生成」按普通画面渲染`,
                  ),
              })
            }
          >
            {bulkGraphicMut.isPending ? (
              <Loader2 className="mr-1 h-3 w-3 animate-spin" />
            ) : null}
            一键关闭
          </Button>
        </div>
      )}
      {/* M1-2: 一键批量生成全部未预览帧（Task 21 起为唯一入口，不再自动触发）。
          2026-07-28 banner 化：全宽 primary + 进度 X/N + 失败计数 + 可关闭 */}
      {pendingPreviewCount > 0 && !bulkBannerDismissed && (
        <div className="relative space-y-1">
          <Button
            type="button"
            className="w-full"
            disabled={previewAllMut.isPending || bulkPreviewActive}
            onClick={() => setConfirmBulkPreview(true)}
          >
            {previewAllMut.isPending || bulkPreviewActive ? (
              <Loader2 className="mr-1 h-4 w-4 animate-spin" />
            ) : (
              <Sparkles className="mr-1 h-4 w-4" />
            )}
            {bulkPreviewActive
              ? `批量生成预览中… 已生成 ${bulkDone}/${bulkTotal}${failedPreviewCnt > 0 ? `（${failedPreviewCnt} 帧失败）` : ""}`
              : `一键生成全部预览（${pendingPreviewCount} 帧待生成）`}
          </Button>
          {failedPreviewCnt > 0 && !bulkPreviewActive && (
            <p className="text-muted-foreground text-center text-xs">
              其中 {failedPreviewCnt} 帧上次生成失败，点击上方按钮重试
            </p>
          )}
          {/* 批量进行中隐藏 ×——曾可点，关闭后完全失去进度可见性（评审 No.2） */}
          {!bulkPreviewActive && (
            <button
              type="button"
              aria-label="本次不再提示"
              title="本次不再提示（重新进入编辑器恢复）"
              onClick={() => setBulkBannerDismissed(true)}
              className="text-muted-foreground hover:text-foreground absolute -top-1 -right-1 rounded-full p-1 text-xs"
            >
              ×
            </button>
          )}
        </div>
      )}

      {/* Frame rendering: grouped or flat */}
      {hasGroups ? (
        <div className="space-y-3">
          {sceneGroups.map((group, gi) => (
            <SceneGroupCard
              key={gi}
              group={group}
              frames={sb.frames}
              defaultExpanded={gi === activeGroup}
              videoType={videoType}
              anchorId={`scene-group-${gi}`}
              open={expandedGroups.has(gi)}
              onOpenChange={(v) =>
                setExpandedGroups((prev) => {
                  const next = new Set(prev);
                  if (v) next.add(gi);
                  else next.delete(gi);
                  return next;
                })
              }
              renderFrameCard={(frame) => (
                <FrameCard
                  key={frame.index}
                  frame={frame}
                  videoType={frame.visual_source ?? videoType}
                  taskVideoType={videoType}
                  taskId={taskId}
                  referenceImage={characterFrontUrl}
                  previewMut={sharedPreviewMut}
                  regenPromptMut={regenPromptMut}
                  pending={previewPending}
                  onDelete={canEditFrames ? handleDeleteFrame : undefined}
                  onInsertAfter={canEditFrames ? handleInsertAfter : undefined}
                  insertPending={addFrameMut.isPending}
                  highlight={frame.index === justInsertedIndex}
                  shotDirection={shotByIndex.get(frame.index)}
                  audioReady={sb.audio_ready}
                  onGenerateStart={() => setAudioGenerating(true)}
                />
              )}
            />
          ))}
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {sb.frames.map((frame) => (
            <FrameCard
              key={frame.index}
              frame={frame}
              videoType={videoType}
              taskVideoType={videoType}
              taskId={taskId}
              referenceImage={characterFrontUrl}
              previewMut={sharedPreviewMut}
              regenPromptMut={regenPromptMut}
              pending={previewPending}
              onDelete={canEditFrames ? handleDeleteFrame : undefined}
              onInsertAfter={canEditFrames ? handleInsertAfter : undefined}
              insertPending={addFrameMut.isPending}
              highlight={frame.index === justInsertedIndex}
              shotDirection={shotByIndex.get(frame.index)}
              audioReady={sb.audio_ready}
              onGenerateStart={() => setAudioGenerating(true)}
            />
          ))}
        </div>
      )}

      {/* M-UX: 新增分镜（仅 draft 态——ready 态 recompose 不重 TTS 会音画错位） */}
      {canEditFrames && (
        <div className="flex justify-center">
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="text-xs"
            disabled={addFrameMut.isPending}
            onClick={handleAddFrame}
          >
            {addFrameMut.isPending ? (
              <Loader2 className="mr-1 h-3 w-3 animate-spin" />
            ) : (
              <Plus className="mr-1 h-3 w-3" />
            )}
            新增分镜
          </Button>
        </div>
      )}

      <p className="text-muted-foreground text-center text-xs">
        编辑完成后点击右上角「开始渲染」，将正式生成视频
      </p>

      {/* Task 21（前端 D4）：三个花钱操作的二次确认框。文案里写清"要花多少"，
          而不是泛泛的"确定吗"——用户得知道点下去产生多少次付费调用。 */}
      <AlertDialog
        open={confirmBulkPreview}
        onOpenChange={setConfirmBulkPreview}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>生成全部预览图</AlertDialogTitle>
            <AlertDialogDescription>
              将消耗 {pendingPreviewCount} 次 AI
              生图调用（每帧一次，产生费用）。 已有预览图的帧会跳过。确定继续？
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction onClick={() => previewAllMut.mutate()}>
              确认生成（{pendingPreviewCount} 帧）
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={confirmRender} onOpenChange={setConfirmRender}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {isFailedTask ? "重新生成视频" : "开始渲染视频"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              将对 {sb.frames?.length ?? 0} 个分镜执行正式渲染：AI 生成画面 +
              配音合成 +
              视频合成，产生费用且耗时较长。请确认分镜内容与旁白已编辑完成。
            </AlertDialogDescription>
            {isVimaxTask && (
              <div className="bg-muted/40 space-y-1 rounded-md border p-3 text-xs">
                {costEstimateBlock}
                <p className="text-muted-foreground">{costText.disclaimer}</p>
              </div>
            )}
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              className="bg-indigo-600 hover:bg-indigo-700"
              onClick={() => {
                // 乐观跳转：与修复前一致，POST 一发出即去详情页看进度。
                router.push(`/workspace/video-gen/${taskId}`);
                activeRenderMut.mutate(undefined);
              }}
            >
              确认渲染
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <RecomposeConfirmDialog
        open={confirmRecompose}
        onOpenChange={setConfirmRecompose}
        isFailed
        showFailedHint={false}
        onConfirm={() => {
          // 乐观跳转：与修复前一致，POST 一发出即去详情页看进度。
          router.push(`/workspace/video-gen/${taskId}`);
          recomposeMut.mutate();
        }}
      />
    </div>
  );
}
