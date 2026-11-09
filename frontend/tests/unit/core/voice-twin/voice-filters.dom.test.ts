/**
 * Tests for voice-twin selector filter/group pure logic (A4, 2026-08-09).
 */

import { describe, expect, it } from "vitest";

import type { PresetVoice } from "@/core/voice-twin/types";
import {
  collectLocales,
  EMPTY_VOICE_FILTERS,
  filterCatalogVoices,
  groupCatalogVoices,
  loadVoiceFilters,
  saveVoiceFilters,
  toggleInList,
} from "@/core/voice-twin/voice-filters";

function voice(partial: Partial<PresetVoice> & { id: string }): PresetVoice {
  return {
    name: partial.id,
    gender: "female",
    locale: "zh-CN",
    provider: "edge-tts",
    description: "",
    scenario: [],
    tone: [],
    ...partial,
  };
}

const CATALOG: PresetVoice[] = [
  voice({
    id: "edge:zh-CN-XiaoxiaoNeural",
    provider: "edge-tts",
    scenario: ["日常对话"],
    tone: ["亲切"],
  }),
  voice({
    id: "edge:zh-CN-YunjianNeural",
    provider: "edge-tts",
    gender: "male",
    scenario: ["培训讲解"],
    tone: ["沉稳"],
  }),
  voice({
    id: "preset:146",
    provider: "qihu",
    scenario: ["培训讲解", "有声书"],
    tone: ["知性"],
  }),
  voice({
    id: "preset:295",
    provider: "qihu",
    gender: "male",
    scenario: ["广告配音"],
    tone: ["磁性"],
  }),
  voice({
    id: "doubao:zh_female_vv_uranus_bigtts",
    provider: "doubao",
    scenario: ["培训讲解"],
    tone: ["知性"],
  }),
  voice({
    id: "edge:en-US-AriaNeural",
    provider: "edge-tts",
    locale: "en-US",
    scenario: ["多语种"],
    tone: ["亲切"],
  }),
];

describe("filterCatalogVoices", () => {
  it("空筛选返回全部", () => {
    expect(filterCatalogVoices(CATALOG, EMPTY_VOICE_FILTERS)).toHaveLength(6);
  });

  it("按语言精确匹配", () => {
    const out = filterCatalogVoices(CATALOG, {
      ...EMPTY_VOICE_FILTERS,
      locales: ["en-US"],
    });
    expect(out.map((v) => v.id)).toEqual(["edge:en-US-AriaNeural"]);
  });

  it("按性别匹配", () => {
    const out = filterCatalogVoices(CATALOG, {
      ...EMPTY_VOICE_FILTERS,
      genders: ["male"],
    });
    expect(out.every((v) => v.gender === "male")).toBe(true);
    expect(out).toHaveLength(2);
  });

  it("scenario 任一命中即保留（多选为 OR）", () => {
    const out = filterCatalogVoices(CATALOG, {
      ...EMPTY_VOICE_FILTERS,
      scenarios: ["培训讲解", "广告配音"],
    });
    expect(out.map((v) => v.id)).toEqual([
      "edge:zh-CN-YunjianNeural",
      "preset:146",
      "preset:295",
      "doubao:zh_female_vv_uranus_bigtts",
    ]);
  });

  it("多维度为 AND 组合", () => {
    const out = filterCatalogVoices(CATALOG, {
      locales: ["zh-CN"],
      genders: ["female"],
      scenarios: ["培训讲解"],
      tones: ["知性"],
    });
    expect(out.map((v) => v.id)).toEqual([
      "preset:146",
      "doubao:zh_female_vv_uranus_bigtts",
    ]);
  });
});

describe("groupCatalogVoices", () => {
  it("按 provider 分组且顺序固定 edge→qihu→doubao", () => {
    const groups = groupCatalogVoices(CATALOG);
    expect(groups.map((g) => g.provider)).toEqual([
      "edge-tts",
      "qihu",
      "doubao",
    ]);
    expect(groups[0]?.voices).toHaveLength(3);
    expect(groups[1]?.voices).toHaveLength(2);
    expect(groups[2]?.voices).toHaveLength(1);
  });

  it("空组不返回", () => {
    const groups = groupCatalogVoices(
      CATALOG.filter((v) => v.provider === "qihu"),
    );
    expect(groups.map((g) => g.provider)).toEqual(["qihu"]);
  });
});

describe("collectLocales", () => {
  it("去重且保持出现顺序", () => {
    expect(collectLocales(CATALOG)).toEqual(["zh-CN", "en-US"]);
  });
});

/** Minimal Storage stub backed by a Map（对齐 tests/unit/lib 既有惯例）。 */
function makeStorage(entries: Record<string, string> = {}): Storage {
  const store = new Map<string, string>(Object.entries(entries));
  return {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
    clear: () => store.clear(),
    key: (i: number) => [...store.keys()][i] ?? null,
    get length() {
      return store.size;
    },
  } as Storage;
}

describe("localStorage 持久化", () => {
  it("save→load 往返一致", () => {
    const storage = makeStorage();
    const filters = {
      locales: ["zh-CN"],
      genders: ["female" as const],
      scenarios: ["培训讲解"],
      tones: [],
    };
    saveVoiceFilters(filters, storage);
    expect(loadVoiceFilters(storage)).toEqual(filters);
  });

  it("损坏 JSON 回退空筛选（不抛）", () => {
    const storage = makeStorage({ "voice-twin:selector-filters": "{broken" });
    expect(loadVoiceFilters(storage)).toEqual(EMPTY_VOICE_FILTERS);
  });

  it("非法字段类型被过滤", () => {
    const storage = makeStorage({
      "voice-twin:selector-filters": JSON.stringify({
        locales: "zh-CN",
        genders: ["male", "x"],
        tones: 42,
      }),
    });
    expect(loadVoiceFilters(storage)).toEqual({
      locales: [],
      genders: ["male"],
      scenarios: [],
      tones: [],
    });
  });
});

describe("toggleInList", () => {
  it("切换成员", () => {
    expect(toggleInList(["a"], "b")).toEqual(["a", "b"]);
    expect(toggleInList(["a", "b"], "a")).toEqual(["b"]);
  });
});
