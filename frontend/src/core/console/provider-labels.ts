/** Shared provider label mapping for console panels. */

import type { Translations } from "@/core/i18n/locales/types";

/** provider raw value → human-readable label (covers nano-banana/nano_banana dual spelling). */
export function providerLabel(
  provider: string,
  c: Translations["console"],
): string {
  const map: Record<string, string> = {
    "gpt-image-2": c.providerGptImage2,
    seedream: c.providerSeedream,
    "seedance-ark": c.providerSeedanceArk,
    seedance: c.providerSeedance,
    qihu: c.providerQihu,
    "nano-banana": c.providerNanoBanana,
    nano_banana: c.providerNanoBanana,
    happyhorse: c.providerHappyhorse,
    dashscope: c.providerDashscope,
    hailuo: c.providerHailuo,
    doubao_tts: c.providerDoubaoTts,
    qihu_tts: c.providerQihuTts,
    volcengine: c.providerVolcengine,
    ai_image: c.providerAiImage,
    ai_video: c.providerAiVideo,
  };
  return map[provider] ?? provider;
}
