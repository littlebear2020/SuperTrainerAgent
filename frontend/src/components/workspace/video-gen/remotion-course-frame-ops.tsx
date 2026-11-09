"use client";

/**
 * remotion_course 详情页帧操作表单（2026-09-10 独立管线计划 T13，决策 14 一期）。
 *
 * 形态 = 版式切换 + 静帧预览 + 素材区（规则与分镜编辑器 5.2 一致：图槽/VideoClipPage
 * 才显示、嵌入/上传帧只提供替换、VideoClipPage 有上传/AI 生成视频入口）+
 * 旁白编辑 + 「重生配音」（audio_only 复用）。替换 AI 管线三件套中的「重生画面」。
 */

import { Loader2, Mic, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  useRegenerateFrame,
  useUpdateFrameLayout,
  useUpdateStoryboardFrame,
} from "@/core/video-gen/hooks";
import {
  hasAssetArea,
  VIDEO_CLIP_LAYOUT,
} from "@/core/video-gen/layout-content-schema";
import type { StoryboardFrameData } from "@/core/video-gen/types";

import { LayoutSwitcher } from "./layout-switcher";
import { RemotionCourseAssetArea } from "./remotion-course-asset-area";
import { StillPreview } from "./still-preview";

export interface RemotionCourseFrameOpsProps {
  taskId: string;
  frame: StoryboardFrameData;
  taskStatus: string;
  onClose: () => void;
  regenPending?: boolean;
  onRegenStart?: (frameIndex: number) => void;
  onRegenSyncError?: (frameIndex: number) => void;
  onVisualChanged?: () => void;
}

export function RemotionCourseFrameOps({
  taskId,
  frame,
  taskStatus,
  onClose,
  regenPending,
  onRegenStart,
  onRegenSyncError,
  onVisualChanged,
}: RemotionCourseFrameOpsProps) {
  const updateMut = useUpdateStoryboardFrame(taskId);
  const updateLayout = useUpdateFrameLayout(taskId);
  const audioRegenMut = useRegenerateFrame(taskId, {
    onMutate: (i) => onRegenStart?.(i),
    onSyncError: (i) => onRegenSyncError?.(i),
  });
  const [narration, setNarration] = useState(frame.narration ?? "");
  const [prompt, setPrompt] = useState(frame.image_prompt ?? "");

  const editable = ["ready", "draft", "failed"].includes(taskStatus);
  const canRegen = taskStatus === "ready" || taskStatus === "failed";
  const canUpload = taskStatus === "ready" || taskStatus === "draft";
  const isVideoSlot = frame.layout === VIDEO_CLIP_LAYOUT;
  const showAsset = hasAssetArea(frame.layout);

  const dirty =
    narration !== (frame.narration ?? "") ||
    (showAsset && prompt !== (frame.image_prompt ?? ""));

  const saveEdits = () => {
    updateMut.mutate(
      {
        frameIndex: frame.index,
        updates: {
          ...(narration !== (frame.narration ?? "") ? { narration } : {}),
          ...(showAsset && prompt !== (frame.image_prompt ?? "")
            ? { image_prompt: prompt }
            : {}),
        },
      },
      {
        onSuccess: () =>
          toast.success("已保存，需点「重生配音」并「重新合成」生效"),
      },
    );
  };

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

      <div className="flex flex-col gap-4 sm:flex-row">
        {/* 左：版式切换 + 静帧预览 + 素材操作 */}
        <div className="w-full shrink-0 space-y-2 sm:w-56">
          <LayoutSwitcher
            value={frame.layout}
            onChange={(layout) =>
              updateLayout.mutate({ frameIndex: frame.index, layout })
            }
            disabled={!editable}
          />
          <StillPreview
            taskId={taskId}
            frameIndex={frame.index}
            layout={frame.layout}
            className="w-full"
          />
          {canRegen && (
            <Button
              size="sm"
              variant="outline"
              className="h-7 w-full text-xs"
              disabled={regenPending}
              onClick={() =>
                audioRegenMut.mutate(
                  { frameIndex: frame.index, mode: "audio_only" },
                  { onSuccess: () => updateMut.reset() },
                )
              }
              title="只重新生成配音（画面不动）——改讲解词后点这个"
            >
              {regenPending ? (
                <Loader2 className="mr-1 h-3 w-3 animate-spin" />
              ) : (
                <Mic className="mr-1 h-3 w-3" />
              )}
              重生配音
            </Button>
          )}
        </div>

        {/* 右：素材区（图槽/VideoClipPage 门控）+ 旁白 */}
        <div className="min-w-0 flex-1 space-y-2">
          {showAsset && (
            <RemotionCourseAssetArea
              taskId={taskId}
              frame={frame}
              canRegenerate={canRegen}
              canUpload={canUpload}
              uploadFullWidth
              showMaterialPreview={false}
              aiEnablePrompt={prompt}
              enableAiCancel={false}
              regenSuccessToast={(isVideo) =>
                isVideo ? "已重新生成视频" : "已重新生成配图"
              }
              onVisualRegenerated={onVisualChanged}
              onUploaded={() => {
                updateMut.reset();
                onVisualChanged?.();
              }}
            />
          )}

          <label className="text-muted-foreground block text-xs">
            旁白（解说词）
          </label>
          <Textarea
            className="h-16 w-full text-sm"
            value={narration}
            disabled={!editable}
            onChange={(e) => setNarration(e.target.value)}
          />
          {showAsset && (
            <>
              <label className="text-muted-foreground block text-xs">
                {isVideoSlot ? "视频描述" : "配图描述"}
              </label>
              <Textarea
                className="h-14 w-full text-sm"
                placeholder={
                  isVideoSlot
                    ? "描述这一段的画面与运镜（生成视频用）"
                    : "描述一张承载本页要点/流程/数据的示意图"
                }
                value={prompt}
                disabled={!editable}
                onChange={(e) => setPrompt(e.target.value)}
              />
            </>
          )}
          {dirty && editable && (
            <Button
              size="sm"
              className="h-7 text-xs"
              disabled={updateMut.isPending}
              onClick={saveEdits}
            >
              保存修改
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
