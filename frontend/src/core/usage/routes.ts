import { pathOfThread } from "@/core/threads/utils";

import type { UsageCapability } from "./types";

export function usageTaskHref(task: {
  source_id: string;
  capability: UsageCapability;
}): string {
  if (task.capability === "ai_image" || task.capability === "ai_video") {
    return `/workspace/video-gen/${encodeURIComponent(task.source_id)}`;
  }
  return pathOfThread(task.source_id, { agent_name: task.capability });
}
