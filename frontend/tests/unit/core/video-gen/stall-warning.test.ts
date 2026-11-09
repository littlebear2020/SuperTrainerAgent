import { describe, expect, it } from "vitest";

import { getStallWarning } from "@/core/video-gen/hooks";
import type { VideoTaskBrief } from "@/core/video-gen/types";

const NOW = new Date("2026-07-13T12:00:00Z").getTime();
const MIN = 60_000;

function task(over: Partial<VideoTaskBrief>): VideoTaskBrief {
  return {
    id: "vg_t",
    user_id: "u1",
    domain: "video_gen",
    task_type: "ai_image",
    status: "running",
    progress: 50,
    title: null,
    error_message: null,
    retry_count: 0,
    dirty_frames: [],
    created_at: null,
    updated_at: null,
    thumbnail_url: null,
    cost: null,
    currency: null,
    ...over,
  };
}

describe("getStallWarning", () => {
  it("pending 任务 updated_at 超过 5min → 返回降级提示", () => {
    const t = task({
      status: "pending",
      updated_at: new Date(NOW - 6 * MIN).toISOString(),
      created_at: new Date(NOW - 7 * MIN).toISOString(),
    });
    expect(getStallWarning(t, NOW)).not.toBeNull();
  });

  it("running 任务刚刚更新（1min）→ null（未卡死）", () => {
    const t = task({
      status: "running",
      updated_at: new Date(NOW - 1 * MIN).toISOString(),
    });
    expect(getStallWarning(t, NOW)).toBeNull();
  });

  it("ready/failed 等终态 → null（不报卡死）", () => {
    for (const status of ["ready", "failed", "draft"]) {
      const t = task({
        status,
        updated_at: new Date(NOW - 30 * MIN).toISOString(),
      });
      expect(getStallWarning(t, NOW)).toBeNull();
    }
  });

  it("updated_at 缺失时用 created_at 粗代理（超过阈值仍提示）", () => {
    const t = task({
      status: "running",
      updated_at: null,
      created_at: new Date(NOW - 8 * MIN).toISOString(),
    });
    expect(getStallWarning(t, NOW)).not.toBeNull();
  });

  it("composing 超时也提示（重新合成卡住）", () => {
    const t = task({
      status: "composing",
      updated_at: new Date(NOW - 7 * MIN).toISOString(),
    });
    expect(getStallWarning(t, NOW)).not.toBeNull();
  });
});
