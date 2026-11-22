/** 视频任务的统一目录过滤与退役音色判定；后端仍是提交前的权威门禁。 */

import { isRetiredVoiceId } from "@/core/voice-twin/types";

export interface VoiceOption {
  id: string;
  name: string;
  gender?: string;
  provider?: string;
}

/** 所有任务类型共用同一目录；退役当前值由调用方保留为禁用项。 */
export function filterSupportedVoices<T extends VoiceOption>(voices: T[]): T[] {
  return voices.filter(
    (voice) =>
      !isRetiredVoiceId(voice.id) &&
      (!voice.provider || voice.provider === "doubao"),
  );
}

export function retiredVoiceBlocked(
  voiceId: string | null | undefined,
): boolean {
  return isRetiredVoiceId(voiceId);
}
