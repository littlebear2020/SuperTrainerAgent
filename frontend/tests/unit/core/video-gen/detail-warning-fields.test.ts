/**
 * 2026-08-03：任务详情的降级告警必须消费**所有**后端下发的 warning 字段。
 *
 * 真实事故：后端 `_attach_degradation_warning` 一直在算 `degradation_warning`
 * （bgm 混音失败 / 字幕烧录失败 / 转场降级 / 音色降级 + 本轮新增的
 * visual_shortfall / preview_i2v_unsupported / prompt_neutralized），
 * 但前端**从未读过这个字段** —— 整条 degradations.json 通道对用户不可见。
 * 与 2026-07-29 的 `ai_image_i2i_warning` 是同型事故（后端写 sidecar、全仓零读取）。
 *
 * 本测试锁住「字段清单」这一契约：新增后端 warning 字段时若忘了在详情页接线，
 * 这里会转红。用源码断言而非渲染测试——渲染整个详情页需要大量 mock，
 * 而真正易漏的就是「有没有把字段名接进去」。
 */
import { readFileSync } from "fs";
import { join } from "path";

import { describe, expect, it } from "vitest";

const SRC = readFileSync(
  join(
    __dirname,
    // 2026-10-06 拆分批：警告横幅自 video-gen-detail 的 IIFE 抽出为独立组件，
    // 字段接线守卫随代码迁到新文件。
    "../../../../src/components/workspace/video-gen/video-gen-detail-warning-banner.tsx",
  ),
  "utf-8",
);

/** 后端 routers/video_gen.py 会写进 task.result 的全部 warning 字段。 */
const BACKEND_WARNING_FIELDS = [
  "subtitle_status",
  "duration_warning",
  "remotion_cjk_warning",
  "ai_video_coverage_warning",
  "av_drift_warning",
  "voice_mode_warning",
  "placeholder_warning",
  "ai_image_i2i_warning",
  "degradation_warning",
];

describe("video-gen 详情页降级告警", () => {
  it.each(BACKEND_WARNING_FIELDS)("消费后端字段 %s", (field) => {
    expect(SRC).toContain(field);
  });

  it("degradation_warning 既被读取也被渲染", () => {
    expect(SRC).toContain("r.degradation_warning");
    // 读进变量还不够——必须进 hasAny 判定并有对应的渲染块
    expect(SRC).toContain("!!degWarn");
    expect(SRC).toMatch(/\{degWarn && \(/);
  });
});
