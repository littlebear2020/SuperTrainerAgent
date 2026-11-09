import { test, expect } from "vitest";

import { buildCreatePayload } from "@/components/workspace/video-gen/create-payload";

const baseInput = {
  taskType: "ai_image" as const,
  mode: "generate" as const,
  script: "",
  topic: "红烧肉",
  lang: "zh",
  aspect: "9:16" as const,
  style: "default",
  subtitleStyle: "default" as const,
  bgm: null,
  voiceId: null,
  outputFormat: "mp4" as const,
  quality: "standard" as const,
  ttsSpeed: 1.0,
  bgmVolume: 0.15,
  bgmDucking: true,
  engine: "auto",
};

test("buildCreatePayload defaults auto_render to false (storyboard-first)", () => {
  const payload = buildCreatePayload(baseInput);
  expect(payload.auto_render).toBe(false);
});

test("buildCreatePayload sets auto_render true only when explicitly requested", () => {
  const payload = buildCreatePayload({ ...baseInput, autoRender: true });
  expect(payload.auto_render).toBe(true);
});

test("buildCreatePayload forwards all fields", () => {
  const payload = buildCreatePayload({ ...baseInput, courseProjectId: "cp-1" });
  expect(payload.video_type).toBe("ai_image");
  expect(payload.course_project_id).toBe("cp-1");
  expect(payload.topic).toBe("红烧肉");
});

test("buildCreatePayload forwards target_duration when provided", () => {
  const payload = buildCreatePayload({ ...baseInput, targetDuration: 30 });
  expect(payload.target_duration).toBe(30);
});

test("buildCreatePayload omits target_duration when not provided", () => {
  const payload = buildCreatePayload(baseInput);
  expect(payload.target_duration).toBeUndefined();
});

// Task 6（2026-08-01）：任务级生成模型。
// 关键不变量：**不指定时请求体里不出现该字段**，保持与改动前完全一致 ——
// 避免后端把「显式 null」与「未指定」当成两回事。
// E（2026-08-31 转场清理）：前端不再下发 transition / transition_duration——
// 后端缺省即 none（硬切）；旧后端收到无 transition 的 payload 也走自身默认值。
test("buildCreatePayload omits transition fields (hard-cut by default)", () => {
  const payload = buildCreatePayload(baseInput);
  expect("transition" in payload).toBe(false);
  expect("transition_duration" in payload).toBe(false);
});

test("buildCreatePayload omits provider fields when unspecified", () => {
  const payload = buildCreatePayload(baseInput);
  expect("image_provider" in payload).toBe(false);
  expect("video_provider" in payload).toBe(false);
});

test("buildCreatePayload omits provider fields when explicitly null", () => {
  const payload = buildCreatePayload({
    ...baseInput,
    imageProvider: null,
    videoProvider: null,
  });
  expect("image_provider" in payload).toBe(false);
  expect("video_provider" in payload).toBe(false);
});

test("buildCreatePayload forwards a chosen image provider", () => {
  const payload = buildCreatePayload({
    ...baseInput,
    imageProvider: "nano-banana",
  });
  expect(payload.image_provider).toBe("nano-banana");
  expect("video_provider" in payload).toBe(false);
});

test("buildCreatePayload forwards a chosen video provider", () => {
  const payload = buildCreatePayload({
    ...baseInput,
    taskType: "ai_video",
    videoProvider: "minimax-h3",
  });
  expect(payload.video_provider).toBe("minimax-h3");
  expect("image_provider" in payload).toBe(false);
});

test("buildCreatePayload omits graphic_ratio at default 30 (backend default)", () => {
  // T3（2026-09-01）：默认 30 不下发——请求体形状与改动前一致，后端缺省承担 30%。
  const payload = buildCreatePayload({ ...baseInput, graphicRatio: 30 });
  expect("graphic_ratio" in payload).toBe(false);
  const payloadDefault = buildCreatePayload(baseInput);
  expect("graphic_ratio" in payloadDefault).toBe(false);
});

test("buildCreatePayload forwards non-default graphic ratio", () => {
  const payload = buildCreatePayload({ ...baseInput, graphicRatio: 50 });
  expect(payload.graphic_ratio).toBe(50);
});

// T-E11（D6 review #1，2026-09-09 v4.2 review）：语速「跟随」态。
// 关键不变量：null（跟随 = 项目 tts_speed > config 缺省）时请求体**不出现**
// tts_speed——修前恒显式下发 1.0，挂项目任务的后端三级链恒命中第一级，
// 项目级语速在 UI 主路径被架空。
test("buildCreatePayload omits tts_speed when following (null)", () => {
  const payload = buildCreatePayload({
    ...baseInput,
    ttsSpeed: null,
    courseProjectId: "cp-1",
  });
  expect("tts_speed" in payload).toBe(false);
});

test("buildCreatePayload forwards explicit tts_speed", () => {
  const payload = buildCreatePayload({ ...baseInput, ttsSpeed: 1.5 });
  expect(payload.tts_speed).toBe(1.5);
});
