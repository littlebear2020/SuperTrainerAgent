import { describe, expect, it } from "vitest";

import { usageTaskHref } from "@/core/usage/routes";

describe("usageTaskHref", () => {
  it.each([
    "course-video",
    "creative-studio",
    "ppt-master",
    "podcast-studio",
  ] as const)("uses the canonical agent thread route for %s", (capability) => {
    expect(usageTaskHref({ source_id: "thread-1", capability })).toBe(
      `/workspace/agents/${capability}/chats/thread-1`,
    );
  });

  it.each(["ai_image", "ai_video"] as const)(
    "uses the video detail route for %s",
    (capability) => {
      expect(usageTaskHref({ source_id: "video/1 ?", capability })).toBe(
        "/workspace/video-gen/video%2F1%20%3F",
      );
    },
  );
});
