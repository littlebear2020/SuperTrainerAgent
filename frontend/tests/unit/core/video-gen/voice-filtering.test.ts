/**
 * Plan B Task 4（2026-10-19）：ViMax 音色目录过滤与 preset 残留判定。
 *
 * 契约（设计 D4）：
 * - filterVoicesForTaskType：vimax 剔除 qihu/preset 条目；ai_image 原样返回。
 * - vimaxVoiceBlocked：vimax + preset → true（提交/试听阻断）；其他组合 false。
 * - isPresetVoice：仅 "preset:N" 前缀命中。
 */
import { describe, expect, it } from "vitest";

import {
  filterVoicesForTaskType,
  isPresetVoice,
  vimaxVoiceBlocked,
} from "@/core/video-gen/voice-filtering";

const VOICES = [
  { id: "preset:146", name: "知性女声", gender: "Female", provider: "qihu" },
  { id: "preset:332", name: "磁性解说男声", gender: "Male", provider: "qihu" },
  {
    id: "doubao:zh_male_A",
    name: "豆包男声",
    gender: "Male",
    provider: "doubao",
  },
  {
    id: "zh-CN-YunjianNeural",
    name: "云健",
    gender: "Male",
    provider: "edge-tts",
  },
];

describe("filterVoicesForTaskType", () => {
  it("vimax 剔除全部 360 preset 条目（doubao/edge 保留）", () => {
    const out = filterVoicesForTaskType(VOICES, "vimax");
    expect(out.map((v) => v.id)).toEqual([
      "doubao:zh_male_A",
      "zh-CN-YunjianNeural",
    ]);
  });

  it("ai_image 原样返回（qihu 仍展示）", () => {
    expect(filterVoicesForTaskType(VOICES, "ai_image")).toEqual(VOICES);
  });

  it("taskType 缺省原样返回", () => {
    expect(filterVoicesForTaskType(VOICES, undefined)).toEqual(VOICES);
  });
});

describe("vimaxVoiceBlocked / isPresetVoice", () => {
  it("vimax + preset 残留 → 阻断", () => {
    expect(vimaxVoiceBlocked("vimax", "preset:332")).toBe(true);
  });

  it("vimax + doubao/edge/默认(null) → 放行", () => {
    expect(vimaxVoiceBlocked("vimax", "doubao:zh_male_A")).toBe(false);
    expect(vimaxVoiceBlocked("vimax", "zh-CN-YunjianNeural")).toBe(false);
    expect(vimaxVoiceBlocked("vimax", null)).toBe(false);
  });

  it("非 ViMax + preset → 放行（其他类型语义不变）", () => {
    expect(vimaxVoiceBlocked("ai_image", "preset:332")).toBe(false);
    expect(vimaxVoiceBlocked("ai_video", "preset:146")).toBe(false);
  });

  it("isPresetVoice 只认 preset: 前缀", () => {
    expect(isPresetVoice("preset:1")).toBe(true);
    expect(isPresetVoice("doubao:preset")).toBe(false);
    expect(isPresetVoice(null)).toBe(false);
    expect(isPresetVoice("")).toBe(false);
  });
});
