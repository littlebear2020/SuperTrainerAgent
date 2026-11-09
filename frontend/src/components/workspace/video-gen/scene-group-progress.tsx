"use client";

import type {
  SceneGroupData,
  StoryboardFrameData,
} from "@/core/video-gen/types";
import { cn } from "@/lib/utils";

const GROUP_COLORS = [
  "bg-indigo-400",
  "bg-emerald-400",
  "bg-amber-400",
  "bg-rose-400",
  "bg-violet-400",
  "bg-cyan-400",
  "bg-orange-400",
  "bg-pink-400",
];

interface SceneGroupProgressProps {
  groups: SceneGroupData[];
  frames: StoryboardFrameData[];
  activeGroupIndex?: number;
  onGroupClick?: (index: number) => void;
}

function getGroupDuration(
  group: SceneGroupData,
  frames: StoryboardFrameData[],
): number {
  return group.frame_indices.reduce((sum, fi) => {
    const frame = frames[fi];
    return sum + (frame?.duration ?? 3.0);
  }, 0);
}

export function SceneGroupProgress({
  groups,
  frames,
  activeGroupIndex,
  onGroupClick,
}: SceneGroupProgressProps) {
  if (!groups.length) return null;

  const totalDuration = groups.reduce(
    (sum, g) => sum + getGroupDuration(g, frames),
    0,
  );
  if (totalDuration === 0) return null;

  return (
    <div className="bg-muted/30 flex h-8 w-full overflow-hidden rounded-md border">
      {groups.map((group, gi) => {
        const duration = getGroupDuration(group, frames);
        const percent = (duration / totalDuration) * 100;
        const isActive = activeGroupIndex === gi;
        // 2026-08-19：窄组降级——宽度过窄时标题 truncate 必剩省略号，改显帧数
        // （完整标题保留在 hover title 与点击定位后的帧区）。
        const narrow = percent < 6;
        return (
          <button
            key={gi}
            type="button"
            onClick={() => onGroupClick?.(gi)}
            className={cn(
              "flex items-center justify-center border-r px-2 text-xs font-medium text-white transition-all last:border-r-0 hover:brightness-110",
              GROUP_COLORS[gi % GROUP_COLORS.length],
              isActive && "ring-primary ring-2 ring-offset-1",
            )}
            style={{ width: `${percent}%`, minWidth: "40px" }}
            title={`${group.title} — ${duration.toFixed(0)}秒`}
          >
            <span className="truncate">
              {narrow ? `${group.frame_indices.length}帧` : group.title}
            </span>
          </button>
        );
      })}
    </div>
  );
}
