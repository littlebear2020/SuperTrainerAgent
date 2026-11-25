import type { VideoTaskBrief } from "@/core/video-gen/types";
import type { WorkCapability } from "@/core/workspace/video-assets-route";

export type VideoTaskType = VideoTaskBrief["task_type"];

export const TASK_TYPE_CAPABILITY = {
  ai_image: "ai_image",
  ai_visual: "ai_image",
  ai_video: "ai_video",
  vimax: "ai_video",
  remotion_course: "course-video",
  remotion: "course-video",
} satisfies Record<VideoTaskType, WorkCapability>;

interface CapabilitySource {
  readonly agentNames: readonly WorkCapability[];
  readonly taskTypes: readonly VideoTaskType[];
}

export const WORK_CAPABILITY_SOURCES = {
  "course-video": {
    agentNames: ["course-video"],
    taskTypes: ["remotion_course", "remotion"],
  },
  ai_image: { agentNames: [], taskTypes: ["ai_image", "ai_visual"] },
  ai_video: { agentNames: [], taskTypes: ["ai_video", "vimax"] },
  "creative-studio": { agentNames: ["creative-studio"], taskTypes: [] },
  "ppt-master": { agentNames: ["ppt-master"], taskTypes: [] },
  "podcast-studio": { agentNames: ["podcast-studio"], taskTypes: [] },
} satisfies Record<WorkCapability, CapabilitySource>;
