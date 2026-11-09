"use client";

import { Loader2Icon } from "lucide-react";

/**
 * VimaxStageIndicator (3.3 process visualization).
 *
 * Shows the current ViMax render stage + message + per-shot meta, read from the
 * task result JSON written by `_wrap_vimax_progress` (render_stage /
 * render_message / render_meta). Sits under the coarse progress bar in
 * video-gen-detail so a long vimax render shows what it's actually doing
 * ("Generating character portraits", "Decomposing shot visual descriptions",
 * shot_count, etc.) instead of just a stuck "75%".
 *
 * Pure presentational; data comes via props from the task detail poll.
 */
type VimaxStageIndicatorProps = {
  /** task.result object; may contain render_stage / render_message / render_meta */
  result?: Record<string, unknown> | null;
};

// Human-readable Chinese labels for the ViMax stages (ViMax emits english keys).
export const STAGE_LABELS: Record<string, string> = {
  extract_characters: "提取角色",
  design_storyboard: "设计分镜",
  decompose_shots: "拆解镜头",
  construct_camera_tree: "构建运镜",
  render_start: "开始渲染",
  character_portraits: "生成角色肖像",
  character_portraits_loaded: "加载角色肖像",
  camera_image: "生成分镜首帧",
  frame_prompt: "生成镜头提示",
  frame_start: "生成镜头",
  frame_done: "完成镜头",
  concat_start: "拼接视频",
  final_video_exists: "成片就绪",
};

/**
 * Pure helper: pick the human-readable label for a vimax render stage. Exported
 * for unit-testing the mapping without rendering JSX (project convention: pure
 * logic in .test.ts, no JSX in unit tests).
 */
export function resolveStageLabel(
  stage: string | undefined,
  message: string | undefined,
): string {
  if (!stage) return "";
  return STAGE_LABELS[stage] ?? message ?? stage;
}

export function VimaxStageIndicator({ result }: VimaxStageIndicatorProps) {
  const stage = (result?.render_stage as string | undefined) ?? null;
  const message = (result?.render_message as string | undefined) ?? null;
  const meta =
    (result?.render_meta as Record<string, unknown> | undefined) ?? null;

  // No stage info → render nothing (don't clutter non-vimax / pre-stage tasks)
  if (!stage) {
    return null;
  }

  const label = resolveStageLabel(stage ?? undefined, message ?? undefined);
  const metaParts: string[] = [];
  if (meta) {
    for (const [key, value] of Object.entries(meta)) {
      if (value == null) continue;
      // value may be a scalar or a nested dict (ViMax meta); stringify safely
      const valueStr =
        typeof value === "string" ||
        typeof value === "number" ||
        typeof value === "boolean"
          ? String(value)
          : JSON.stringify(value);
      metaParts.push(`${key}: ${valueStr}`);
    }
  }

  return (
    <div className="text-muted-foreground flex items-center gap-2 text-xs">
      <Loader2Icon className="size-3 shrink-0 animate-spin" />
      <span className="truncate">
        {label}
        {metaParts.length > 0 && (
          <span className="text-muted-foreground/70">
            {" "}
            · {metaParts.join(" · ")}
          </span>
        )}
      </span>
    </div>
  );
}
