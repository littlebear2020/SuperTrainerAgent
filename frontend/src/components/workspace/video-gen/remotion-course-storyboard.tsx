"use client";

/**
 * remotion_course 独立分镜编辑器（2026-09-10 独立管线计划 T10/T11/T14）。
 *
 * 独立形态（决策 4）：以「版式卡片」为中心——版式切换 + 静帧预览（主视觉）+
 * 结构化内容编辑（schema 驱动表单）+ 旁白试听 + 素材区（图槽/VideoClipPage 门控）。
 *
 * 明确不渲染（AI 管线交互）：镜头调度行、角色参考、视觉来源切换器、
 * 「一键关闭图文镜」「一键生成全部预览」banner；AI 视频入口仅 VideoClipPage 帧素材区。
 */

import {
  DndContext,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { useQueryClient } from "@tanstack/react-query";
import {
  ChevronDown,
  ChevronUp,
  GripVertical,
  ImageIcon,
  Loader2,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Rocket,
  Trash2,
  Upload,
  VideoIcon,
} from "lucide-react";
import { useRouter } from "next/navigation";
import {
  memo,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
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
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { reparseCourseStoryboard } from "@/core/video-gen/api";
import {
  STORYBOARD_KEY,
  VIDEO_KEY,
  useAddFrame,
  useDeleteFrame,
  useRegenerateFrame,
  useRenderFromDraft,
  useReorderFrames,
  useStoryboardWithAudioPolling,
  useUpdateFrameLayout,
  useUpdateStoryboardFrame,
  useUploadFrameVisual,
  useVideoTask,
} from "@/core/video-gen/hooks";
import {
  LAYOUT_CONTENT_SCHEMA,
  VIDEO_CLIP_LAYOUT,
  checkPlaceholderFrames,
  hasAssetArea,
  isAiMaterialFrame,
  isEmbeddedOrUploadedPath,
  isImageSlotLayout,
  type LayoutContentFieldDef,
} from "@/core/video-gen/layout-content-schema";
import { deriveDisplayGroups } from "@/core/video-gen/scene-groups";
import type { StoryboardFrameData } from "@/core/video-gen/types";
import { cn } from "@/lib/utils";

import { FrameAudioButton } from "./frame-audio-button";
import { LayoutSwitcher } from "./layout-switcher";
import { RemotionCourseAssetArea } from "./remotion-course-asset-area";
import { SceneGroupCard } from "./scene-group-card";
import { StillPreview } from "./still-preview";

// ── 旁白试听（复用既有 /audio?frame= 端点）────────────────────────────────

// ── 结构化内容编辑（T11：schema 驱动表单）─────────────────────────────────

/** unknown → 展示字符串（仅原始类型；对象值返回空——结构化字段走行编辑器）。 */
function toDisplayString(v: unknown): string {
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return "";
}

function ContentFieldEditor({
  field,
  value,
  onChange,
  inputId,
}: {
  field: LayoutContentFieldDef;
  value: unknown;
  onChange: (v: unknown) => void;
  /** DSN-007（2026-09-14）：label 关联用——落到首个可聚焦输入元素。 */
  inputId?: string;
}) {
  const commonCls = "w-full text-sm";

  if (
    field.kind === "text" ||
    field.kind === "code" ||
    field.kind === "number"
  ) {
    return (
      <Input
        className={commonCls}
        type={field.kind === "number" ? "number" : "text"}
        id={inputId}
        value={toDisplayString(value)}
        onChange={(e) =>
          // m13：number 档存原始串——清空即「未设置」（保存时丢弃），中间态不再
          // Number() 出 NaN（JSON.stringify 变 null 发后端被静默丢字段）。
          onChange(e.target.value)
        }
      />
    );
  }
  if (field.kind === "textarea") {
    return (
      <Textarea
        className={cn(commonCls, "min-h-[56px]")}
        id={inputId}
        value={toDisplayString(value)}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  }
  if (field.kind === "list" || field.kind === "chapters") {
    // 列表项 = 每行一条（简单可靠；复杂结构见 steps/chart/cards/options）
    const items = Array.isArray(value)
      ? value.map((x) => toDisplayString(x))
      : [];
    return (
      <Textarea
        className={cn(commonCls, "min-h-[72px]")}
        placeholder="每行一个条目"
        id={inputId}
        value={items.join("\n")}
        onChange={(e) =>
          onChange(
            e.target.value
              .split("\n")
              .map((s) => s.trim())
              .filter(Boolean),
          )
        }
      />
    );
  }
  if (
    field.kind === "steps" ||
    field.kind === "chart" ||
    field.kind === "options" ||
    field.kind === "cards"
  ) {
    const rows: Array<Record<string, string>> = Array.isArray(value)
      ? value.map((r) =>
          typeof r === "object" && r
            ? { ...(r as Record<string, string>) }
            : {},
        )
      : [];
    const sub = (row: Record<string, string>, key: string) => row[key] ?? "";
    const subKeys: Record<
      "steps" | "chart" | "options" | "cards",
      [string, string][]
    > = {
      steps: [
        ["name", "步骤名"],
        ["desc", "说明"],
      ],
      chart: [
        ["label", "标签"],
        ["value", "数值(%)"],
      ],
      options: [
        ["key", "选项键"],
        ["text", "选项内容"],
      ],
      cards: [
        ["label", "标签"],
        ["body", "正文"],
        ["metric", "指标"],
      ],
    };
    return (
      <div className="space-y-2">
        {rows.map((row, i) => (
          <div key={i} className="flex items-start gap-2">
            {subKeys[field.kind].map(([k, label]) => (
              <Input
                // DSN-007：复合编辑器首行首个输入承接 label 关联
                id={
                  i === 0 && k === subKeys[field.kind][0]![0]
                    ? inputId
                    : undefined
                }
                key={k}
                className="text-xs"
                placeholder={label}
                value={sub(row, k)}
                onChange={(e) => {
                  const next = rows.map((r, j) =>
                    j === i ? { ...r, [k]: e.target.value } : r,
                  );
                  onChange(next);
                }}
              />
            ))}
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-8 px-2"
              onClick={() => onChange(rows.filter((_, j) => j !== i))}
              aria-label={`删除第${i + 1}行`}
            >
              <Trash2 className="h-3 w-3" />
            </Button>
          </div>
        ))}
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-7 text-xs"
          onClick={() => onChange([...rows, {}])}
        >
          <Plus className="mr-1 h-3 w-3" /> 添加一行
        </Button>
      </div>
    );
  }
  if (field.kind === "instructor") {
    // 封面讲师（M9）：{initial, name, title} 三字段
    const v =
      typeof value === "object" && value
        ? { ...(value as Record<string, unknown>) }
        : {};
    const rows: [string, string][] = [
      ["initial", "姓氏首字"],
      ["name", "姓名"],
      ["title", "头衔"],
    ];
    return (
      <div className="space-y-2">
        {rows.map(([k, label]) => (
          <div key={k} className="flex items-center gap-2">
            <span className="text-muted-foreground w-20 shrink-0 text-xs">
              {label}
            </span>
            <Input
              className="text-sm"
              value={toDisplayString(v[k])}
              onChange={(e) => onChange({ ...v, [k]: e.target.value })}
            />
          </div>
        ))}
      </div>
    );
  }
  return null;
}

function ContentEditor({
  layout,
  content,
  onSave,
  saving,
}: {
  layout: string | null | undefined;
  content: Record<string, unknown> | null | undefined;
  onSave: (next: Record<string, unknown> | null) => void;
  saving: boolean;
}) {
  const [draft, setDraft] = useState<Record<string, unknown>>(content ?? {});
  const fields = layout ? LAYOUT_CONTENT_SCHEMA[layout] : undefined;
  // m14（2026-09-11 二轮审查）：dirty 由 draft 与已存 content 派生——修前 onSave
  // 后立即 setDirty(false)，PUT 失败回滚时按钮已禁用（须再改一字才能重试）。
  // 派生式在保存成功（服务端值 == draft）时自动翻回可保存，失败时保持可重试。
  const saved = content ?? {};
  const dirty =
    !!fields &&
    fields.some(
      (f) =>
        JSON.stringify(draft[f.key] ?? null) !==
        JSON.stringify(saved[f.key] ?? null),
    );
  if (!layout || !fields || fields.length === 0) return null;

  return (
    <div className="space-y-3">
      {fields.map((field) => (
        <div key={field.key} className="space-y-1">
          {/* DSN-007（2026-09-14）：label 关联输入（读屏/DOM 可查） */}
          <label
            htmlFor={`frame-content-${field.key}`}
            className="text-muted-foreground text-xs"
          >
            {field.label}
          </label>
          <ContentFieldEditor
            field={field}
            value={draft[field.key]}
            inputId={`frame-content-${field.key}`}
            onChange={(v) => {
              setDraft((d) => ({ ...d, [field.key]: v }));
            }}
          />
        </div>
      ))}
      <Button
        type="button"
        size="sm"
        className="h-7 text-xs"
        disabled={!dirty || saving}
        onClick={() => {
          // 全空 → null（回确定性推导）；否则整包写回。
          // m13：number 档在编辑期为原始串（清空=未设置、中间态不落 NaN），
          // 保存时统一 Number 化，非法值丢弃（后端 overlay 不吃 NaN/null）。
          const nonEmpty = Object.fromEntries(
            Object.entries(draft)
              .map(([k, v]) => {
                const field = fields.find((f) => f.key === k);
                if (field?.kind === "number") {
                  const s = typeof v === "string" ? v.trim() : v;
                  if (s === "" || s === null || s === undefined)
                    return [k, null];
                  const n = Number(s);
                  return [k, Number.isFinite(n) ? n : null];
                }
                return [k, v];
              })
              .filter(([, v]) => {
                if (v === null || v === undefined) return false;
                if (Array.isArray(v)) return v.length > 0;
                // 对象值（instructor/steps 行等）：任一子字段非空即保留
                if (typeof v === "object")
                  return Object.values(v).some(
                    (x) => toDisplayString(x).trim() !== "",
                  );
                return toDisplayString(v).trim() !== "";
              }),
          );
          onSave(Object.keys(nonEmpty).length ? nonEmpty : null);
        }}
      >
        {saving ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : null}
        保存画面内容
      </Button>
    </div>
  );
}

// ── 素材区（T11：图槽门控 + 嵌入图守卫 + VideoClipPage AI 视频入口）─────────

/**
 * 配图/视频描述（image_prompt）——本地草稿 + **失焦保存**（M8，2026-09-11 审查：
 * 修前 onChange 每击键一次 PUT，并发乱序 + 输入被轮询 refetch 打闪）。
 */
function PromptField({
  taskId,
  frameIndex,
  initial,
  isVideoSlot,
}: {
  taskId: string;
  frameIndex: number;
  initial: string;
  isVideoSlot: boolean;
}) {
  const updateFrame = useUpdateStoryboardFrame(taskId);
  const [draft, setDraft] = useState(initial);
  useEffect(() => {
    setDraft(initial); // 外部变更（轮询/重生/重新解析）同步本地草稿
  }, [initial]);
  return (
    <div className="space-y-1">
      <label className="text-muted-foreground text-xs">
        {isVideoSlot ? "视频描述" : "配图描述"}
      </label>
      <Textarea
        className="min-h-[48px] text-sm"
        placeholder={
          isVideoSlot
            ? "描述这一段的画面与运镜（生成视频用）"
            : "描述一张承载本页要点/流程/数据的示意图（留空则渲染占位）"
        }
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => {
          if (draft !== initial) {
            updateFrame.mutate({
              frameIndex,
              updates: { image_prompt: draft },
            });
          }
        }}
      />
    </div>
  );
}

function AssetArea({
  taskId,
  frame,
  canRegenerate,
}: {
  taskId: string;
  frame: StoryboardFrameData;
  /** ready/failed 态才有「重新生成」（后端 _regenerate_frame 状态守卫）。 */
  canRegenerate: boolean;
}) {
  // 2026-10-06 素材区合并批：主体收敛到 RemotionCourseAssetArea（与详情页
  // frame-ops 共用），此处只保留分镜页专属的描述编辑（PromptField）。
  const isVideoSlot = frame.layout === VIDEO_CLIP_LAYOUT;
  return (
    <RemotionCourseAssetArea
      taskId={taskId}
      frame={frame}
      canRegenerate={canRegenerate}
    >
      {/* 配图描述（image_prompt）——AI 生成的前置条件之一 */}
      <PromptField
        taskId={taskId}
        frameIndex={frame.index}
        initial={frame.image_prompt ?? ""}
        isVideoSlot={isVideoSlot}
      />
    </RemotionCourseAssetArea>
  );
}

// ── 帧卡 ──────────────────────────────────────────────────────────────────

interface CourseFrameCardProps {
  taskId: string;
  frame: StoryboardFrameData;
  canRegenerate: boolean;
  /** D2：draft 态拖拽重排（手柄式——listeners 只挂手柄，不劫持表单输入）。 */
  reorder?: {
    attributes: Record<string, unknown> | undefined;
    listeners: Record<string, unknown> | undefined;
    setNodeRef: (el: HTMLElement | null) => void;
    style: CSSProperties;
  };
  onMove?: (dir: -1 | 1) => void;
  canMoveUp?: boolean;
  canMoveDown?: boolean;
  /** m12：音频缓存版本（任务 updated_at）——重生配音后防旧缓冲。 */
  audioVersion?: string | number;
  /** B3 复审 F1：ready 态试听 toast 不出重触发 action（后端 already_ready 短路）。 */
  audioReady?: boolean;
}

function CourseFrameCardInner({
  taskId,
  frame,
  canRegenerate,
  reorder,
  onMove,
  canMoveUp,
  canMoveDown,
  audioVersion,
  audioReady,
}: CourseFrameCardProps) {
  const updateFrame = useUpdateStoryboardFrame(taskId);
  const updateLayout = useUpdateFrameLayout(taskId);
  const deleteFrame = useDeleteFrame(taskId);
  const [narration, setNarration] = useState(frame.narration);
  // M7：外部 narration 变化（重新解析/轮询回写）同步本地草稿——修前 key 只按
  // index，reparse 后组件不重挂载显示旧值，且「保存旁白」可一键把新解析覆盖回旧值。
  useEffect(() => {
    setNarration(frame.narration);
  }, [frame.narration]);

  const inner = (
    <div className="bg-card space-y-3 rounded-lg border p-4 shadow-sm">
      {/* 1. 版式切换 + 静帧预览（主视觉，唯一大图） */}
      <div className="flex items-center justify-between gap-2">
        <span className="text-muted-foreground flex items-center gap-1 text-xs">
          {reorder && (
            <span
              {...reorder.attributes}
              {...reorder.listeners}
              className="hover:text-foreground cursor-grab active:cursor-grabbing"
              title="拖拽调整帧顺序"
            >
              <GripVertical className="h-3.5 w-3.5" />
            </span>
          )}
          帧 {frame.index + 1}
        </span>
        <LayoutSwitcher
          value={frame.layout}
          onChange={(layout) =>
            updateLayout.mutate({ frameIndex: frame.index, layout })
          }
        />
      </div>
      <StillPreview
        taskId={taskId}
        frameIndex={frame.index}
        layout={frame.layout}
        className="w-full"
      />

      {/* 2. 结构化内容编辑 */}
      <ContentEditor
        key={frame.layout ?? "__auto__"}
        layout={frame.layout}
        content={frame.layout_content}
        saving={updateFrame.isPending}
        onSave={(layoutContent) =>
          updateFrame.mutate(
            {
              frameIndex: frame.index,
              updates: { layout_content: layoutContent },
            },
            { onSuccess: () => toast.success("画面内容已保存") },
          )
        }
      />

      {/* 3. 旁白编辑 + 试听 */}
      <div className="space-y-1">
        <div className="flex items-center justify-between">
          <label className="text-muted-foreground text-xs">
            旁白（配音文本）
          </label>
          <FrameAudioButton
            taskId={taskId}
            frameIndex={frame.index}
            version={audioVersion}
            audioReady={audioReady}
          />
        </div>
        <Textarea
          className="min-h-[72px] text-sm"
          value={narration}
          onChange={(e) => setNarration(e.target.value)}
        />
        <Button
          type="button"
          size="sm"
          className="h-7 text-xs"
          disabled={
            narration === frame.narration ||
            updateFrame.isPending ||
            !narration.trim()
          }
          onClick={() =>
            updateFrame.mutate(
              {
                frameIndex: frame.index,
                updates: { narration: narration.trim() },
              },
              {
                onSuccess: () => toast.success("旁白已保存（渲染时重新配音）"),
              },
            )
          }
        >
          保存旁白
        </Button>
      </div>

      {/* 4. 素材区（图槽/VideoClipPage 才渲染） */}
      <AssetArea taskId={taskId} frame={frame} canRegenerate={canRegenerate} />

      {/* 5. 帧操作：上移/下移（draft 重排，D2）+ 删除 */}
      <div className="flex items-center justify-end gap-1">
        {onMove && (
          <>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-xs"
              disabled={!canMoveUp}
              title={
                canMoveUp
                  ? "上移一帧"
                  : "上一帧属其他章节（组内重排；跨组请用拖拽或删除重插）"
              }
              onClick={() => onMove(-1)}
            >
              <ChevronUp className="h-3.5 w-3.5" />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-xs"
              disabled={!canMoveDown}
              title={
                canMoveDown
                  ? "下移一帧"
                  : "下一帧属其他章节（组内重排；跨组请用拖拽或删除重插）"
              }
              onClick={() => onMove(1)}
            >
              <ChevronDown className="h-3.5 w-3.5" />
            </Button>
          </>
        )}
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="text-destructive hover:text-destructive h-7 text-xs"
          onClick={() => deleteFrame.mutate(frame.index)}
        >
          <Trash2 className="mr-1 h-3 w-3" /> 删除帧
        </Button>
      </div>
    </div>
  );

  return reorder ? (
    <div ref={reorder.setNodeRef} style={reorder.style}>
      {inner}
    </div>
  ) : (
    inner
  );
}

// 2026-10-06 性能批：memo 化帧卡——流式/乐观更新重渲时未变帧跳过
const CourseFrameCard = memo(CourseFrameCardInner);

// ── 主组件 ────────────────────────────────────────────────────────────────

/** 帧卡签名 key（M7 + m7，2026-09-11）：**不含 index**——重排的乐观更新会即刻
 * 全量 reindex，含 index 的 key 会把区间帧卡重挂载、未保存草稿静默丢失；内容签名
 * 让组件实例跟随内容走（重排只换位置不改内容 → 状态保留）。外部内容变化（重新
 * 解析/回写）仍触发重挂载防旧草稿回写。重复内容帧以出现序号区分（重复帧互换会
 * 重挂载，可接受边界）。 */
function frameCardKey(frames: StoryboardFrameData[], idx: number): string {
  const f = frames[idx];
  if (!f) return `#${idx}`; // 防御（乐观更新窗口外的越界）
  const sig = `${f.narration}:${f.layout ?? ""}:${JSON.stringify(f.layout_content ?? null)}`;
  const dup = frames
    .slice(0, idx)
    .filter(
      (g) =>
        `${g.narration}:${g.layout ?? ""}:${JSON.stringify(g.layout_content ?? null)}` ===
        sig,
    ).length;
  return `${sig}#${dup}`;
}

/** 2026-10-06 性能批：O(n) 预计算全部帧 key（frameCardKey 的 dup 扫描是
 * O(n²)/帧、每渲染 O(n³) 总量——预计算后签名只 stringify 一次/帧）。
 * 输出与 frameCardKey 逐帧一致（同签名按出现序 #0/#1/... 递增）。 */
function frameKeyMap(
  frames: StoryboardFrameData[],
): Map<StoryboardFrameData, string> {
  const seen = new Map<string, number>();
  const map = new Map<StoryboardFrameData, string>();
  frames.forEach((f) => {
    if (!f) return; // 防御（乐观更新窗口外的越界）
    const sig = `${f.narration}:${f.layout ?? ""}:${JSON.stringify(f.layout_content ?? null)}`;
    const dup = seen.get(sig) ?? 0;
    seen.set(sig, dup + 1);
    map.set(f, `${sig}#${dup}`);
  });
  return map;
}

/** D2：单卡 sortable 槽（hooks 恒调用；draggable=false 时原样渲染）。 */
function CourseFrameCardSlot({
  frame,
  sortableEnabled,
  render,
}: {
  frame: StoryboardFrameData;
  sortableEnabled: boolean;
  render: (reorder: CourseFrameCardProps["reorder"]) => ReactNode;
}) {
  const sortable = useSortable({ id: frame.index, disabled: !sortableEnabled });
  if (!sortableEnabled) return <>{render(undefined)}</>;
  return (
    <>
      {render({
        attributes: sortable.attributes as unknown as Record<string, unknown>,
        listeners: sortable.listeners as unknown as
          | Record<string, unknown>
          | undefined,
        setNodeRef: sortable.setNodeRef,
        style: {
          transform: CSS.Transform.toString(sortable.transform),
          transition: sortable.transition,
          opacity: sortable.isDragging ? 0.5 : 1,
        },
      })}
    </>
  );
}

export function RemotionCourseStoryboard({ taskId }: { taskId: string }) {
  const router = useRouter();
  const qc = useQueryClient();
  // M4（2026-09-11 二轮审查）：拿到一次即停轮询——enabled:true 会让 draft 编辑
  // 主态 GET /tasks/{id} 每 2s 永久轮询（路由分流层修过同款坑，这里被重新引入）。
  // resolvedTask 落定后 enabled=false，缓存数据仍可读；task_type/标题在编辑态不变。
  const [resolvedTask, setResolvedTask] = useState<boolean>(false);
  const { data: task } = useVideoTask(taskId, { enabled: !resolvedTask });
  useEffect(() => {
    if (!resolvedTask && task) setResolvedTask(true);
  }, [task, resolvedTask]);
  const { data: storyboard } = useStoryboardWithAudioPolling(taskId, false);
  const renderFromDraft = useRenderFromDraft(taskId);
  const addFrame = useAddFrame(taskId);
  const reorderMut = useReorderFrames(taskId);
  const [reparseOpen, setReparseOpen] = useState(false);
  const [reparseMode, setReparseMode] = useState<"faithful" | "rewrite">(
    "faithful",
  );
  const [reparseConfirm, setReparseConfirm] = useState(false);
  const [reparsePending, setReparsePending] = useState(false);
  const [renderConfirmOpen, setRenderConfirmOpen] = useState(false);

  // useMemo 钉住引用：裸 `?? []` 每次渲染新数组，frameKeys 等 deps 全部失稳
  const frames = useMemo(() => storyboard?.frames ?? [], [storyboard]);
  const status = task?.status ?? storyboard?.status;
  const canRegenerate = status === "ready" || status === "failed";
  // D2（§5.2）：draft 态可重排（后端 reorder 端点 draft-only；复用现有端点）
  const canReorder = status === "draft" && frames.length > 1;
  const groups = deriveDisplayGroups(storyboard?.scene_groups ?? [], frames);
  const placeholder = checkPlaceholderFrames(frames);
  // m8：计数与后端 image_slot_target/video_clip_target 四条件逐字对齐（版式 +
  // 源 + prompt + 无素材）——切版式后残留源的帧不再虚增「将生成 N 张」。
  const aiVideoCount = frames.filter(
    (f) =>
      f.layout === VIDEO_CLIP_LAYOUT &&
      f.visual_source === "ai_video" &&
      !!f.image_prompt?.trim() &&
      !f.local_visual_path &&
      !f.visual_url,
  ).length;
  const aiImageCount = frames.filter(
    (f) =>
      isImageSlotLayout(f.layout) &&
      f.visual_source === "ai_image" &&
      !!f.image_prompt?.trim() &&
      !f.local_visual_path &&
      !f.visual_url,
  ).length;
  // 2026-10-06 性能批：帧 key 预计算（frames 变化时 O(n) 一次）
  const frameKeys = useMemo(() => frameKeyMap(frames), [frames]);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
  );
  // M3：组边界守卫——分组视图跨组 ↑↓ 被后端"组归属不变"语义吞掉（点击零反馈），
  // 组边界禁用并提示（跨组调整用拖拽/删除重插）。
  const groupOf = new Map<number, number>();
  if (groups.length > 0) {
    groups.forEach((g, gi) => {
      for (const fi of g.frame_indices ?? []) groupOf.set(fi, gi);
    });
  }
  const sameGroup = (a: number, b: number) =>
    groups.length === 0 || groupOf.get(a) === groupOf.get(b);

  const doReparse = async () => {
    setReparsePending(true);
    try {
      const r = await reparseCourseStoryboard(taskId, reparseMode);
      toast.success(
        `已按${reparseMode === "faithful" ? "保真" : "改写"}档重新解析：${r.n_frames} 帧`,
      );
      // m12：限定 query key——无参 invalidateQueries 会全量失效（含列表/项目等无关缓存）
      await qc.invalidateQueries({ queryKey: [STORYBOARD_KEY, taskId] });
      await qc.invalidateQueries({ queryKey: [VIDEO_KEY, taskId] });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "重新解析失败");
    } finally {
      setReparsePending(false);
      setReparseConfirm(false);
      setReparseOpen(false);
    }
  };

  const startRender = () => {
    renderFromDraft.mutate(undefined, {
      onSuccess: () => router.push(`/workspace/video-gen/${taskId}`),
    });
  };

  const renderFrameCard = (frame: StoryboardFrameData) => (
    <CourseFrameCardSlot
      key={frameKeys.get(frame) ?? `#${frames.indexOf(frame)}`}
      frame={frame}
      sortableEnabled={canReorder && groups.length === 0}
      render={(reorder) => (
        <CourseFrameCard
          taskId={taskId}
          frame={frame}
          canRegenerate={canRegenerate}
          reorder={reorder}
          audioVersion={task?.updated_at ?? undefined}
          audioReady={storyboard?.audio_ready}
          onMove={
            canReorder
              ? (dir) =>
                  // M3（2026-09-11 二轮审查）：后端 reorder 是 insert-before 语义
                  //（to>from 时 insert_pos=to-1）——下移须发 index+2 才落得进下一格
                  //（发 index+1 会插回原位 = 恒 no-op）。
                  reorderMut.mutate({
                    fromIndex: frame.index,
                    toIndex: frame.index + (dir === -1 ? -1 : 2),
                  })
              : undefined
          }
          canMoveUp={
            canReorder &&
            frame.index > 0 &&
            sameGroup(frame.index, frame.index - 1)
          }
          canMoveDown={
            canReorder &&
            frame.index < frames.length - 1 &&
            sameGroup(frame.index, frame.index + 1)
          }
        />
      )}
    />
  );

  const handleDragEnd = (e: DragEndEvent) => {
    const { active, over } = e;
    if (!over || active.id === over.id) return;
    const from = frames.findIndex((f) => f.index === active.id);
    const to = frames.findIndex((f) => f.index === over.id);
    if (from === -1 || to === -1) return;
    reorderMut.mutate({ fromIndex: from, toIndex: to });
  };

  return (
    <div className="mx-auto max-w-5xl space-y-4 p-4">
      {/* 头部：任务标题/状态 + 重新解析 + 开始渲染 */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <h1 className="truncate text-lg font-semibold">
            {task?.title ?? storyboard?.title ?? taskId}
          </h1>
          <p className="text-muted-foreground text-xs">
            文字动效 · {frames.length} 帧 · 状态 {status ?? "…"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={status !== "draft"}
            onClick={() => setReparseOpen(true)}
          >
            <RefreshCw className="mr-1 h-3.5 w-3.5" /> 重新解析
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={status !== "draft" || renderFromDraft.isPending}
            onClick={() => setRenderConfirmOpen(true)}
          >
            {renderFromDraft.isPending ? (
              <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
            ) : (
              <Rocket className="mr-1 h-3.5 w-3.5" />
            )}
            开始渲染
          </Button>
        </div>
      </div>

      {/* 插入帧 + VideoClipPage 可发现性引导（四轮修订） */}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => addFrame.mutate({})}
        >
          <Plus className="mr-1 h-3.5 w-3.5" /> 插入帧
        </Button>
        <span className="text-muted-foreground text-xs">
          需要视频片段？插入帧后把版式切到「视频剪辑页」，上传视频或 AI 生成。
          {canReorder && " 草稿态可拖拽手柄或用 ↑↓ 按钮调整帧顺序。"}
        </span>
      </div>

      {/* 分组导航（章节锚点） */}
      {groups.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {groups.map((g, i) => (
            <a
              key={i}
              href={`#course-group-${i}`}
              className="text-muted-foreground hover:text-foreground text-xs underline-offset-2 hover:underline"
            >
              {g.title}
            </a>
          ))}
        </div>
      )}

      {/* 帧列表 */}
      {groups.length > 0 ? (
        <div className="space-y-4">
          {groups.map((g, i) => (
            <div key={i} id={`course-group-${i}`}>
              <SceneGroupCard
                group={g}
                frames={frames}
                videoType="remotion_course"
                renderFrameCard={renderFrameCard}
                defaultExpanded={i === 0}
              />
            </div>
          ))}
        </div>
      ) : canReorder ? (
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragEnd={handleDragEnd}
        >
          <SortableContext
            items={frames.map((f) => f.index)}
            strategy={verticalListSortingStrategy}
          >
            <div className="grid gap-4 sm:grid-cols-2">
              {frames.map(renderFrameCard)}
            </div>
          </SortableContext>
        </DndContext>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2">
          {frames.map(renderFrameCard)}
        </div>
      )}

      {/* 重新解析弹层（决策 15）：选档 + 二次确认 */}
      <AlertDialog open={reparseOpen} onOpenChange={setReparseOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>重新解析课程脚本</AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-2">
                <label className="flex items-start gap-2 font-normal">
                  <input
                    type="radio"
                    name="reparse-mode"
                    className="mt-1"
                    checked={reparseMode === "faithful"}
                    onChange={() => setReparseMode("faithful")}
                  />
                  保真解析（默认）——只优化表达使其适合口播，不改大意与数据
                </label>
                <label className="flex items-start gap-2 font-normal">
                  <input
                    type="radio"
                    name="reparse-mode"
                    className="mt-1"
                    checked={reparseMode === "rewrite"}
                    onChange={() => setReparseMode("rewrite")}
                  />
                  AI 改写——改写为口语讲解稿
                </label>
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction onClick={() => setReparseConfirm(true)}>
              下一步
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 重新解析二次确认：整量覆盖明示 */}
      <AlertDialog open={reparseConfirm} onOpenChange={setReparseConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认重新解析</AlertDialogTitle>
            <AlertDialogDescription>
              将以「{reparseMode === "faithful" ? "保真" : "改写"}」档重新解析，
              手动修改与已上传素材将丢失，文档内嵌图将按标记重新分配。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>返回</AlertDialogCancel>
            <AlertDialogAction
              disabled={reparsePending}
              onClick={() => void doReparse()}
            >
              {reparsePending ? (
                <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
              ) : null}
              确认重新解析
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 渲染确认弹层（决策 16）：流程文案 + 占位检查 */}
      <AlertDialog open={renderConfirmOpen} onOpenChange={setRenderConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>开始渲染</AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-2 text-sm">
                <p>
                  配音合成 + 槽位配图生成（{aiImageCount} 张）+ 模板渲染
                  {aiVideoCount > 0
                    ? ` + AI 视频生成（${aiVideoCount} 段，约 ${Math.ceil(aiVideoCount * 2)} 分钟）`
                    : ""}
                </p>
                {(placeholder.imagePlaceholders.length > 0 ||
                  placeholder.videoPlaceholders.length > 0) && (
                  <div className="rounded border border-amber-500/40 bg-amber-500/10 p-2 text-xs">
                    {placeholder.imagePlaceholders.length > 0 && (
                      <p>
                        ⚠️ {placeholder.imagePlaceholders.length} 帧无配图（第{" "}
                        {placeholder.imagePlaceholders
                          .map((i) => i + 1)
                          .join("、")}
                        帧），将渲染占位画面
                      </p>
                    )}
                    {placeholder.videoPlaceholders.length > 0 && (
                      <p>
                        ⚠️ {placeholder.videoPlaceholders.length} 帧无视频（第{" "}
                        {placeholder.videoPlaceholders
                          .map((i) => i + 1)
                          .join("、")}
                        帧），将渲染播放键占位
                      </p>
                    )}
                  </div>
                )}
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={() => setRenderConfirmOpen(false)}>
              去补充
            </AlertDialogCancel>
            <AlertDialogAction onClick={startRender}>
              仍要渲染
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
