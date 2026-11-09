// frontend/src/core/video-gen/scene-groups.ts
import type { SceneGroupData, StoryboardFrameData } from "./types";

export const UNGROUPED_SENTINEL_INDEX = -1;

/** 分组模式的展示分组：未被任何组覆盖的帧（历史/异常数据）合成尾部「未分组」组，防丢帧。 */
export function deriveDisplayGroups(
  groups: SceneGroupData[],
  frames: StoryboardFrameData[],
): SceneGroupData[] {
  if (!groups.length) return [];
  const covered = new Set(groups.flatMap((g) => g.frame_indices));
  const leftover = frames.map((f) => f.index).filter((i) => !covered.has(i));
  if (!leftover.length) return groups;
  return [
    ...groups,
    {
      index: UNGROUPED_SENTINEL_INDEX,
      title: "未分组",
      frame_indices: leftover,
    },
  ];
}
