import { describe, expect, it } from "vitest";

import {
  filterSupportedVoices,
  retiredVoiceBlocked,
} from "@/core/video-gen/voice-filtering";

const DOUBAO_VOICES = [
  {
    id: "doubao:zh_female_vv_uranus_bigtts",
    name: "Vivi2.0",
    gender: "Female",
    provider: "doubao",
  },
  {
    id: "doubao:zh_female_yingyujiaoxue_uranus_bigtts",
    name: "Tina2.0",
    gender: "Female",
    provider: "doubao",
  },
  {
    id: "doubao:zh_male_m191_uranus_bigtts",
    name: "云舟2.0",
    gender: "Male",
    provider: "doubao",
  },
  {
    id: "doubao:zh_male_liufei_uranus_bigtts",
    name: "刘飞2.0",
    gender: "Male",
    provider: "doubao",
  },
];

const VOICES = [
  {
    id: "qihu:custom",
    name: "未识别旧渠道",
    gender: "Female",
    provider: "qihu",
  },
  { id: "preset:146", name: "旧 360 女声", gender: "Female", provider: "qihu" },
  {
    id: "edge:zh-CN-XiaoxiaoNeural",
    name: "旧 Edge 女声",
    provider: "edge-tts",
  },
  {
    id: "zh-CN-YunjianNeural",
    name: "旧裸 Edge 男声",
    gender: "Male",
    provider: "edge-tts",
  },
  ...DOUBAO_VOICES,
];

describe("filterSupportedVoices", () => {
  it("所有视频任务共用时都只保留 4 个字节目录音色", () => {
    const out = filterSupportedVoices(VOICES);
    expect(out.map((v) => v.id)).toEqual(DOUBAO_VOICES.map((v) => v.id));
  });
});

describe("退役音色提交阻断", () => {
  it("所有任务类型的 preset/Edge 残留均阻断", () => {
    expect(retiredVoiceBlocked("preset:332")).toBe(true);
    expect(retiredVoiceBlocked("edge:zh-CN-XiaoxiaoNeural")).toBe(true);
    expect(retiredVoiceBlocked("zh-CN-YunjianNeural")).toBe(true);
  });

  it("字节、克隆与默认空值放行", () => {
    expect(retiredVoiceBlocked(DOUBAO_VOICES[0]!.id)).toBe(false);
    expect(retiredVoiceBlocked("clone:user-voice")).toBe(false);
    expect(retiredVoiceBlocked(null)).toBe(false);
    expect(retiredVoiceBlocked("")).toBe(false);
  });
});
