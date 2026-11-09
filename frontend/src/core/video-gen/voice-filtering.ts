/**
 * Plan B（2026-10-19，设计决策 D4）：ViMax 任务的音色目录过滤与 preset 残留判定。
 *
 * 范围铁律：**仅 ViMax** 不展示/不接受 360 preset 音色；其他视频类型
 * （ai_image/ai_video/remotion_course）与项目编辑接口不受影响。
 * 后端同语义防线：ensure_vimax_voice_allowed（付费前 422）+ shared synthesize
 * reject 分支——前端是第一道（不出现在选项里），后端是权威。
 */

export interface VoiceOption {
  id: string;
  name: string;
  gender?: string;
  provider?: string;
}

/** 360 preset 音色判定（"preset:N" 前缀）。 */
export function isPresetVoice(voiceId: string | null | undefined): boolean {
  return !!voiceId && voiceId.startsWith("preset:");
}

/**
 * 按任务类型过滤音色目录：vimax 剔除 qihu（360）条目；其他类型原样返回。
 * 保留非法当前值（残留 preset）由调用方以禁用 option + 提示呈现，不静默清空。
 */
export function filterVoicesForTaskType<T extends VoiceOption>(
  voices: T[],
  taskType: string | null | undefined,
): T[] {
  if (taskType === "vimax") {
    return voices.filter((v) => v.provider !== "qihu" && !isPresetVoice(v.id));
  }
  return voices;
}

/**
 * ViMax 任务携带 preset 当前值/残留值 → 提交与试听须阻断（后端会 422）。
 * 非 ViMax 或非 preset → false（放行）。
 */
export function vimaxVoiceBlocked(
  taskType: string | null | undefined,
  voiceId: string | null | undefined,
): boolean {
  return taskType === "vimax" && isPresetVoice(voiceId);
}
