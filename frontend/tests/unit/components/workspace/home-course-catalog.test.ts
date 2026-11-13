import { existsSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { HOME_COURSE_ITEMS } from "@/components/workspace/home-course-catalog";

describe("HOME_COURSE_ITEMS", () => {
  it("keeps the six stable IDs and assigns three video and three studio groups", () => {
    expect(HOME_COURSE_ITEMS.map((item) => item.id)).toEqual([
      "course-video",
      "ai_image",
      "ai_video",
      "creative-studio",
      "ppt-master",
      "podcast-studio",
    ]);
    expect(HOME_COURSE_ITEMS.map((item) => item.group)).toEqual([
      "video",
      "video",
      "video",
      "studio",
      "studio",
      "studio",
    ]);
    expect(HOME_COURSE_ITEMS.every((item) => !("entry" in item))).toBe(true);
  });

  it("keeps the existing agent routes and AI task types", () => {
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
  });

  it("uses the approved video, poster and presentation media", () => {
    expect(HOME_COURSE_ITEMS.slice(0, 3).map((item) => item.media)).toEqual([
      {
        kind: "video",
        src: "/course-samples/text-motion-preview.mp4",
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
      { kind: "image", src: "/course-samples/poster-design.svg" },
      { kind: "image", src: "/course-samples/ppt-design.svg" },
      { kind: "image", src: "/course-samples/podcast-work.webp" },
    ]);
  });

  it.each([
    "teacher-A-half.png",
    "poster-design.svg",
    "ppt-design.svg",
    "text-motion-preview.mp4",
  ])("ships the approved production asset %s", (asset) => {
    expect(existsSync(`public/course-samples/${asset}`)).toBe(true);
  });
});
