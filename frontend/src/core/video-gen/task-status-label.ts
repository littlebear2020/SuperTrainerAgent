/**
 * Single source of truth for the video task status label string, shared by the
 * list-page + detail-page StatusBadge components so they can never diverge
 * (2026-07-15: the detail page used to lump `draft`/`composing` into "排队中").
 *
 * Mirrors the list page's logic exactly (video-gen-page.tsx StatusBadge draft
 * branch + getActiveStatusLabel). The detail page now calls this too.
 */
export function getVideoTaskStatusLabel(
  status: string,
  progress?: number,
): string {
  switch (status) {
    case "ready":
      return "完成";
    case "failed":
      return "失败";
    case "draft":
      return (progress ?? 0) < 100 ? "生成分镜" : "草稿";
    case "running":
      return "处理中";
    case "composing":
      return "重新合成中";
    // T-C2（R14，2026-09-09 feedback-round1）：qc_repair 前端建模——修前落到
    // default「排队中」（worker 质检检出异常画面、gateway watcher 重生中）
    case "qc_repair":
      return "质检修复中";
    default:
      return "排队中"; // pending + any unknown
  }
}

/**
 * Video task terminal statuses — mirrors the backend's single source of truth
 * `TERMINAL_STATUSES` (backend/app/gateway/_shared/task_framework/schemas.py).
 *
 * 2026-08-19（反馈1-项2）：修前 "ready"||"failed"||"cancelled" 三字面量在
 * video-gen-page.tsx（D5 花费闸）与 video-gen-detail.tsx（详情花费闸）各硬编码
 * 一份——后端新增终态时会静默漏同步，出现列表/详情行为分裂。
 * ⚠️ 后端 TERMINAL_STATUSES 变更时此处必须同步（无编译期联动，靠注释与测试）。
 */
export const VIDEO_TASK_TERMINAL_STATUSES: ReadonlySet<string> = new Set([
  "ready",
  "failed",
  "cancelled",
]);

export function isTerminalVideoTaskStatus(status: string): boolean {
  return VIDEO_TASK_TERMINAL_STATUSES.has(status);
}
