import { describe, expect, it } from "vitest";

import { storyboardRefetchInterval } from "@/core/video-gen/hooks";
import type { StoryboardData } from "@/core/video-gen/types";

function q(data: Partial<StoryboardData> | undefined, error: Error | null) {
  return { state: { data, error } } as unknown as Parameters<
    typeof storyboardRefetchInterval
  >[0];
}

describe("storyboardRefetchInterval", () => {
  // 第 3 参 taskFailed 对应 B2 边界

  it("404/error → 10s 退避（E1：非 2s 刷屏；生成中改由 status 字段标识，不再 404）", () => {
    expect(
      storyboardRefetchInterval(q(undefined, new Error("404")), false, false),
    ).toBe(10000);
  });

  it("生成中（status=generating，200 响应）→ 每 2s 轮询至落盘", () => {
    expect(
      storyboardRefetchInterval(
        q({ status: "generating" } as Partial<StoryboardData>, null),
        false,
        false,
      ),
    ).toBe(2000);
  });

  it("task 已失败 → 停止轮询（B2：防永不落盘的 404 无限轮询）", () => {
    expect(
      storyboardRefetchInterval(q(undefined, new Error("404")), false, true),
    ).toBe(false);
  });

  it("audio_ready 后停止", () => {
    expect(
      storyboardRefetchInterval(q({ audio_ready: true }, null), true, false),
    ).toBe(false);
  });

  it("audio_error 后停止（非再生成态：pollAudioReady=false）", () => {
    expect(
      storyboardRefetchInterval(
        q({ audio_error: "boom" } as Partial<StoryboardData>, null),
        false,
        false,
      ),
    ).toBe(false);
  });

  it("B3 复审 F2③：再生成中（pollAudioReady=true）data 仍挂上一轮 audio_error → 必须继续 3s 轮询（修前 error 早退把重试/帧卡 action 的轮询恢复一并判停，生成完成后 UI 无感知）", () => {
    expect(
      storyboardRefetchInterval(
        q({ audio_error: "boom" } as Partial<StoryboardData>, null),
        true,
        false,
      ),
    ).toBe(3000);
  });

  it("F2③ 边界：audio_ready=true 时 pollAudioReady 让位（data 比触发信号新，就绪即停）", () => {
    expect(
      storyboardRefetchInterval(q({ audio_ready: true }, null), true, false),
    ).toBe(false);
  });

  it("audio 轮询中、未就绪 → 每 3s", () => {
    expect(
      storyboardRefetchInterval(q({ audio_ready: false }, null), true, false),
    ).toBe(3000);
  });

  it("非 audio 轮询、数据已加载、无 error → 停止", () => {
    expect(
      storyboardRefetchInterval(q({ audio_ready: false }, null), false, false),
    ).toBe(false);
  });

  // 第 4 参 hasPendingPreview（预览异步化 2026-07-21）

  it("audio_ready 但有预览在途 → 3s 轮询", () => {
    expect(
      storyboardRefetchInterval(
        q({ audio_ready: true }, null),
        false,
        false,
        true,
      ),
    ).toBe(3000);
  });

  it("audio_ready 且无预览在途 → 停止（回归：原行为不变）", () => {
    expect(
      storyboardRefetchInterval(
        q({ audio_ready: true }, null),
        false,
        false,
        false,
      ),
    ).toBe(false);
  });

  it("音频未就绪且无 audio 轮询、有预览在途 → 3s 轮询", () => {
    expect(
      storyboardRefetchInterval(
        q({ audio_ready: false }, null),
        false,
        false,
        true,
      ),
    ).toBe(3000);
  });
});
