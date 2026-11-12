import { describe, expect, it } from "vitest";

import { HOME_COURSE_ITEMS } from "@/components/workspace/home-course-catalog";

describe("HOME_COURSE_ITEMS", () => {
  it("exposes exactly six capabilities in stable order without category entries", () => {
    expect(HOME_COURSE_ITEMS.map((item) => item.id)).toEqual([
      "course-video",
      "ai_image",
      "ai_video",
      "creative-studio",
      "ppt-master",
      "podcast-studio",
    ]);
    expect(HOME_COURSE_ITEMS.every((item) => !("entry" in item))).toBe(true);
  });

  it("maps agent entries and AI entries without adding remotion_course or vimax routes", () => {
    expect(
      HOME_COURSE_ITEMS.filter((item) => item.kind === "agent").map(
        (item) => item.agentName,
      ),
    ).toEqual([
      "course-video",
      "creative-studio",
      "ppt-master",
      "podcast-studio",
    ]);
    expect(
      HOME_COURSE_ITEMS.filter((item) => item.kind === "video-task").map(
        (item) => item.initialTaskType,
      ),
    ).toEqual(["ai_image", "ai_video"]);
    expect(HOME_COURSE_ITEMS[0]?.kind).toBe("agent");
  });

  it("uses dedicated posters for videos and existing static images", () => {
    expect(HOME_COURSE_ITEMS.slice(0, 3).map((item) => item.media)).toEqual([
      {
        kind: "video",
        src: "/course-samples/remotion.mp4",
        poster: "/course-samples/text-motion-poster.webp",
      },
      {
        kind: "video",
        src: "/course-samples/image-motion.mp4",
        poster: "/course-samples/image-motion-poster.webp",
      },
      {
        kind: "video",
        src: "/course-samples/ai-video.mp4",
        poster: "/course-samples/ai-video-poster.webp",
      },
    ]);
    expect(HOME_COURSE_ITEMS.slice(3).map((item) => item.media)).toEqual([
      { kind: "image", src: "/course-samples/knowledge-card.webp" },
      { kind: "image", src: "/course-samples/ppt-work.webp" },
      { kind: "image", src: "/course-samples/podcast-work.webp" },
    ]);
  });
});
