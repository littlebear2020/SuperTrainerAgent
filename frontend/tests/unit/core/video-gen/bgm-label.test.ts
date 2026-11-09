import { describe, expect, it } from "vitest";

import { resolveBgmLabel } from "@/core/video-gen/bgm-filter";
import type { BGMTrack } from "@/core/video-gen/types";

const tracks: BGMTrack[] = [
  {
    id: "morning_piano",
    name: "清晨钢琴",
    preview_url: "/x/1",
    source: "built-in",
  },
  {
    id: "custom_abc123",
    name: "my_song.mp3",
    preview_url: "/x/2",
    source: "custom",
  },
];

describe("resolveBgmLabel", () => {
  it("已知内置曲 → 显示中文名，不算缺失", () => {
    expect(resolveBgmLabel("morning_piano", tracks)).toEqual({
      label: "清晨钢琴",
      missing: false,
    });
  });

  it("已知自定义曲 → 显示上传名", () => {
    expect(resolveBgmLabel("custom_abc123", tracks)).toEqual({
      label: "my_song.mp3",
      missing: false,
    });
  });

  it("已下架的旧曲 id → 标记 missing 并给用户可读说明", () => {
    // 2026-07-31 删旧 3 首后，老任务 payload 仍指向 ambient_calm 等；
    // 后端静默降级为无 BGM，前端必须让用户看见，不能只显示裸 id。
    expect(resolveBgmLabel("ambient_calm", tracks)).toEqual({
      label: "ambient_calm（曲目已下架，本片无背景音乐）",
      missing: true,
    });
  });

  it("曲库还没加载完 → 暂不判定缺失（避免误报）", () => {
    expect(resolveBgmLabel("ambient_calm", [])).toEqual({
      label: "ambient_calm",
      missing: false,
    });
  });

  it("无 BGM → 空标签", () => {
    expect(resolveBgmLabel(null, tracks)).toEqual({
      label: "",
      missing: false,
    });
  });
});
