import { describe, expect, it } from "vitest";

import { HOME_COURSE_ITEMS } from "@/components/workspace/home-course-catalog";

describe("HOME_COURSE_ITEMS", () => {
  it("exposes exactly six confirmed routes in stable order", () => {
    expect(HOME_COURSE_ITEMS.map((item) => item.id)).toEqual([
      "course-video",
      "ai_image",
      "ai_video",
      "creative-studio",
      "ppt-master",
      "co-writer",
    ]);
    expect(HOME_COURSE_ITEMS.slice(0, 3).every((item) => item.entry === "video")).toBe(true);
    expect(HOME_COURSE_ITEMS.slice(3).every((item) => item.entry === "text")).toBe(true);
  });

  it("maps agent entries and AI entries without inventing remotion_course route", () => {
    const agents = HOME_COURSE_ITEMS.filter((item) => item.kind === "agent");
    expect(agents.map((item) => item.agentName)).toEqual([
      "course-video",
      "creative-studio",
      "ppt-master",
      "co-writer",
    ]);
    const aiItems = HOME_COURSE_ITEMS.filter((item) => item.kind === "video-task");
    expect(aiItems.map((item) => item.initialTaskType)).toEqual([
      "ai_image",
      "ai_video",
    ]);
    expect(HOME_COURSE_ITEMS[0]?.kind).toBe("agent");
  });

  it("uses only optimized course sample media", () => {
    const externalMedia = HOME_COURSE_ITEMS.flatMap((item) =>
      "src" in item.media ? [item.media.src] : [],
    );
    expect(externalMedia).toHaveLength(4);
    for (const src of externalMedia) {
      expect(src.startsWith("/course-samples/")).toBe(true);
      expect(src).not.toContain("assets/");
      expect(src).not.toMatch(/\.png$|^https?:/);
    }
    expect(externalMedia).not.toContain("/course-samples/course-poster.webp");
  });
});
