import { describe, expect, it } from "vitest";

import {
  buildCreatePayload,
  isTargetDurationVisible,
  isValidTargetDuration,
  resolveTargetDurationForPayload,
  resolveSecondsPerFrameForPayload,
  type CreatePayloadInput,
} from "@/components/workspace/video-gen/create-payload";

function base(overrides: Partial<CreatePayloadInput> = {}): CreatePayloadInput {
  return {
    taskType: "vimax",
    mode: "fixed",
    script: "剧本",
    topic: "",
    lang: "zh",
    aspect: "9:16",
    style: "default",
    subtitleStyle: "default",
    bgm: null,
    voiceId: null,
    outputFormat: "mp4",
    quality: "standard",
    ttsSpeed: null,
    bgmVolume: 0.15,
    bgmDucking: true,
    ...overrides,
  };
}

describe("buildCreatePayload title / vimax fixed target duration", () => {
  it("sends trimmed title and target_duration for vimax fixed", () => {
    const payload = buildCreatePayload(
      base({ title: "  [AUTO-TEST] 刻舟求剑  ", targetDuration: 60 }),
    );
    expect(payload.title).toBe("[AUTO-TEST] 刻舟求剑");
    expect(payload.target_duration).toBe(60);
  });

  it("omits title entirely when blank (request shape unchanged)", () => {
    const payload = buildCreatePayload(base({ title: "   " }));
    expect("title" in payload).toBe(false);
  });

  it("truncates overlong title to 80 chars (backend contract)", () => {
    const payload = buildCreatePayload(base({ title: "A".repeat(100) }));
    expect(payload.title).toHaveLength(80);
  });

  it("keeps target_duration undefined when left empty", () => {
    const payload = buildCreatePayload(base({ targetDuration: undefined }));
    expect(payload.target_duration).toBeUndefined();
  });
});

describe("resolveTargetDurationForPayload", () => {
  it("keeps value for generate and vimax fixed only", () => {
    expect(resolveTargetDurationForPayload("ai_image", "generate", 60)).toBe(
      60,
    );
    expect(resolveTargetDurationForPayload("vimax", "fixed", 60)).toBe(60);
    expect(resolveTargetDurationForPayload("ai_image", "fixed", 60)).toBe(
      undefined,
    );
  });

  it("drops hidden residue, NaN and out-of-range values", () => {
    expect(resolveTargetDurationForPayload("vimax", "fixed", "")).toBe(
      undefined,
    );
    expect(resolveTargetDurationForPayload("vimax", "fixed", Number.NaN)).toBe(
      undefined,
    );
    expect(resolveTargetDurationForPayload("vimax", "fixed", 1)).toBe(
      undefined,
    );
    expect(resolveTargetDurationForPayload("vimax", "fixed", 999)).toBe(
      undefined,
    );
  });
});

describe("isValidTargetDuration", () => {
  it("accepts empty and 5–600, rejects NaN/out-of-range", () => {
    expect(isValidTargetDuration("")).toBe(true);
    expect(isValidTargetDuration(5)).toBe(true);
    expect(isValidTargetDuration(600)).toBe(true);
    expect(isValidTargetDuration(Number.NaN)).toBe(false);
    expect(isValidTargetDuration(4)).toBe(false);
    expect(isValidTargetDuration(601)).toBe(false);
  });
});

describe("isTargetDurationVisible", () => {
  it("is the single shared visibility rule for UI and payload", () => {
    expect(isTargetDurationVisible("ai_image", "generate")).toBe(true);
    expect(isTargetDurationVisible("ai_image", "fixed")).toBe(false);
    expect(isTargetDurationVisible("vimax", "fixed")).toBe(true);
  });
});

describe("resolveSecondsPerFrameForPayload", () => {
  it("only sends in generate mode and within 3–8", () => {
    expect(resolveSecondsPerFrameForPayload("generate", 5)).toBe(5);
    expect(resolveSecondsPerFrameForPayload("fixed", 5)).toBe(undefined);
    expect(resolveSecondsPerFrameForPayload("generate", 2)).toBe(undefined);
    expect(resolveSecondsPerFrameForPayload("generate", Number.NaN)).toBe(
      undefined,
    );
  });
});

describe("remotion course project payload preservation", () => {
  it("keeps follow_project inheritance without injecting brand or palette overrides", () => {
    const payload = buildCreatePayload(
      base({
        taskType: "remotion_course",
        mode: "fixed",
        script: "固定逐字稿",
        courseProjectId: "cp_existing",
        style: "follow_project",
        voiceId: "follow_project",
        subtitleStyle: "follow_project",
      }),
    );

    expect(payload).toMatchObject({
      video_type: "remotion_course",
      aspect: "16:9",
      course_project_id: "cp_existing",
      style: "follow_project",
      subtitle_style: "default",
    });
    expect(payload.voice_id).toBeUndefined();
    expect(JSON.parse(JSON.stringify(payload))).not.toHaveProperty("voice_id");
    expect(payload).not.toHaveProperty("tts_speed");
    expect(payload).not.toHaveProperty("remotion_theme");
    expect(payload).not.toHaveProperty("color_palette");
  });
});
