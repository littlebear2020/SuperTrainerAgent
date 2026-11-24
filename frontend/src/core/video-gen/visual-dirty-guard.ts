/**
 * E5（2026-11-24 前端可靠性批，PR1）：帧面板「重生画面」付费前守卫的类型门控。
 *
 * 画面提示词 / 视觉来源改了没保存就点「重生画面」，后端拿旧参数付费重生，用户
 * 以为改完直接出图。守卫只对 AI 生图 / AI 生视频 / ai_visual 三类启用——它们的
 * 重生是直接付费的模型调用；ViMax 与 remotion_course 保持原按钮规则（后者另有
 * 专属帧面板）。纯函数，node 档可测。
 */
const VISUAL_DIRTY_GUARDED_TYPES = new Set([
  "ai_image",
  "ai_video",
  "ai_visual",
]);

export function supportsVisualDirtyGuard(
  videoType: string | undefined,
): boolean {
  return videoType != null && VISUAL_DIRTY_GUARDED_TYPES.has(videoType);
}
