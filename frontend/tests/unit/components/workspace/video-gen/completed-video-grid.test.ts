import { describe, expect, it } from "vitest";

import {
  canPreviewOrDownload,
  selectCompletedWorks,
} from "@/components/workspace/video-gen/completed-video-grid";
import type { VideoTaskBrief } from "@/core/video-gen/types";

function task(
  id: string,
  overrides: Partial<VideoTaskBrief> = {},
): VideoTaskBrief {
  return {
    id,
    user_id: "u1",
    domain: "video_gen",
    task_type: "ai_image",
    status: "ready",
    progress: 100,
    title: id,
    error_message: null,
    retry_count: 0,
    dirty_frames: [],
    created_at: "2026-11-01T00:00:00Z",
    updated_at: null,
    thumbnail_url: null,
    cost: null,
    currency: null,
    ...overrides,
  };
}

describe("selectCompletedWorks", () => {
  it("filters only ready ai_image and ai_video", () => {
    const tasks = [
      task("image"),
      task("video", { task_type: "ai_video" }),
      task("course", { task_type: "remotion_course" }),
      task("vimax", { task_type: "vimax" }),
      task("failed", { status: "failed" }),
      task("draft", { status: "draft" }),
      task("running", { status: "running" }),
    ];
    expect(selectCompletedWorks(tasks).map((item) => item.id)).toEqual([
      "image",
      "video",
    ]);
  });

  it("sorts created_at descending without mutating input and caps at 100", () => {
    const tasks = Array.from({ length: 103 }, (_, index) =>
      task(`id-${String(index).padStart(3, "0")}`, {
        created_at: new Date(Date.UTC(2026, 0, 1, 0, index)).toISOString(),
      }),
    );
    tasks.push(task("bad-date", { created_at: "not-a-date" }));
    tasks.push(task("empty-date", { created_at: null }));
    const shuffled = [...tasks].reverse();
    const inputOrder = shuffled.map((item) => item.id);
    const result = selectCompletedWorks(shuffled);
    expect(result).toHaveLength(100);
    expect(result[0]?.id).toBe("id-102");
    expect(result.map((item) => item.id)).not.toContain("bad-date");
    expect(shuffled.map((item) => item.id)).toEqual(inputOrder);

    const withInvalid = selectCompletedWorks([
      task("valid", { created_at: "2026-01-01T00:00:00Z" }),
      task("bad", { created_at: "bad" }),
      task("empty", { created_at: null }),
    ]);
    expect(withInvalid.map((item) => item.id)).toEqual([
      "valid",
      "bad",
      "empty",
    ]);
    const ties = [
      task("b", { created_at: "2026-01-01T00:00:00Z" }),
      task("a", { created_at: "2026-01-01T00:00:00Z" }),
    ];
    expect(selectCompletedWorks(ties).map((item) => item.id)).toEqual([
      "a",
      "b",
    ]);
    expect(
      selectCompletedWorks([...ties].reverse()).map((item) => item.id),
    ).toEqual(["a", "b"]);
  });
});

describe("canPreviewOrDownload", () => {
  it("gates preview and download on ready and non-missing", () => {
    expect(
      canPreviewOrDownload({ status: "ready", artifact_missing: true }),
    ).toBe(false);
    expect(canPreviewOrDownload({ status: "ready" })).toBe(true);
    expect(
      canPreviewOrDownload({ status: "ready", artifact_missing: false }),
    ).toBe(true);
    expect(canPreviewOrDownload({ status: "failed" })).toBe(false);
  });
});
