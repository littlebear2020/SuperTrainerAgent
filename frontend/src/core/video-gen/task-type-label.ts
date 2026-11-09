/**
 * T-E5（R19，2026-09-09 feedback-round1）：任务类型显示名单一事实源。
 *
 * 修前列表卡片（page）/创建表单（TYPE_CONFIG）/详情页三处各硬编码一份，
 * 后端新增类型时静默漏同步——remotion_course 落到「Remotion」/「未知」
 * （与 chat-handoff 存量混称）。此表与后端 task_type 枚举对齐；未知值原样
 * 透出（运维可辨new类型）。
 *
 * D1（T-F1）：合并后入口 Tab 名「文字动效」= remotion_course；存量
 * chat-handoff 任务（remotion）标「文字动效（旧）」只读兼容。
 */
export const VIDEO_TASK_TYPE_LABELS: Record<string, string> = {
  ai_image: "AI 生图",
  ai_visual: "AI 生图",
  ai_video: "AI 生视频",
  vimax: "ViMax 故事",
  remotion: "文字动效（旧）",
  remotion_course: "文字动效",
};

export function videoTaskTypeLabel(
  taskType: string | null | undefined,
): string {
  if (!taskType) return "未知";
  return VIDEO_TASK_TYPE_LABELS[taskType] ?? taskType;
}
