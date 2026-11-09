"use client";

/**
 * remotion_course 帧素材区（2026-10-06 近重复合并批，单一事实源）。
 *
 * 分镜编辑器（remotion-course-storyboard 的 AssetArea）与详情页
 * （remotion-course-frame-ops）原本各持一份 ~200 行近似实现，规则口径
 * （B2 前缀判定 / m10 isAiMaterial / 重生门控）靠注释互相「对齐」，已三处分叉。
 * 本组件收敛两者，复核确认的差异用 prop 表达：
 * - showMaterialPreview：分镜页内嵌预览；详情页 false（左侧已有 StillPreview）
 * - aiEnablePrompt：详情页模式——需先填描述，且随 visual_source 一并提交
 * - enableAiCancel：分镜页提供「取消 AI 生成」（visual_source 置空）；详情页不提供
 * - canUpload：详情页 ready/draft 才可上传；分镜页恒 true
 * - regenSuccessToast / onVisualRegenerated / onUploaded：文案与刷新回调
 *
 * 关键修复（合并动机）：重生统一走 useRegenerateFrame（自带
 * invalidateQueries + 同步错误 toast）——修前详情页直调 api.regenerateFrame
 * 无失效逻辑，重生完成后页面不自动刷新。
 */

import { ImageIcon, Loader2, RefreshCw, Upload, VideoIcon } from "lucide-react";
import { useRef, type ReactNode } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  useRegenerateFrame,
  useUpdateStoryboardFrame,
  useUploadFrameVisual,
} from "@/core/video-gen/hooks";
import {
  hasAssetArea,
  isAiMaterialFrame,
  isEmbeddedOrUploadedPath,
  VIDEO_CLIP_LAYOUT,
} from "@/core/video-gen/layout-content-schema";
import type { StoryboardFrameData } from "@/core/video-gen/types";
import { cn } from "@/lib/utils";

export interface RemotionCourseAssetAreaProps {
  taskId: string;
  frame: StoryboardFrameData;
  /** ready/failed 态才有「重新生成」（后端 _regenerate_frame 状态守卫）。 */
  canRegenerate: boolean;
  /** 上传入口门控（详情页 ready/draft；分镜页默认 true）。 */
  canUpload?: boolean;
  /** 详情页形态（评估回馈#6）：上传按钮整行宽 + 悬浮说明——修前合并丢了 w-full 与 tooltip。 */
  uploadFullWidth?: boolean;
  /** 素材预览块（默认 true；详情页 false——左侧已有 StillPreview）。 */
  showMaterialPreview?: boolean;
  /** 详情页模式（传入即启用）：AI 开启需先填描述（空则禁用），并随 visual_source 一并提交；
   *  显示门随之派生为 non-embedded（分镜页缺省=no-material）——评估回馈#3：原 aiEnableGate
   *  独立 prop 从未被组件体读取，已删，派生口径落注释。 */
  aiEnablePrompt?: string;
  /** 「取消 AI 生成」入口（分镜页 true；详情页不提供）。 */
  enableAiCancel?: boolean;
  /** 重生成功 toast（默认「已重新生成，完成后自动刷新」）。 */
  regenSuccessToast?: (isVideoSlot: boolean) => string;
  /** 重生成功回调（详情页 onVisualChanged）。 */
  onVisualRegenerated?: () => void;
  /** 上传成功回调（详情页 onVisualChanged）。 */
  onUploaded?: () => void;
  /** 预览与操作排之间插入的内容（分镜页的 PromptField）。 */
  children?: ReactNode;
}

export function RemotionCourseAssetArea({
  taskId,
  frame,
  canRegenerate,
  canUpload = true,
  uploadFullWidth = false,
  showMaterialPreview = true,
  aiEnablePrompt,
  enableAiCancel = true,
  regenSuccessToast,
  onVisualRegenerated,
  onUploaded,
  children,
}: RemotionCourseAssetAreaProps) {
  const updateFrame = useUpdateStoryboardFrame(taskId);
  const uploadVisual = useUploadFrameVisual(taskId);
  // 重生统一走 useRegenerateFrame——直调 API 无失效逻辑，重生完成不自动刷新
  // （ready/failed 态 storyboard 不轮询）；hook 自带 invalidateQueries + 同步错误 toast。
  const regenMut = useRegenerateFrame(taskId);
  const fileRef = useRef<HTMLInputElement | null>(null);

  if (!hasAssetArea(frame.layout)) return null;
  const isVideoSlot = frame.layout === VIDEO_CLIP_LAYOUT;
  const hasMaterial = !!frame.local_visual_path || !!frame.visual_url;
  // B2（2026-09-11 审查）：按路径前缀区分——AI 本地产物（visuals/img_、shot_）不是
  // 用户素材，修前 `!!local_visual_path` 把首轮渲染后的 AI 帧全判成嵌入/上传，
  // 「重新生成」入口永久消失（后端 P6 守卫只拒 embedded/ 与 frame_ 前缀）。
  const isEmbeddedOrUploaded = isEmbeddedOrUploadedPath(
    frame.local_visual_path,
  );
  // m10：两侧共用同一判定（isAiMaterialFrame）。
  const isAiMaterial = isAiMaterialFrame(frame, isVideoSlot);
  const materialUrl = frame.local_visual_path
    ? `/api/video/tasks/${taskId}/frames/${frame.index}/visual`
    : (frame.visual_url ?? null);

  const doRegenerate = () => {
    regenMut.mutate(
      { frameIndex: frame.index, mode: "visual_only" },
      {
        onSuccess: () => {
          toast.success(
            regenSuccessToast?.(isVideoSlot) ?? "已重新生成，完成后自动刷新",
          );
          onVisualRegenerated?.();
        },
      },
    );
  };

  // AI 开启：详情页模式描述随 visual_source 一并提交；两种模式都按各自描述源门控
  const detailAiMode = aiEnablePrompt !== undefined;
  const showAiEnable = detailAiMode
    ? !isEmbeddedOrUploaded && !isAiMaterial && canUpload
    : !hasMaterial && !isAiMaterial;
  const aiEnableDisabled = detailAiMode
    ? !aiEnablePrompt.trim()
    : !frame.image_prompt?.trim();
  const aiEnableTitle = aiEnableDisabled
    ? detailAiMode
      ? "请先填写素材描述"
      : "请先填写配图描述"
    : undefined;

  const accept = isVideoSlot
    ? "video/mp4,video/webm"
    : "image/jpeg,image/png,image/webp";

  return (
    <div className="bg-muted/20 space-y-2 rounded-md border p-3">
      <div className="flex items-center gap-2 text-xs font-medium">
        {isVideoSlot ? (
          <VideoIcon className="h-3.5 w-3.5" />
        ) : (
          <ImageIcon className="h-3.5 w-3.5" />
        )}
        {isVideoSlot ? "视频素材" : "配图素材"}
      </div>

      {showMaterialPreview &&
        (materialUrl ? (
          <div className="space-y-2">
            {isVideoSlot && frame.visual_type === "video" ? (
              <video
                src={materialUrl}
                controls
                className="w-full rounded border"
              />
            ) : (
              <img
                loading="lazy"
                src={materialUrl}
                alt={`帧 ${frame.index} 素材`}
                className="w-full rounded border object-contain"
              />
            )}
            {isEmbeddedOrUploaded && (
              <p className="text-muted-foreground text-[11px]">
                嵌入/上传素材：不支持「重新生成」，可替换。
              </p>
            )}
            {frame.image_note && (
              <p className="text-muted-foreground text-[11px]">
                📎 {frame.image_note}
              </p>
            )}
          </div>
        ) : (
          <div className="space-y-2">
            <p className="text-muted-foreground rounded border border-dashed p-3 text-center text-xs">
              {isVideoSlot
                ? "当前无视频，成片将渲染播放键占位"
                : "当前无配图，成片将渲染占位画面"}
            </p>
            {frame.image_note && (
              <p className="text-muted-foreground text-[11px]">
                📎 {frame.image_note}
              </p>
            )}
          </div>
        ))}

      {!showMaterialPreview && isEmbeddedOrUploaded && (
        <p className="text-muted-foreground text-[11px]">
          嵌入/上传素材：不支持「重新生成」，可替换。
        </p>
      )}
      {!showMaterialPreview && frame.image_note && (
        <p className="text-muted-foreground text-[11px]">
          📎 {frame.image_note}
        </p>
      )}

      {children}

      <div className="flex flex-wrap items-center gap-2">
        {/* 嵌入/上传帧：只提供替换，不提供重新生成（P6 守卫对齐） */}
        {isAiMaterial && canRegenerate && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-7 text-xs"
            disabled={regenMut.isPending}
            onClick={doRegenerate}
          >
            {regenMut.isPending ? (
              <Loader2 className="mr-1 h-3 w-3 animate-spin" />
            ) : (
              <RefreshCw className="mr-1 h-3 w-3" />
            )}
            {isVideoSlot ? "重新生成视频" : "重新生成配图"}
          </Button>
        )}
        {showAiEnable && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-7 text-xs"
            disabled={aiEnableDisabled}
            title={aiEnableTitle}
            onClick={() => {
              updateFrame.mutate(
                {
                  frameIndex: frame.index,
                  updates: {
                    ...(detailAiMode ? { image_prompt: aiEnablePrompt } : {}),
                    visual_source: isVideoSlot ? "ai_video" : "ai_image",
                  },
                },
                {
                  onSuccess: () =>
                    toast.success(
                      isVideoSlot
                        ? `已开启 AI 生成视频，${detailAiMode ? "重新合成后生成" : "渲染时生成"}`
                        : `已开启 AI 生成配图，${detailAiMode ? "重新合成后生成" : "渲染时生成"}`,
                    ),
                },
              );
            }}
          >
            {isVideoSlot
              ? "AI 生成视频"
              : detailAiMode
                ? "AI 生成配图"
                : "生成配图"}
          </Button>
        )}
        {enableAiCancel && !hasMaterial && isAiMaterial && (
          <span className="text-muted-foreground text-xs">
            已开启 AI 生成（渲染时生成）
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="ml-1 h-6 px-2 text-xs"
              onClick={() =>
                updateFrame.mutate({
                  frameIndex: frame.index,
                  updates: { visual_source: null },
                })
              }
            >
              取消
            </Button>
          </span>
        )}
        {canUpload && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            className={cn("h-7 text-xs", uploadFullWidth && "w-full")}
            title={
              uploadFullWidth ? "上传本地图片/视频替换该帧素材" : undefined
            }
            disabled={uploadVisual.isPending}
            onClick={() => fileRef.current?.click()}
          >
            <Upload className="mr-1 h-3 w-3" />
            {hasMaterial ? "替换" : isVideoSlot ? "上传视频" : "上传图片"}
          </Button>
        )}
        <input
          ref={fileRef}
          type="file"
          accept={accept}
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f)
              uploadVisual.mutate(
                { frameIndex: frame.index, file: f },
                { onSuccess: () => onUploaded?.() },
              );
            e.target.value = "";
          }}
        />
        {uploadVisual.isPending && <Loader2 className="h-3 w-3 animate-spin" />}
      </div>
    </div>
  );
}
