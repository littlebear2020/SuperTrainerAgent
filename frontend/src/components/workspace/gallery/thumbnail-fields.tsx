"use client";

/**
 * 灵感库缩略图字段（2026-10-06 近重复合并批，单一事实源）。
 * 库内编辑支持换一张、AI 重生成和恢复原图；AI 生成 handler 留在调用方。
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
  /** 无标题时悬浮提示（请先填写标题再生成封面）。 */
  generateDisabledTitle?: string;
  /** 恢复原图的基准（当前值≠基准时显示「恢复原图」）。 */
  originalThumbnail?: string | null;
  /** slate 双主题样式开关。 */
  isDark?: boolean;
}

export function ThumbnailFields({
  thumbnail,
  onThumbnailChange,
  isGenerating,
  onGenerate,
  disabled,
  generateDisabledTitle,
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
