/** content-match T8③: 来源类型徽标（图/视/卡）纯逻辑。 */
import { expect, test } from "vitest";

import { frameSourceBadge } from "@/core/video-gen/frameStrip";

test("ai_video 帧 → 视", () => {
  expect(frameSourceBadge({ visual_source: "ai_video" })).toEqual({
    kind: "video",
    label: "视",
  });
});

test("graphic 帧 → 文（图内文字镜）", () => {
  expect(frameSourceBadge({ visual_source: "graphic" })).toEqual({
    kind: "card",
    label: "文",
  });
});

test("ai_image 帧 → 图", () => {
  expect(frameSourceBadge({ visual_source: "ai_image" })).toEqual({
    kind: "image",
    label: "图",
  });
});

test("visual_source 缺省回落任务类型（与 frame_ops 同口径）", () => {
  expect(frameSourceBadge({}, "ai_video")?.kind).toBe("video");
  expect(frameSourceBadge({}, "ai_image")?.kind).toBe("image");
  expect(frameSourceBadge({}, "graphic")?.kind).toBe("card");
});

test("存量 remotion 与未知值不标", () => {
  expect(frameSourceBadge({ visual_source: "remotion" })).toBeNull();
  expect(frameSourceBadge({ visual_source: "hologram" })).toBeNull();
  expect(frameSourceBadge({}, undefined)).toBeNull();
});
