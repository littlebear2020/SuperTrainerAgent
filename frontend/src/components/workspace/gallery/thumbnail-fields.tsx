"use client";

/**
 * 灵感库缩略图字段（2026-10-06 近重复合并批，单一事实源）。
 *
 * 已下线的消息分享弹窗与灵感库编辑表单（gallery/page）原本
 * 各持一份「上传截图 / AI 生成 / 预览 / 覆盖操作」UI 与上传 handler
 * （同端点同文案）。收敛为共享组件，复核确认的分叉以 variant 表达：
 * - share：无图时上传 + AI 生成；有图时可「移除」（分享可选图）
 * - gallery：有图时「换一张 / AI 重生成 / 恢复原图」（编辑态不支持清空，
 *   None 语义=不修改）；无图时上传 + AI 生成；slate 暗色主题样式
 * AI 生成 handler 留在调用方（分享侧有内容感知 contentHint 分叉）。
 */

import { type ChangeEvent } from "react";
import { toast } from "sonner";

import { uploadThumbnail } from "@/core/gallery/api";
import { cn } from "@/lib/utils";

export interface ThumbnailFieldsProps {
  thumbnail: string | null;
  onThumbnailChange: (url: string | null) => void;
  isGenerating: boolean;
  onGenerate: () => void;
  /** 生成按钮 disabled（如提交中）。 */
  disabled?: boolean;
  /** 无标题时悬浮提示（两侧文案一致：请先填写标题再生成封面）。 */
  generateDisabledTitle?: string;
  variant: "share" | "gallery";
  /** gallery：恢复原图的基准（当前值≠基准时显示「恢复原图」）。 */
  originalThumbnail?: string | null;
  /** gallery 的 slate 双主题样式开关。 */
  isDark?: boolean;
}

export function ThumbnailFields({
  thumbnail,
  onThumbnailChange,
  isGenerating,
  onGenerate,
  disabled,
  generateDisabledTitle,
  variant,
  originalThumbnail,
  isDark,
}: ThumbnailFieldsProps) {
  const handleUpload = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const { thumbnail: url } = await uploadThumbnail(file);
      onThumbnailChange(url);
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "缩略图上传失败，请重试",
      );
    } finally {
      e.target.value = "";
    }
  };

  const fileInput = (key: string) => (
    <input
      key={key}
      type="file"
      accept="image/png,image/jpeg,image/gif,image/webp"
      onChange={(e) => void handleUpload(e)}
      className="hidden"
      disabled={disabled}
    />
  );

  const generateButton = (labelWhenIdle: string, key: string) => (
    <button
      key={key}
      type="button"
      onClick={() => onGenerate()}
      disabled={isGenerating || disabled}
      title={generateDisabledTitle}
      className="rounded-lg bg-indigo-500/10 px-3 py-1.5 text-xs font-medium text-indigo-600 hover:bg-indigo-500/20 disabled:opacity-50 dark:text-indigo-400"
    >
      {isGenerating ? "生成中..." : labelWhenIdle}
    </button>
  );

  if (variant === "share") {
    return thumbnail ? (
      <div className="flex items-center gap-3">
        <img
          loading="lazy"
          src={thumbnail}
          alt="缩略图预览"
          className="aspect-video h-16 rounded-md border object-cover"
        />
        <button
          type="button"
          onClick={() => onThumbnailChange(null)}
          className="text-muted-foreground hover:text-foreground text-xs"
        >
          移除
        </button>
      </div>
    ) : (
      <div className="flex items-center gap-2">
        <label className="bg-muted hover:bg-muted/70 cursor-pointer rounded-lg px-3 py-1.5 text-xs font-medium">
          上传截图
          {fileInput("share-upload")}
        </label>
        {generateButton("✨ AI 生成", "share-gen")}
      </div>
    );
  }

  // gallery 变体（slate 双主题）
  const chipClass = cn(
    "cursor-pointer rounded-lg px-3 py-1.5 text-xs font-medium",
    isDark
      ? "bg-white/10 text-slate-200 hover:bg-white/15"
      : "bg-slate-100 text-slate-600 hover:bg-slate-200",
  );
  return (
    <>
      {thumbnail ? (
        <div className="flex items-center gap-3">
          <img
            loading="lazy"
            src={thumbnail}
            alt="缩略图预览"
            className="aspect-video h-16 rounded-md border object-cover"
          />
          <div className="flex gap-2">
            <label className={chipClass}>
              换一张
              {fileInput("gallery-swap")}
            </label>
            {generateButton("✨ AI 重生成", "gallery-gen")}
            {thumbnail !== (originalThumbnail ?? null) && (
              <button
                type="button"
                onClick={() => onThumbnailChange(originalThumbnail ?? null)}
                className="text-muted-foreground hover:text-foreground rounded-lg px-2 py-1.5 text-xs"
              >
                恢复原图
              </button>
            )}
          </div>
        </div>
      ) : (
        <div className="flex items-center gap-2">
          <label className={chipClass}>
            上传截图
            {fileInput("gallery-upload")}
          </label>
          {generateButton("✨ AI 生成", "gallery-gen-idle")}
        </div>
      )}
      <p className="text-muted-foreground mt-1 text-[10px]">
        暂不支持移除缩略图——可上传或生成新图覆盖。
      </p>
    </>
  );
}
