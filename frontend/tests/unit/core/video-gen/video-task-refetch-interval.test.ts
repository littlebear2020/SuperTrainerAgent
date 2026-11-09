import { expect, test } from "vitest";

import { videoTaskRefetchInterval } from "@/core/video-gen/hooks";

test("ready/failed stops polling by default", () => {
  expect(videoTaskRefetchInterval("ready")).toBe(false);
  expect(videoTaskRefetchInterval("failed")).toBe(false);
});

test("ready keeps polling during recompose grace (M8)", () => {
  // recompose 派发后 status 要等拿到渲染信号量才翻 composing——grace 期停轮询
  // 会错过 composing→ready 跳变（视频不刷新、dirty 徽标不清）。
  expect(videoTaskRefetchInterval("ready", () => true)).toBe(2000);
  expect(videoTaskRefetchInterval("ready", () => false)).toBe(false);
});

test("active statuses always poll", () => {
  expect(videoTaskRefetchInterval("composing")).toBe(2000);
  expect(videoTaskRefetchInterval("running")).toBe(2000);
  expect(videoTaskRefetchInterval(undefined)).toBe(2000);
});
