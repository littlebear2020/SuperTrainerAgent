"use client";

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
  horizontalListSortingStrategy,
  useSortable,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { Film } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  fetchStillPreview,
  getFrameVisualUrl,
  stillPreview,
} from "@/core/video-gen/api";
import {
  computeClipLayout,
  frameBadge,
  frameSourceBadge,
  GAP_PX,
  timeToFrameIndex,
  timeToPx,
} from "@/core/video-gen/frameStrip";
import { layoutFallbackImage } from "@/core/video-gen/types";
import type { StoryboardFrameData } from "@/core/video-gen/types";
import { cn } from "@/lib/utils";

export interface FrameStripProps {
  frames: StoryboardFrameData[];
  taskId: string;
  /** 播放头（秒）。仅 ready 态由播放器 onTimeUpdate 提供。 */
  currentTime?: number;
  selectedIndex?: number | null;
  dirtyFrames?: number[];
  /** Task 3: 冲突帧高亮（recompose 409 解析出的帧 index）。 */
  highlightIndices?: number[];
  onSelect?: (frameIndex: number) => void;
  onSeek?: (seconds: number) => void;
  /** draft 态拖拽重排；ready/composing 不传（后端 reorder 仅 draft）。 */
  onReorder?: (fromIndex: number, toIndex: number) => void;
  /** 缩略图版本号：重生/上传成功后传 Date.now() 破缓存（Task 3）。 */
  thumbVersion?: number;
  /** content-match T8③：任务类型（ai_image/ai_video/...），来源徽标回落用。 */
  taskType?: string;
  /**
   * T-E9（F8，2026-09-09 feedback-round1）：remotion_course 缩略图模式。
   * "render"：ready 后渲真实成片画面（逐帧 still 管线 + D9 缓存）替代素材图；
   * "m0"：draft 期用版式示意图（素材≠成片画面，未引用素材不再顶替）。
   * 缺省 = 素材图（ai_image/ai_video 既有行为）。
   */
  stillMode?: "render" | "m0";
  /** T-E9：remotion 成片 seek 偏移（intro 帧数/30——帧条 startTime 是内容轴）。 */
  seekOffsetSec?: number;
  className?: string;
}

/** 缩略图源：本地帧（上传/本地化）走 B1 服务路由，远程 AI 帧直接用 url。 */
export function frameThumbSrc(
  taskId: string,
  frame: StoryboardFrameData,
  version?: number,
): string | null {
  if (frame.local_visual_path)
    return getFrameVisualUrl(taskId, frame.index, version);
  return frame.visual_url ?? null;
}

/**
 * T-E9（F8）：remotion_course ready 缩略图——逐帧 still 管线渲**真实成片画面**
 * （D9 缓存，重复打开秒回）；渲染中/失败回落版式示意图（不阻塞帧条）。
 *
 * T-E9（v4.2 批量约束）：懒加载——still 槽串行（每张 10-30s），mount 即对每帧
 * 发 stillPreview 会让 10 帧任务首开详情页分钟级出图且堵用户交互预览；仅滚入
 * 可视区（rootMargin 200px 预载）才触发。批量缩略图走 bulk=true（后端
 * priority=10，低于交互预览 20 / 成片 35）。
 */
function StillStripThumb({
  taskId,
  frameIndex,
  layout,
}: {
  taskId: string;
  frameIndex: number;
  layout?: string | null;
}) {
  const [src, setSrc] = useState<string | null>(null);
  const urlRef = useRef<string | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [visible, setVisible] = useState(false);

  // T-E9 v4.2：可视区检测（IntersectionObserver 不可用——jsdom/旧浏览器——
  // 时直接置 visible，保持修前「mount 即发起」行为）
  useEffect(() => {
    const el = containerRef.current;
    if (!el || typeof IntersectionObserver === "undefined") {
      setVisible(true);
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setVisible(true);
          io.disconnect();
        }
      },
      { rootMargin: "200px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    if (!visible) return;
    let cancelled = false;
    void (async () => {
      try {
        // bulk=true：批量缩略图 priority=10，不堵交互预览（T-E9 v4.2）
        const start = await stillPreview(taskId, frameIndex, true);
        for (let i = 0; i < 15 && !cancelled; i++) {
          const r = await fetchStillPreview(
            taskId,
            frameIndex,
            start.cache_key,
          );
          if (cancelled) return;
          if (r.status === "ready" && r.url) {
            if (urlRef.current) URL.revokeObjectURL(urlRef.current);
            urlRef.current = r.url;
            setSrc(r.url);
            return;
          }
          if (r.status === "failed") return;
          await new Promise((res) => setTimeout(res, 2000));
        }
      } catch {
        // 静默回落示意图
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [taskId, frameIndex, visible]);

  useEffect(() => {
    return () => {
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    };
  }, []);

  return (
    <div ref={containerRef} className="h-full w-full">
      {}
      <img
        loading="lazy"
        src={src ?? layoutFallbackImage(layout)}
        alt={`帧 ${frameIndex + 1} 成片画面`}
        className="h-full w-full object-cover"
        onError={() => setSrc(null)}
      />
    </div>
  );
}

function ThumbClip({
  frame,
  taskId,
  layout,
  selected,
  dirty,
  highlighted,
  thumbVersion,
  sortable,
  taskType,
  stillMode,
  seekOffsetSec,
  onSelect,
  onSeek,
}: {
  frame: StoryboardFrameData;
  taskId: string;
  layout: { leftPx: number; widthPx: number; startTime: number };
  selected: boolean;
  dirty: boolean;
  highlighted: boolean;
  thumbVersion?: number;
  sortable: boolean;
  /** content-match T8③：来源徽标在 visual_source 缺省时回落任务类型。 */
  taskType?: string;
  stillMode?: "render" | "m0";
  seekOffsetSec?: number;
  onSelect?: (frameIndex: number) => void;
  onSeek?: (seconds: number) => void;
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: frame.index, disabled: !sortable });

  // T-E9：remotion_course 缩略图模式（render=成片画面 / m0=版式示意）
  const stillThumb =
    stillMode === "render" ? (
      <StillStripThumb
        taskId={taskId}
        frameIndex={frame.index}
        layout={frame.layout}
      />
    ) : null;
  const m0Src = stillMode === "m0" ? layoutFallbackImage(frame.layout) : null;
  const src = m0Src ?? frameThumbSrc(taskId, frame, thumbVersion);
  const [imgFailed, setImgFailed] = useState(false);
  // src（含版本号）变化 -> 复位重试（与 FrameCard 同语义：裂图不是终态）
  useEffect(() => {
    setImgFailed(false);
  }, [src]);

  return (
    <div
      ref={setNodeRef}
      style={{
        width: layout.widthPx,
        transform: CSS.Transform.toString(transform),
        transition,
        opacity: isDragging ? 0.5 : 1,
      }}
      className={cn(
        "group relative h-14 shrink-0 cursor-pointer overflow-hidden rounded-md border",
        sortable && "cursor-grab active:cursor-grabbing",
        selected
          ? "ring-2 ring-indigo-500 ring-offset-1"
          : "border-border/50 hover:border-indigo-300",
        highlighted && "animate-pulse ring-2 ring-red-500",
      )}
      title={`#${frame.index + 1}（${frame.duration?.toFixed(1) ?? "?"}s）${frame.narration ?? ""}`}
      onClick={() => {
        onSelect?.(frame.index);
        // T-E9：remotion 成片内容轴 → 播放轴（intro 偏移）
        onSeek?.(layout.startTime + (seekOffsetSec ?? 0));
      }}
      {...attributes}
      {...listeners}
    >
      {stillThumb ??
        (src && !imgFailed ? (
          frame.visual_type === "video" ? (
            <video
              src={src}
              className="h-full w-full object-cover"
              muted
              playsInline
            />
          ) : (
            <img
              loading="lazy"
              src={src}
              alt={`帧 ${frame.index + 1}`}
              className="h-full w-full object-cover"
              onError={() => setImgFailed(true)}
            />
          )
        ) : (
          <div
            className="bg-muted flex h-full w-full items-center justify-center"
            title={
              imgFailed ? "预览已过期或文件缺失--重新生成可恢复" : undefined
            }
          >
            <Film className="text-muted-foreground/40 h-4 w-4" />
          </div>
        ))}
      <span className="absolute bottom-0 left-0 bg-black/60 px-1 text-[10px] text-white">
        {frame.index + 1}
      </span>
      {/* content-match T8③: 来源类型徽标（图/视/卡）置右下角——左上失败/复用、右上
          dirty 黄点、左下帧号，四角不冲突 */}
      {(() => {
        const src2 = frameSourceBadge(frame, taskType);
        return src2 ? (
          <span
            className={cn(
              "absolute right-0 bottom-0 rounded-tl px-1 text-[10px] text-white/90",
              src2.kind === "card"
                ? "bg-sky-500/80"
                : src2.kind === "video"
                  ? "bg-violet-500/80"
                  : "bg-emerald-500/70",
            )}
            title={
              src2.kind === "card"
                ? "图表/图文（graphic）"
                : src2.kind === "video"
                  ? "AI 生视频"
                  : "AI 生图"
            }
          >
            {src2.label}
          </span>
        ) : null;
      })()}
      {/* P2-9: 失败/复用帧逐帧徽标（failed 优先于 reuse）。置左上角--
          右上角是 dirty 黄点（top-0.5 right-0.5），左下角是帧号，避免重叠遮挡 */}
      {(() => {
        const badge = frameBadge(frame);
        return badge ? (
          <span
            className={cn(
              "absolute top-0.5 left-0.5 rounded px-1 text-[10px] text-white",
              badge === "failed" ? "bg-red-500/90" : "bg-amber-500/90",
            )}
          >
            {badge === "failed" ? "失败" : "复用"}
          </span>
        ) : null;
      })()}
      {dirty && (
        <span
          className="absolute top-0.5 right-0.5 h-2 w-2 rounded-full bg-amber-400"
          title="已修改，待重新合成"
        />
      )}
    </div>
  );
}

export function FrameStrip({
  frames,
  taskId,
  currentTime,
  selectedIndex,
  dirtyFrames,
  highlightIndices,
  onSelect,
  onSeek,
  onReorder,
  thumbVersion,
  taskType,
  stillMode,
  seekOffsetSec,
  className,
}: FrameStripProps) {
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
  );
  const layout = useMemo(() => computeClipLayout(frames), [frames]);
  const totalDuration = layout.reduce(
    (s, c) => Math.max(s, c.startTime + c.duration),
    0,
  );
  // 总宽 = 末帧 leftPx + widthPx（末帧后无 gap）
  const totalWidthPx = layout.length
    ? layout[layout.length - 1]!.leftPx + layout[layout.length - 1]!.widthPx
    : 0;

  // T-E9：remotion 播放轴 → 内容轴（intro 偏移）——播放头定位与帧命中共用
  const contentTime =
    currentTime !== undefined && seekOffsetSec
      ? Math.max(0, currentTime - seekOffsetSec)
      : currentTime;
  const playheadIdx =
    contentTime !== undefined ? timeToFrameIndex(layout, contentTime) : null;
  // 播放头用 timeToPx 分段线性映射（像素坐标系，gap 已计入 leftPx）
  const playheadPx =
    contentTime !== undefined ? timeToPx(layout, contentTime) : null;

  // ── 滚动跟随 ──────────────────────────────────────────────────────────
  const scrollRef = useRef<HTMLDivElement>(null);
  const lastUserScrollRef = useRef(0);
  const prevSelectedRef = useRef<number | null | undefined>(undefined);
  // P3-1: 标记程序滚动中，区分用户拖滚动条的手动滚动
  const isAutoScrollingRef = useRef(false);

  // P1-1: 用户手势事件记录冷却时间（不用 onScroll --程序滚动会自反馈触发冷却，
  // 导致短帧场景播放头跟一次卡 3 秒再猛跳）
  // P3-1: 原生滚动条拖拽不触发 wheel/pointerdown/touchstart，
  // 用 onScroll + isAutoScrollingRef 标记兜底：程序滚动不记冷却，用户滚动（含滚动条拖拽）记冷却
  const markUserScroll = useCallback(() => {
    lastUserScrollRef.current = Date.now();
  }, []);

  const handleScroll = useCallback(() => {
    // 程序滚动不记冷却
    if (isAutoScrollingRef.current) return;
    lastUserScrollRef.current = Date.now();
  }, []);

  // 播放头跨帧时，若目标帧不在视口内且用户未在手动滚动 -> 平滑滚动跟上
  useEffect(() => {
    if (currentTime === undefined || !scrollRef.current) return;
    const container = scrollRef.current;
    // 仅在播放态触发自动滚动
    if (playheadIdx === null) return;

    const targetLayout = layout.find((c) => c.frameIndex === playheadIdx);
    if (!targetLayout) return;

    const frameLeft = targetLayout.leftPx;
    const frameRight = targetLayout.leftPx + targetLayout.widthPx;
    const viewLeft = container.scrollLeft;
    const viewRight = container.scrollLeft + container.clientWidth;

    // 帧已在视口内 -> 不动
    if (frameLeft >= viewLeft && frameRight <= viewRight) return;

    // 3 秒内有用户手动滚动 -> 不打断
    if (Date.now() - lastUserScrollRef.current < 3000) return;

    // P3-1: 标记程序滚动，防止 onScroll 把它误记为用户滚动
    isAutoScrollingRef.current = true;
    const targetScroll =
      frameLeft - container.clientWidth / 2 + targetLayout.widthPx / 2;
    container.scrollTo({ left: Math.max(0, targetScroll), behavior: "smooth" });
    // smooth 动画持续时间约 300-500ms，安全余量到 800ms
    window.setTimeout(() => {
      isAutoScrollingRef.current = false;
    }, 800);
  }, [playheadIdx, currentTime, layout]);

  // P1-2: selectedIndex 变化 -> 滚动到选中帧（支撑 Task 3 的自动定位）
  // 仅在值真正变化时滚动一次（useRef 比较），不在每渲染都跑
  useEffect(() => {
    if (selectedIndex === prevSelectedRef.current) return;
    prevSelectedRef.current = selectedIndex;
    if (
      selectedIndex === null ||
      selectedIndex === undefined ||
      !scrollRef.current
    )
      return;
    const container = scrollRef.current;
    const targetLayout = layout.find((c) => c.frameIndex === selectedIndex);
    if (!targetLayout) return;

    const frameLeft = targetLayout.leftPx;
    const frameRight = targetLayout.leftPx + targetLayout.widthPx;
    const viewLeft = container.scrollLeft;
    const viewRight = container.scrollLeft + container.clientWidth;

    if (frameLeft >= viewLeft && frameRight <= viewRight) return;

    // P3-1: 标记程序滚动
    isAutoScrollingRef.current = true;
    const targetScroll =
      frameLeft - container.clientWidth / 2 + targetLayout.widthPx / 2;
    container.scrollTo({ left: Math.max(0, targetScroll), behavior: "smooth" });
    window.setTimeout(() => {
      isAutoScrollingRef.current = false;
    }, 800);
  }, [selectedIndex, layout]);

  if (!frames.length) return null;

  function handleDragEnd(e: DragEndEvent) {
    const { active, over } = e;
    if (!over || active.id === over.id || !onReorder) return;
    const from = frames.findIndex((f) => f.index === active.id);
    const to = frames.findIndex((f) => f.index === over.id);
    if (from === -1 || to === -1) return;
    onReorder(from, to);
  }

  const highlightSet = highlightIndices ? new Set(highlightIndices) : null;

  return (
    <div
      className={cn(
        "border-border/50 space-y-2 rounded-lg border p-3",
        className,
      )}
    >
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">分镜</h3>
        <span className="text-muted-foreground text-xs">
          {totalDuration.toFixed(1)}s
          {onReorder && <span className="ml-2 text-[10px]">拖拽可重排</span>}
        </span>
      </div>
      {/* 像素化布局：滚动容器在内层，播放头随内容一起滚 */}
      <div className="relative">
        <div
          ref={scrollRef}
          onScroll={handleScroll}
          onWheel={markUserScroll}
          onPointerDown={markUserScroll}
          onTouchStart={markUserScroll}
          className="flex overflow-x-auto"
        >
          <div className="relative" style={{ width: totalWidthPx }}>
            <DndContext
              sensors={sensors}
              collisionDetection={closestCenter}
              onDragEnd={handleDragEnd}
            >
              <SortableContext
                items={frames.map((f) => f.index)}
                strategy={horizontalListSortingStrategy}
              >
                <div className="flex" style={{ gap: GAP_PX }}>
                  {frames.map((frame) => {
                    const l = layout.find((c) => c.frameIndex === frame.index);
                    // R4: 防御性 null guard（当前同源同步渲染必命中，防 computeClipLayout 未来过滤帧）
                    if (!l) return null;
                    return (
                      <ThumbClip
                        key={frame.index}
                        frame={frame}
                        taskId={taskId}
                        layout={l}
                        selected={
                          selectedIndex === frame.index ||
                          playheadIdx === frame.index
                        }
                        dirty={dirtyFrames?.includes(frame.index) ?? false}
                        highlighted={highlightSet?.has(frame.index) ?? false}
                        thumbVersion={thumbVersion}
                        sortable={!!onReorder}
                        taskType={taskType}
                        stillMode={stillMode}
                        seekOffsetSec={seekOffsetSec}
                        onSelect={onSelect}
                        onSeek={onSeek}
                      />
                    );
                  })}
                </div>
              </SortableContext>
            </DndContext>
            {playheadPx !== null && (
              <div
                className="pointer-events-none absolute top-0 bottom-0 w-0.5 bg-red-500"
                style={{ left: playheadPx }}
              />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
