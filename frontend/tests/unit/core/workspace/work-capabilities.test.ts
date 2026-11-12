import { expect, test } from "vitest";

import { WORK_CAPABILITIES } from "@/core/workspace/work-capabilities";

test("keeps the six work capabilities in home order with their real creation routes", () => {
  expect(
    WORK_CAPABILITIES.map((item) => ({
      id: item.id,
      kind: item.kind,
      agentName: item.kind === "agent" ? item.agentName : undefined,
      initialTaskType:
        item.kind === "video-task" ? item.initialTaskType : undefined,
    })),
  ).toEqual([
    {
      id: "course-video",
      kind: "agent",
      agentName: "course-video",
      initialTaskType: undefined,
    },
    {
      id: "ai_image",
      kind: "video-task",
      agentName: undefined,
      initialTaskType: "ai_image",
    },
    {
      id: "ai_video",
      kind: "video-task",
      agentName: undefined,
      initialTaskType: "ai_video",
    },
    {
      id: "creative-studio",
      kind: "agent",
      agentName: "creative-studio",
      initialTaskType: undefined,
    },
    {
      id: "ppt-master",
      kind: "agent",
      agentName: "ppt-master",
      initialTaskType: undefined,
    },
    {
      id: "podcast-studio",
      kind: "agent",
      agentName: "podcast-studio",
      initialTaskType: undefined,
    },
  ]);
});
