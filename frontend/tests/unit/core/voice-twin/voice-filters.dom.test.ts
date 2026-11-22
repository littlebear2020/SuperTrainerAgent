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

type VoiceFixture = Omit<PresetVoice, "provider"> & { provider: string };

function voice(partial: Partial<VoiceFixture> & { id: string }): PresetVoice {
  return {
    name: partial.id,
    gender: "female",
    locale: "zh-CN",
    provider: "doubao",
    description: "",
    scenario: [],
    tone: [],
    ...partial,
  } as PresetVoice;
}

const DOUBAO_IDS = [
  "doubao:zh_female_vv_uranus_bigtts",
  "doubao:zh_female_yingyujiaoxue_uranus_bigtts",
  "doubao:zh_male_m191_uranus_bigtts",
  "doubao:zh_male_liufei_uranus_bigtts",
];

const CATALOG: PresetVoice[] = [
  voice({ id: DOUBAO_IDS[0]!, scenario: ["培训讲解"], tone: ["知性"] }),
  voice({ id: DOUBAO_IDS[1]!, scenario: ["多语种"], tone: ["亲切"] }),
  voice({
    id: DOUBAO_IDS[2]!,
    gender: "male",
    scenario: ["培训讲解"],
    tone: ["沉稳"],
  }),
  voice({
    id: DOUBAO_IDS[3]!,
    gender: "male",
    scenario: ["广告配音"],
    tone: ["磁性"],
  }),
  voice({
    id: "preset:146",
    provider: "qihu",
    scenario: ["培训讲解"],
    tone: ["知性"],
  }),
  voice({
    id: "edge:en-US-AriaNeural",
    provider: "edge-tts",
    locale: "en-US",
    scenario: ["多语种"],
  }),
];

describe("filterCatalogVoices", () => {
  it("空筛选也只返回 4 个字节目录音色", () => {
    expect(
      filterCatalogVoices(CATALOG, EMPTY_VOICE_FILTERS).map((v) => v.id),
    ).toEqual(DOUBAO_IDS);
  });

  it("语言筛选不会复活退役语言条目", () => {
    expect(
      filterCatalogVoices(CATALOG, {
        ...EMPTY_VOICE_FILTERS,
        locales: ["en-US"],
      }),
    ).toEqual([]);
    expect(
      filterCatalogVoices(CATALOG, {
        ...EMPTY_VOICE_FILTERS,
        locales: ["zh-CN"],
      }).map((v) => v.id),
    ).toEqual(DOUBAO_IDS);
  });

  it("按性别匹配", () => {
    const out = filterCatalogVoices(CATALOG, {
      ...EMPTY_VOICE_FILTERS,
      genders: ["male"],
    });
    expect(out.map((v) => v.id)).toEqual([DOUBAO_IDS[2], DOUBAO_IDS[3]]);
  });

  it("scenario 任一命中即保留（多选为 OR）", () => {
    const out = filterCatalogVoices(CATALOG, {
      ...EMPTY_VOICE_FILTERS,
      scenarios: ["培训讲解", "广告配音"],
    });
    expect(out.map((v) => v.id)).toEqual([
      DOUBAO_IDS[0],
      DOUBAO_IDS[2],
      DOUBAO_IDS[3],
    ]);
  });

  it("多维度为 AND 组合", () => {
    const out = filterCatalogVoices(CATALOG, {
      locales: ["zh-CN"],
      genders: ["female"],
      scenarios: ["培训讲解"],
      tones: ["知性"],
    });
    expect(out.map((v) => v.id)).toEqual([DOUBAO_IDS[0]]);
  });
});

describe("groupCatalogVoices", () => {
  it("只生成豆包分组", () => {
    const groups = groupCatalogVoices(CATALOG);
    expect(groups.map((group) => group.provider)).toEqual(["doubao"]);
    expect(groups[0]?.voices.map((v) => v.id)).toEqual(DOUBAO_IDS);
  });

  it("退役组不返回", () => {
    expect(groupCatalogVoices(CATALOG.slice(4))).toEqual([]);
  });
});

describe("collectLocales", () => {
  it("去重且保持出现顺序", () => {
    expect(collectLocales(CATALOG)).toEqual(["zh-CN", "en-US"]);
  });
});

function makeStorage(entries: Record<string, string> = {}): Storage {
  const store = new Map<string, string>(Object.entries(entries));
  return {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => void store.set(key, value),
    removeItem: (key: string) => void store.delete(key),
    clear: () => store.clear(),
    key: (index: number) => [...store.keys()][index] ?? null,
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
