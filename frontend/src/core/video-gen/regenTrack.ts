import type { StoryboardData } from "./types";

/** 单帧重生超时兜底（与预览 PREVIEW_TIMEOUT_MS 对齐）：后台任务随进程重启蒸发时防永远转圈。 */
export const REGEN_TIMEOUT_MS = 300_000;

/**
 * 点击重生的瞬间乐观清掉缓存中该帧旧 regen_state/regen_error——否则释放 effect 用缓存
 * 旧数据判 isRegenResolved 立即 true → spinner 秒停并弹出上一次陈旧错误 toast；
 * 清掉后新结果随 3s 轮询到达再正常结案（与 clearFramePreviewError 同一理由，b30b026f4）。
 */
export function clearFrameRegenState(
  old: StoryboardData | undefined,
  index: number,
): StoryboardData | undefined {
  if (!old?.frames) return old;
  return {
    ...old,
    frames: old.frames.map((f) =>
      f.index === index && (f.regen_state || f.regen_error)
        ? { ...f, regen_state: undefined, regen_error: undefined }
        : f,
    ),
  };
}

/** 释放判定：后端写入终态（done/error）即结案；running/缺失 → 继续等。 */
export function isRegenResolved(
  frame: { regen_state?: string | null } | undefined,
): boolean {
  return frame?.regen_state === "done" || frame?.regen_state === "error";
}

export function isRegenTimedOut(startedAt: number, now: number): boolean {
  return now - startedAt > REGEN_TIMEOUT_MS;
}
