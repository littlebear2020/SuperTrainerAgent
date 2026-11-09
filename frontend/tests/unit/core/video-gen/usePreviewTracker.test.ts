import { describe, expect, it } from "vitest";

import { releaseFrame, trackFrame } from "@/core/video-gen/usePreviewTracker";

describe("preview tracker (M1-3: clicking #2 must not drop #1)", () => {
  it("tracks multiple frames concurrently without dropping earlier ones", () => {
    let p = new Set<number>();
    p = trackFrame(p, 1);
    p = trackFrame(p, 2); // 点 #2 不得影响 #1（RQv5 共享 mutation 的旧 bug）
    expect(p.has(1)).toBe(true);
    expect(p.has(2)).toBe(true);
    p = releaseFrame(p, 1);
    expect(p.has(1)).toBe(false);
    expect(p.has(2)).toBe(true); // #2 仍在
  });
});
