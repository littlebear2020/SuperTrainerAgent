import { describe, expect, it } from "vitest";

import {
  filterBgmTracks,
  formatBgmDuration,
} from "@/core/video-gen/bgm-filter";
import type { BGMTrack } from "@/core/video-gen/types";

const tracks: BGMTrack[] = [
  {
    id: "strive",
    name: "奋进",
    preview_url: "/x/1",
    source: "built-in",
    tags: ["励志", "企业"],
    duration: 80,
  },
  {
    id: "morning_piano",
    name: "清晨钢琴",
    preview_url: "/x/2",
    source: "built-in",
    tags: ["钢琴", "清晨"],
    duration: 179,
  },
];

describe("filterBgmTracks", () => {
  it("空查询返回全部", () => {
    expect(filterBgmTracks(tracks, "  ")).toHaveLength(2);
  });
  it("按名称过滤", () => {
    expect(filterBgmTracks(tracks, "奋进")).toEqual([tracks[0]]);
  });
  it("按 tag 过滤", () => {
    expect(filterBgmTracks(tracks, "钢琴")).toEqual([tracks[1]]);
  });
});

describe("formatBgmDuration", () => {
  it("超过一分钟 → 分秒", () => expect(formatBgmDuration(80)).toBe("1分20秒"));
  it("不足一分钟 → 秒", () => expect(formatBgmDuration(41)).toBe("41秒"));
  it("空值 → 空串", () => expect(formatBgmDuration(null)).toBe(""));
});
