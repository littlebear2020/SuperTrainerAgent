"use client";

import { ChevronDown, Clock, Film } from "lucide-react";
import { useState } from "react";

import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { videoTaskTypeLabel } from "@/core/video-gen/task-type-label";
import type {
  SceneGroupData,
  StoryboardFrameData,
} from "@/core/video-gen/types";
import { cn } from "@/lib/utils";

interface SceneGroupCardProps {
  group: SceneGroupData;
  frames: StoryboardFrameData[];
  defaultExpanded?: boolean;
  videoType: string;
  renderFrameCard: (
    frame: StoryboardFrameData,
    groupIndex: number,
  ) => React.ReactNode;
  /** D2（2026-08-19）：受控展开——进度条点击需程序化展开目标组；
   * 不传则回落内部态（detail 页等既有调用零改动兼容）。 */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** 滚动锚点：进度条点击后 scrollIntoView 定位用。 */
  anchorId?: string;
}

function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return m > 0 ? `${m}分${s}秒` : `${s}秒`;
}

export function SceneGroupCard({
  group,
  frames,
  defaultExpanded = false,
  videoType,
  renderFrameCard,
  open,
  onOpenChange,
  anchorId,
}: SceneGroupCardProps) {
  const [internalOpen, setInternalOpen] = useState(defaultExpanded);
  const isOpen = open ?? internalOpen;
  const setOpen = onOpenChange ?? setInternalOpen;

  const groupFrames = group.frame_indices
    .map((fi) => frames[fi])
    .filter((f): f is StoryboardFrameData => !!f);

  const groupDuration = groupFrames.reduce(
    (sum, f) => sum + (f.duration ?? 3.0),
    0,
  );

  // T12（2026-09-10 独立管线计划）：修前直接渲染原始枚举值（"N 帧 · remotion_course"）——
  // 必要修复，非适配性改动：副标题枚举值走 videoTaskTypeLabel 翻译。
  const visualLabel =
    group.visual_source_override === "mixed"
      ? "混合"
      : (group.visual_source_override ?? videoTaskTypeLabel(videoType));

  return (
    <Collapsible open={isOpen} onOpenChange={setOpen}>
      <div id={anchorId} className="overflow-hidden rounded-lg border">
        <CollapsibleTrigger asChild>
          <button
            type="button"
            className="bg-muted/40 hover:bg-muted/60 flex w-full items-center gap-3 px-4 py-3 text-left transition-colors"
          >
            <ChevronDown
              className={cn(
                "text-muted-foreground h-4 w-4 shrink-0 transition-transform",
                !isOpen && "-rotate-90",
              )}
            />
            <Film className="text-muted-foreground h-4 w-4 shrink-0" />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="truncate text-sm font-medium">
                  {group.title}
                </span>
                <span className="text-muted-foreground inline-flex items-center gap-0.5 text-xs">
                  <Clock className="h-3 w-3" />
                  {formatDuration(groupDuration)}
                </span>
              </div>
              <span className="text-muted-foreground text-xs">
                {groupFrames.length} 帧 · {visualLabel}
                {group.visual_theme ? ` · ${group.visual_theme}` : ""}
              </span>
            </div>
          </button>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="grid gap-4 p-4 sm:grid-cols-2 lg:grid-cols-3">
            {groupFrames.map((frame) => renderFrameCard(frame, group.index))}
          </div>
        </CollapsibleContent>
      </div>
    </Collapsible>
  );
}
