import { expect, test } from "vitest";

import {
  computeClipLayout,
  frameBadge,
  GAP_PX,
  MIN_FRAME_WIDTH_PX,
  PX_PER_SECOND,
  timeToFrameIndex,
  timeToPx,
} from "@/core/video-gen/frameStrip";

const frames = [
  { index: 0, duration: 5 },
  { index: 1, duration: 10 },
  { index: 2, duration: 5 },
];

test("computeClipLayout computes cumulative start/width in pixels", () => {
  const layout = computeClipLayout(frames);
  expect(layout).toHaveLength(3);
  // 帧0: 5s * 24px/s = 120px, left=0
  expect(layout[0]).toMatchObject({
    frameIndex: 0,
    startTime: 0,
    leftPx: 0,
    widthPx: 120,
  });
  // 帧1: 10s * 24px/s = 240px, left=120+4(gap)=124
  expect(layout[1]).toMatchObject({
    frameIndex: 1,
    startTime: 5,
    leftPx: 124,
    widthPx: 240,
  });
  // 帧2: 5s * 24px/s = 120px, left=124+240+4=368
  expect(layout[2]).toMatchObject({
    frameIndex: 2,
    startTime: 15,
    leftPx: 368,
    widthPx: 120,
  });
});

test("computeClipLayout clamps tiny clips to MIN_FRAME_WIDTH_PX", () => {
  // 0.1s -> 2.4px -> clamped to 96px
  const layout = computeClipLayout([
    { index: 0, duration: 0.1 },
    { index: 1, duration: 100 },
  ]);
  expect(layout[0]!.widthPx).toBe(MIN_FRAME_WIDTH_PX); // 96
  // 像素模式不归一化：长帧保持真实比例
  expect(layout[1]!.widthPx).toBe(100 * PX_PER_SECOND); // 2400
  // leftPx 累进含 gap
  expect(layout[1]!.leftPx).toBe(MIN_FRAME_WIDTH_PX + GAP_PX);
});

test("computeClipLayout with all-zero durations falls back to MIN_FRAME_WIDTH_PX", () => {
  const layout = computeClipLayout([{ index: 0 }, { index: 1 }]);
  expect(layout[0]!.widthPx).toBe(MIN_FRAME_WIDTH_PX);
  expect(layout[1]!.widthPx).toBe(MIN_FRAME_WIDTH_PX);
  // leftPx 累进含 gap
  expect(layout[1]!.leftPx).toBe(MIN_FRAME_WIDTH_PX + GAP_PX);
  // startTime 全 0（无时长信息）
  expect(layout[0]!.startTime).toBe(0);
  expect(layout[1]!.startTime).toBe(0);
});

test("computeClipLayout gap is included in leftPx accumulation", () => {
  // 3 帧等时长 -> 等宽，leftPx 应按 widthPx + gap 累进
  const layout = computeClipLayout([
    { index: 0, duration: 4 },
    { index: 1, duration: 4 },
    { index: 2, duration: 4 },
  ]);
  const w = 4 * PX_PER_SECOND; // 96
  expect(layout[0]!.leftPx).toBe(0);
  expect(layout[1]!.leftPx).toBe(w + GAP_PX);
  expect(layout[2]!.leftPx).toBe(2 * (w + GAP_PX));
});

test("timeToFrameIndex maps seconds to the owning frame", () => {
  const layout = computeClipLayout(frames);
  expect(timeToFrameIndex(layout, 0)).toBe(0);
  expect(timeToFrameIndex(layout, 4.99)).toBe(0);
  expect(timeToFrameIndex(layout, 5)).toBe(1); // 边界归后一帧
  expect(timeToFrameIndex(layout, 19.9)).toBe(2);
});

test("timeToFrameIndex past the end returns last frame; empty returns null", () => {
  const layout = computeClipLayout(frames);
  expect(timeToFrameIndex(layout, 999)).toBe(2);
  expect(timeToFrameIndex([], 3)).toBeNull();
});

test("timeToPx maps seconds to pixel position (piecewise-linear)", () => {
  const layout = computeClipLayout(frames);
  // [0-5s|0-120px] [5-15s|124-364px] [15-20s|368-488px]
  expect(timeToPx(layout, 0)).toBe(0);
  // 帧0 中点 2.5s -> 60px
  expect(timeToPx(layout, 2.5)).toBe(60);
  // 帧1 中点 10s -> 124 + (5/10)*240 = 124+120 = 244
  expect(timeToPx(layout, 10)).toBe(244);
  // 越界钉末帧右边缘
  expect(timeToPx(layout, 999)).toBe(488);
  expect(timeToPx([], 3)).toBe(0);
});

test("timeToPx returns 0 for zero-duration layout", () => {
  const layout = computeClipLayout([{ index: 0 }, { index: 1 }]);
  expect(timeToPx(layout, 0)).toBe(0);
});

// P2-9: 帧状态徽标--failed（preview_error）优先于 reuse（image_reuse 冻结复用）。
test("frameBadge: preview_error > image_reuse > null", () => {
  expect(frameBadge({ preview_error: "boom" })).toBe("failed");
  expect(frameBadge({ visual_type: "image_reuse" })).toBe("reuse");
  expect(frameBadge({ visual_type: "image_reuse", preview_error: "x" })).toBe(
    "failed",
  );
  expect(frameBadge({ visual_type: "image" })).toBeNull();
  expect(frameBadge({})).toBeNull();
});
