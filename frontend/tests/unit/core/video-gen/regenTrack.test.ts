import { expect, test } from "vitest";

import {
  clearFrameRegenState,
  isRegenResolved,
  isRegenTimedOut,
  REGEN_TIMEOUT_MS,
} from "@/core/video-gen/regenTrack";
import type { StoryboardData } from "@/core/video-gen/types";

const sb: StoryboardData = {
  title: "t",
  frames: [
    { index: 0, narration: "a", regen_state: "error", regen_error: "old" },
    { index: 1, narration: "b" },
  ],
} as unknown as StoryboardData;

test("clearFrameRegenState clears only the target frame's regen fields", () => {
  const next = clearFrameRegenState(sb, 0);
  expect(next?.frames[0]?.regen_state).toBeUndefined();
  expect(next?.frames[0]?.regen_error).toBeUndefined();
  expect(next?.frames[1]).toBe(sb.frames[1]); // 其他帧引用不变（structural sharing）
});

test("isRegenResolved only on terminal states", () => {
  expect(isRegenResolved({ regen_state: "done" })).toBe(true);
  expect(isRegenResolved({ regen_state: "error" })).toBe(true);
  expect(isRegenResolved({ regen_state: "running" })).toBe(false);
  expect(isRegenResolved(undefined)).toBe(false);
  expect(isRegenResolved({})).toBe(false);
});

test("isRegenTimedOut", () => {
  expect(isRegenTimedOut(1000, 1000 + REGEN_TIMEOUT_MS + 1)).toBe(true);
  expect(isRegenTimedOut(1000, 1000 + 1000)).toBe(false);
});
