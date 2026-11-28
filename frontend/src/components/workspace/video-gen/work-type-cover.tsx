"use client";

import {
  AudioLines,
  Clapperboard,
  FileWarning,
  Film,
  Image as ImageIcon,
  Palette,
  Presentation,
  type LucideIcon,
} from "lucide-react";

import { useI18n } from "@/core/i18n/hooks";
import type { WorkFile, WorkMediaKind } from "@/core/works/types";
import type { WorkCapability } from "@/core/workspace/video-assets-route";
import { cn } from "@/lib/utils";

export type WorkCoverState = "default" | "empty" | "missing" | "unknown";

interface CoverAppearance {
  icon: LucideIcon;
  /** 根元素渐变（静态类名，禁止运行时拼接 `bg-${color}-*`）。 */
  surface: string;
  medallion: string;
}

/**
 * 图标/配色复用 `core/agents/appearance.ts` 的口径；`ai_image` / `ai_video`
 * 两个任务能力不在该表里，这里给静态映射。
 */
const COVER_APPEARANCE: Record<WorkCapability, CoverAppearance> = {
  "course-video": {
    icon: Clapperboard,
    surface: "from-indigo-500/15 via-transparent to-indigo-500/5",
    medallion:
      "bg-indigo-100 text-indigo-600 dark:bg-indigo-500/10 dark:text-indigo-400",
  },
  ai_image: {
    icon: ImageIcon,
    surface: "from-sky-500/15 via-transparent to-sky-500/5",
    medallion: "bg-sky-100 text-sky-600 dark:bg-sky-500/10 dark:text-sky-400",
  },
  ai_video: {
    icon: Film,
    surface: "from-rose-500/15 via-transparent to-rose-500/5",
    medallion:
      "bg-rose-100 text-rose-600 dark:bg-rose-500/10 dark:text-rose-400",
  },
  "creative-studio": {
    icon: Palette,
    surface: "from-fuchsia-500/15 via-transparent to-fuchsia-500/5",
    medallion:
      "bg-fuchsia-100 text-fuchsia-600 dark:bg-fuchsia-500/10 dark:text-fuchsia-400",
  },
  "ppt-master": {
    icon: Presentation,
    surface: "from-teal-500/15 via-transparent to-teal-500/5",
    medallion:
      "bg-teal-100 text-teal-600 dark:bg-teal-500/10 dark:text-teal-400",
  },
  "podcast-studio": {
    icon: AudioLines,
    surface: "from-cyan-500/15 via-transparent to-cyan-500/5",
    medallion:
      "bg-cyan-100 text-cyan-700 dark:bg-cyan-500/10 dark:text-cyan-300",
  },
};

const NEUTRAL_MEDALLION = "bg-muted text-muted-foreground";
const MISSING_MEDALLION =
  "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-400";

/** 扩展名：1–8 位字母数字；以点开头的隐藏文件（`.env`）不算扩展名。 */
const FILE_EXTENSION = /\.([A-Za-z0-9]{1,8})$/;

/** 格式徽标：有文件名取扩展名（MP4/PPTX/HTML…），没有文件名按 mediaKind 给通用标签。 */
function coverFormatLabel(
  file: WorkFile | null,
  labels: Record<WorkMediaKind, string>,
): string | null {
  const name = file?.name ?? "";
  const extension = name.includes(".", 1)
    ? FILE_EXTENSION.exec(name)?.[1]
    : undefined;
  if (extension) return extension.toUpperCase();
  if (!file) return null;
  return labels[file.mediaKind];
}

function medallionFor(state: WorkCoverState, fallback: string): string {
  if (state === "missing") return MISSING_MEDALLION;
  if (state === "default") return fallback;
  return NEUTRAL_MEDALLION;
}

/**
 * 统一的作品类型封面：卡片与查看器共用。
 *
 * 只用 CSS 与内联 SVG，不渲染 `<img>` 或任何 `src`——列表零请求的门禁不变。
 * 状态不能只靠颜色区分：missing/empty/unknown 各带明确的状态文字。
 */
export function WorkTypeCover({
  capability,
  file,
  state,
  className,
}: {
  capability: WorkCapability;
  file: WorkFile | null;
  state: WorkCoverState;
  className?: string;
}) {
  const { t } = useI18n();
  const appearance = COVER_APPEARANCE[capability];
  const Icon = state === "missing" ? FileWarning : appearance.icon;
  const medallion = medallionFor(state, appearance.medallion);
  let statusText: string | null = null;
  if (state === "missing") statusText = t.workspace.works.fileMissing;
  else if (state === "empty") statusText = t.workspace.works.emptyFiles;
  else if (state === "unknown")
    statusText = t.workspace.works.fileInfoUnavailable;
  const format = coverFormatLabel(file, {
    video: t.workspace.works.coverVideo,
    audio: t.workspace.works.coverAudio,
    document: t.workspace.works.coverDocument,
    image: t.workspace.works.coverImage,
    other: t.workspace.works.coverOther,
  });

  return (
    <div
      data-testid="work-type-cover"
      data-cover-state={state}
      className={cn(
        "bg-muted ring-border/70 relative flex h-full w-full items-center justify-center overflow-hidden bg-gradient-to-br text-center ring-1 ring-inset",
        state === "default" && appearance.surface,
        className,
      )}
    >
      <div className="flex min-w-0 flex-col items-center gap-2 px-4">
        <span
          className={cn(
            "flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl shadow-sm",
            medallion,
          )}
        >
          <Icon className="h-6 w-6" aria-hidden="true" />
        </span>
        <span className="text-foreground max-w-full truncate text-sm leading-tight font-semibold">
          {t.workspace.home.courseItems[capability].title}
        </span>
        {format ? (
          <span
            data-cover-format
            className="border-border bg-background/85 text-foreground/80 rounded-full border px-2 py-0.5 text-[10px] font-semibold tracking-wider"
          >
            {format}
          </span>
        ) : null}
        {statusText ? (
          <span
            className={
              state === "missing"
                ? "text-xs text-amber-700 dark:text-amber-400"
                : "text-muted-foreground text-xs"
            }
          >
            {statusText}
          </span>
        ) : null}
      </div>
    </div>
  );
}
