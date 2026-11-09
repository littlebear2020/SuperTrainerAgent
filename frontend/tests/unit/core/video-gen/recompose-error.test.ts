import { expect, test } from "vitest";

import { parseUnavailableFrames } from "@/core/video-gen/recomposeError";

test("parses single frame number (1-based -> 0-based)", () => {
  expect(parseUnavailableFrames("帧 3 的画面素材不可用（共 1/5 帧）")).toEqual([
    2,
  ]);
});

test("parses multiple frames with 顿号 separator", () => {
  expect(
    parseUnavailableFrames("帧 1、2、5 的画面素材不可用（共 3/10 帧）"),
  ).toEqual([0, 1, 4]);
});

test("parses frames with 等 truncation suffix", () => {
  expect(
    parseUnavailableFrames("帧 1、2 等 的画面素材不可用（共 15/30 帧）"),
  ).toEqual([0, 1]);
});

test("parses regen_failed mixed message", () => {
  const msg =
    "帧 1、2、3 的画面素材不可用（共 3/5 帧）--远程链接已过期且无本地副本，直接合成只会得到黑屏画面。其中帧 2 是上次重新生成失败--请对这些帧点「重新生成」补齐画面后再合成。";
  expect(parseUnavailableFrames(msg)).toEqual([0, 1, 2]);
});

test("parses regen_failed all message", () => {
  const msg =
    "帧 1、3 的画面素材不可用（共 2/5 帧）--上次重新生成失败，直接合成会沿用失败前的旧画面。请对这些帧点「重新生成」补齐画面后再合成。";
  expect(parseUnavailableFrames(msg)).toEqual([0, 2]);
});

test("returns empty for message without frame numbers", () => {
  expect(parseUnavailableFrames("缺音频（audio.mp3），无法重新合成")).toEqual(
    [],
  );
  expect(parseUnavailableFrames("")).toEqual([]);
  expect(parseUnavailableFrames("unknown error")).toEqual([]);
});

test("defensively parses legacy Python list repr format", () => {
  expect(
    parseUnavailableFrames("帧 [2, 5] 的旁白已修改但未重新生成配音"),
  ).toEqual([1, 4]);
});

test("deduplicates frame indices", () => {
  // If message somehow lists same frame twice
  expect(parseUnavailableFrames("帧 1、1、2 的画面素材不可用")).toEqual([0, 1]);
});
