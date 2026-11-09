"use client";

import { Loader2, Mic, RefreshCw, Upload, X } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { getFrameVisualUrl } from "@/core/video-gen/api";
import {
  useRegenerateFrame,
  useUpdateStoryboardFrame,
  useUploadFrameVisual,
} from "@/core/video-gen/hooks";
import type {
  RefInjectionRecord,
  StoryboardFrameData,
} from "@/core/video-gen/types";

import { RemotionCourseFrameOps } from "./remotion-course-frame-ops";
import { VisualSourceSwitcher } from "./visual-source-switcher";

export interface FrameOpsPanelProps {
  taskId: string;
  frame: StoryboardFrameData;
  taskStatus: string;
  onClose: () => void;
  /** B1: 缩略图破缓存版本号——重生/上传成功后被 bump，强制 <img loading="lazy"> 重新请求同名文件。 */
  thumbVersion?: number;
  /** M5: 帧级参考注入留痕（任务详情 result.ref_injection 按帧匹配）——「参考资产」标签数据源。 */
  refRecord?: RefInjectionRecord;
  /** B1: 视觉变更成功回调（重生/上传成功时触发，父组件据此 bump thumbVersion）。 */
  onVisualChanged?: () => void;
  /** H4: 该帧重生在途（异步化后由父组件 tracker 驱动，关面板不丢追踪）。 */
  regenPending?: boolean;
  /** H4: 重生点击回调（父组件据此 track pending + 乐观清旧 regen_state）。 */
  onRegenStart?: (frameIndex: number) => void;
  /** H4: 同步校验错误回调（立即释放 pending）。 */
  onRegenSyncError?: (frameIndex: number) => void;
  /**
   * T13（2026-09-10 独立管线计划，决策 14）：任务类型——remotion_course 换专属形态
   * （版式切换 + 静帧预览 + 素材区，替换「重生画面」三件套）。缺省走 AI 管线形态
   * （既有调用零改动兼容）。
   */
  videoType?: string;
}

/** M5: 分镜级「参考资产」标签文案——复用 sidecar 注入数据（决策 19/B15）。
 *
 * 实体名归一：style_anchor → 「锚图」；ai_image 任务级记录的 entity="ref" → 「参考图」；
 * 机制 M（2026-08-31）预览图首帧条目 entity="preview" → 「预览图（首帧）」。
 * 同实体多张（三视图全传）聚合为 ×N；无注入 → null（不渲染标签）。
 */
export function refAssetsLabel(
  record: RefInjectionRecord | undefined,
): string | null {
  const injected = record?.injected_refs ?? [];
  if (!injected.length) return null;
  const counts = new Map<string, number>();
  for (const r of injected) {
    const name =
      r.entity === "style_anchor"
        ? "锚图"
        : r.entity === "ref"
          ? "参考图"
          : r.entity === "preview"
            ? "预览图（首帧）"
            : r.entity;
    counts.set(name, (counts.get(name) ?? 0) + 1);
  }
  return [...counts.entries()].map(([n, c]) => `${n} ×${c}`).join(" / ");
}

/** 选中帧操作面板：大图预览 + prompt/旁白编辑（ready 已放开，Task 4 后端）+ 重生/上传。 */
export function FrameOpsPanel({
  taskId,
  frame,
  taskStatus,
  onClose,
  thumbVersion,
  refRecord,
  onVisualChanged,
  regenPending,
  onRegenStart,
  onRegenSyncError,
  videoType,
}: FrameOpsPanelProps) {
  const regenMut = useRegenerateFrame(taskId, {
    onMutate: (i) => onRegenStart?.(i),
    onSyncError: (i) => onRegenSyncError?.(i),
  });
  const uploadMut = useUploadFrameVisual(taskId);
  const updateMut = useUpdateStoryboardFrame(taskId);
  const uploadRef = useRef<HTMLInputElement | null>(null);
  const [narration, setNarration] = useState(frame.narration ?? "");
  const [prompt, setPrompt] = useState(frame.image_prompt ?? "");
  // content-match T8②：详情页逐镜切换视觉来源（此前仅草稿编辑器有）
  const [source, setSource] = useState(frame.visual_source ?? "");

  const editable =
    taskStatus === "ready" || taskStatus === "draft" || taskStatus === "failed";
  const canRegen = taskStatus === "ready" || taskStatus === "failed";
  // H1c: 上传仅 draft/ready（后端对 failed 409）——failed 态按钮可点必失败且此前静默。
  const canUpload = taskStatus === "ready" || taskStatus === "draft";
  // B1: 本地帧传 thumbVersion 破缓存——同名文件（frame_NNN.jpg/img_NNNN.png）被覆盖后
  // src 字符串不变 → 浏览器不发新请求；?v= 变化强制重取（no-cache 头只做 304 廉价校验）。
  const src = frame.local_visual_path
    ? getFrameVisualUrl(taskId, frame.index, thumbVersion)
    : frame.visual_url;
  const dirty =
    narration !== (frame.narration ?? "") ||
    prompt !== (frame.image_prompt ?? "") ||
    source !== (frame.visual_source ?? "");
  // F6（2026-09-28 edits 修复批）：单拆 narration 维度——旁白未保存时「重生配音」
  // 会拿旧旁白配音，用户误以为修改已生效。
  const narrationDirty = narration !== (frame.narration ?? "");
  const warning = (updateMut.data as { warning?: string } | undefined)?.warning;

  // T13（决策 14）：remotion_course 一期专属形态——版式 + 静帧 + 素材区
  // （图槽门控/嵌入图守卫/VideoClipPage AI 视频入口），替换「重生画面」三件套。
  // 放在全部 hooks 之后（rules-of-hooks）。
  if (videoType === "remotion_course") {
    return (
      <RemotionCourseFrameOps
        taskId={taskId}
        frame={frame}
        taskStatus={taskStatus}
        onClose={onClose}
        regenPending={regenPending}
        onRegenStart={onRegenStart}
        onRegenSyncError={onRegenSyncError}
        onVisualChanged={onVisualChanged}
      />
    );
  }

  return (
    <div className="border-border/50 space-y-3 rounded-lg border p-4">
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-semibold">
          帧 {frame.index + 1}
          <span className="text-muted-foreground ml-2 text-xs font-normal">
            {frame.duration?.toFixed(1) ?? "?"}s
          </span>
        </h4>
        <Button
          variant="ghost"
          size="sm"
          className="h-6 w-6 p-0"
          onClick={onClose}
        >
          <X className="h-4 w-4" />
        </Button>
      </div>

      {/* M5: 分镜级「参考资产」标签（本帧注入了：角色A ×1 / 锚图 ×1）——数据源为
          任务详情并入的 sidecar ref_injection（M5 卡：锚图生效可见，B15）。 */}
      {refAssetsLabel(refRecord) && (
        <p
          data-testid="frame-ref-assets"
          className="text-muted-foreground text-xs"
        >
          参考：{refAssetsLabel(refRecord)}
        </p>
      )}

      <div className="flex gap-4">
        <div className="w-48 shrink-0">
          {src ? (
            frame.visual_type === "video" ? (
              <video
                src={src}
                className="w-full rounded-md border"
                muted
                playsInline
                controls
              />
            ) : (
              <img
                loading="lazy"
                src={src}
                alt={`帧 ${frame.index + 1}`}
                className="w-full rounded-md border"
              />
            )
          ) : (
            <div className="bg-muted flex h-24 flex-col items-center justify-center gap-1 rounded-md border border-dashed text-xs">
              <span>无画面或预览已过期</span>
              {/* draft 态「重生画面」是禁用的——指向实际可用的按钮（评审 No.3） */}
              {canRegen ? (
                <span className="text-muted-foreground text-[10px]">
                  点下方「重生画面」重新生成
                </span>
              ) : canUpload ? (
                <span className="text-muted-foreground text-[10px]">
                  点下方「上传替换」替换素材（或到草稿编辑器生成预览图）
                </span>
              ) : null}
            </div>
          )}
          {/*
            2026-08-02 修「上传替换被右侧画面提示词挡住」：
            原为 `flex gap-2` + 三个 flex-1 按钮挤在 w-48（192px）左栏里 ——
            扣掉两个 gap-2（16px）后每个按钮仅约 58px，而 size=sm 的 px-3(24px)
            + 图标 12px + mr-1 4px 已占 40px，留给「上传替换」四个汉字的空间不足，
            按钮溢出容器、压到右栏的画面提示词上。
            改 grid 两列：前两个按钮各占一列，「上传替换」独占整行（col-span-2），
            每个按钮实得约 88px / 192px，中文标签不再溢出。
          */}
          <div className="mt-2 grid grid-cols-2 gap-2">
            <Button
              size="sm"
              variant="outline"
              className="h-7 w-full min-w-0 text-xs"
              disabled={!canRegen || regenPending}
              onClick={() =>
                regenMut.mutate(
                  { frameIndex: frame.index, mode: "visual_only" },
                  {
                    onSuccess: () => updateMut.reset(),
                  },
                )
              }
              title={
                canRegen
                  ? "只重新生成画面（配音/字幕不动）"
                  : "仅 ready/failed 可重生"
              }
            >
              {regenPending ? (
                <Loader2 className="mr-1 h-3 w-3 animate-spin" />
              ) : (
                <RefreshCw className="mr-1 h-3 w-3" />
              )}
              重生画面
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="h-7 w-full min-w-0 text-xs"
              disabled={!canRegen || !!regenPending || narrationDirty}
              onClick={() =>
                regenMut.mutate(
                  { frameIndex: frame.index, mode: "audio_only" },
                  {
                    onSuccess: () => updateMut.reset(),
                  },
                )
              }
              title={
                narrationDirty
                  ? "请先保存旁白修改"
                  : canRegen
                    ? "只重新生成配音（画面不动）——改讲解词后点这个"
                    : "仅 ready/failed 可重生"
              }
            >
              {regenPending ? (
                <Loader2 className="mr-1 h-3 w-3 animate-spin" />
              ) : (
                <Mic className="mr-1 h-3 w-3" />
              )}
              重生配音
            </Button>
            <span
              className="col-span-2 min-w-0"
              title={
                canUpload
                  ? "上传本地图片/视频替换该帧画面"
                  : taskStatus === "failed"
                    ? "failed 状态不支持上传替换（请先重新生成该帧或直接重新合成）"
                    : "当前状态不支持上传替换"
              }
            >
              <Button
                size="sm"
                variant="outline"
                className="h-7 w-full text-xs"
                disabled={!canUpload || uploadMut.isPending}
                onClick={() => uploadRef.current?.click()}
              >
                <Upload className="mr-1 h-3 w-3" />
                上传替换
              </Button>
            </span>
          </div>
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
                {
                  onSuccess: () => {
                    updateMut.reset();
                    onVisualChanged?.();
                  },
                },
              );
              e.target.value = "";
            }}
          />
        </div>

        <div className="flex-1 space-y-2">
          {/* content-match T8②：视觉来源切换（后端 T8⑤ 返回 warning 经下方 warning 位展示） */}
          <VisualSourceSwitcher
            value={source || undefined}
            onChange={(v) => setSource(v)}
            disabled={!editable}
          />
          <p className="text-muted-foreground text-[10px]">
            切换视觉来源后需点「重生画面」出新画面，「重新合成」本身不拾取新来源
          </p>
          <label className="text-muted-foreground block text-xs">
            旁白（解说词）
          </label>
          <textarea
            className="border-border bg-background h-16 w-full rounded-md border p-2 text-sm"
            value={narration}
            disabled={!editable}
            onChange={(e) => setNarration(e.target.value)}
          />
          <label className="text-muted-foreground block text-xs">
            画面提示词
          </label>
          <textarea
            className="border-border bg-background h-16 w-full rounded-md border p-2 text-sm"
            value={prompt}
            disabled={!editable}
            onChange={(e) => setPrompt(e.target.value)}
          />
          {dirty && editable && (
            <Button
              size="sm"
              className="h-7 text-xs"
              disabled={updateMut.isPending}
              onClick={() =>
                updateMut.mutate(
                  {
                    frameIndex: frame.index,
                    updates: {
                      ...(narration !== (frame.narration ?? "")
                        ? { narration }
                        : {}),
                      ...(prompt !== (frame.image_prompt ?? "")
                        ? { image_prompt: prompt }
                        : {}),
                      ...(source !== (frame.visual_source ?? "")
                        ? { visual_source: source }
                        : {}),
                    },
                  },
                  {
                    onSuccess: () =>
                      toast.success(
                        "已保存，需点「重生画面/重生配音」并「重新合成」生效",
                      ),
                  },
                )
              }
            >
              保存修改
            </Button>
          )}
          {warning && (
            <p className="text-xs text-amber-600 dark:text-amber-400">
              {warning}
            </p>
          )}
          {canRegen && (
            <p className="text-muted-foreground text-xs">
              改画面词→保存→「重生画面」；改讲解词→保存→「重生配音」；最后点上方「重新合成」出片。
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
