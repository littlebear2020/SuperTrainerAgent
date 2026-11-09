// frontend/src/core/video-gen/scene-groups.test.ts
import { describe, expect, it } from "vitest";

import { deriveDisplayGroups, UNGROUPED_SENTINEL_INDEX } from "./scene-groups";
import type { SceneGroupData, StoryboardFrameData } from "./types";

const frame = (index: number): StoryboardFrameData =>
  ({ index, narration: `n${index}` }) as StoryboardFrameData;

describe("deriveDisplayGroups", () => {
  it("无分组时原样返回空", () => {
    expect(deriveDisplayGroups([], [frame(0), frame(1)])).toEqual([]);
  });

  it("全部已覆盖时不追加未分组", () => {
    const groups: SceneGroupData[] = [
      { index: 0, title: "A", frame_indices: [0, 1] },
    ];
    const out = deriveDisplayGroups(groups, [frame(0), frame(1)]);
    expect(out).toHaveLength(1);
  });

  it("新增帧不在任何组时追加未分组尾组", () => {
    const groups: SceneGroupData[] = [
      { index: 0, title: "A", frame_indices: [0, 1] },
    ];
    const out = deriveDisplayGroups(groups, [frame(0), frame(1), frame(2)]);
    expect(out).toHaveLength(2);
    const tail = out[1]!;
    expect(tail.index).toBe(UNGROUPED_SENTINEL_INDEX);
    expect(tail.title).toBe("未分组");
    expect(tail.frame_indices).toEqual([2]);
  });
});
