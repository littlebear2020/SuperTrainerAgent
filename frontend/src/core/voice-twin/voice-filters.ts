/**
 * VoiceSelector 筛选/分组纯逻辑（A4，2026-08-09）。
 *
 * 抽成纯函数便于单测；筛选状态持久化 localStorage（本模块无深链需求，
 * 比 URL query 简单）。克隆音色组不经过这里的筛选（始终全量显示）。
 */

import type { PresetVoice, VoiceProvider } from "./types";

/** 筛选状态。空数组 = 该维度不限制。 */
export interface VoiceFilterState {
  locales: string[];
  genders: ("male" | "female")[];
  scenarios: string[];
  tones: string[];
}

export const EMPTY_VOICE_FILTERS: VoiceFilterState = {
  locales: [],
  genders: [],
  scenarios: [],
  tones: [],
};

const STORAGE_KEY = "voice-twin:selector-filters";

function asStringArray(v: unknown): string[] {
  return Array.isArray(v)
    ? v.filter((x): x is string => typeof x === "string")
    : [];
}

function defaultStorage(): Storage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

/** 读取持久化筛选；损坏/缺字段一律回退空筛选（不抛）。
 *  storage 可注入（测试用 Map stub；生产默认 window.localStorage）。 */
export function loadVoiceFilters(storage?: Storage): VoiceFilterState {
  const store = storage ?? defaultStorage();
  if (!store) return EMPTY_VOICE_FILTERS;
  try {
    const raw = store.getItem(STORAGE_KEY);
    if (!raw) return EMPTY_VOICE_FILTERS;
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null)
      return EMPTY_VOICE_FILTERS;
    const obj = parsed as Record<string, unknown>;
    return {
      locales: asStringArray(obj.locales),
      genders: asStringArray(obj.genders).filter(
        (g): g is "male" | "female" => g === "male" || g === "female",
      ),
      scenarios: asStringArray(obj.scenarios),
      tones: asStringArray(obj.tones),
    };
  } catch {
    return EMPTY_VOICE_FILTERS;
  }
}

export function saveVoiceFilters(
  filters: VoiceFilterState,
  storage?: Storage,
): void {
  const store = storage ?? defaultStorage();
  if (!store) return;
  try {
    store.setItem(STORAGE_KEY, JSON.stringify(filters));
  } catch {
    // 隐私模式/quota 满等场景静默放弃持久化，不影响当次使用
  }
}

/** 多维筛选：locale/性别为精确匹配；scenario/tone 为「任一标签命中」。 */
export function filterCatalogVoices(
  voices: PresetVoice[],
  filters: VoiceFilterState,
): PresetVoice[] {
  return voices.filter(
    (v) =>
      (filters.locales.length === 0 || filters.locales.includes(v.locale)) &&
      (filters.genders.length === 0 || filters.genders.includes(v.gender)) &&
      (filters.scenarios.length === 0 ||
        v.scenario.some((s) => filters.scenarios.includes(s))) &&
      (filters.tones.length === 0 ||
        v.tone.some((t) => filters.tones.includes(t))),
  );
}

export interface VoiceGroup {
  provider: VoiceProvider;
  voices: PresetVoice[];
}

/** 按 provider 分组，固定顺序：edge-tts（免费）→ 360 → 豆包。空组不返回。 */
export function groupCatalogVoices(voices: PresetVoice[]): VoiceGroup[] {
  const order: VoiceProvider[] = ["edge-tts", "qihu", "doubao"];
  return order
    .map((provider) => ({
      provider,
      voices: voices.filter((v) => v.provider === provider),
    }))
    .filter((g) => g.voices.length > 0);
}

/** 从数据生成语言选项（去重、按出现顺序）。 */
export function collectLocales(voices: PresetVoice[]): string[] {
  const seen: string[] = [];
  for (const v of voices) {
    if (!seen.includes(v.locale)) seen.push(v.locale);
  }
  return seen;
}

/** 切换多选数组成员。 */
export function toggleInList<T>(list: T[], item: T): T[] {
  return list.includes(item) ? list.filter((x) => x !== item) : [...list, item];
}
