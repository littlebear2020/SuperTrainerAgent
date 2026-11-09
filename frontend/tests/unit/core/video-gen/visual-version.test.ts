import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { expect, test } from "vitest";

import { visualUrlVersion } from "@/core/video-gen/visual-version";

test("same url + tick yields stable version (cache-friendly)", () => {
  expect(visualUrlVersion("https://tos.example.com/a.png", 0)).toBe(
    visualUrlVersion("https://tos.example.com/a.png", 0),
  );
});

test("new preview (new TOS url) changes version → browser refetches", () => {
  // 回归（2026-07-27 预览缓存不刷新）：预览本地化后同名文件被覆盖，
  // 仅靠 state 版本号无法感知；visual_url 每次重生成必变，版本必须跟着变。
  const v1 = visualUrlVersion("https://tos.example.com/img_v1.png?sig=aaa", 0);
  const v2 = visualUrlVersion("https://tos.example.com/img_v2.png?sig=bbb", 0);
  expect(v1).not.toBe(v2);
});

test("upload path (no new url) falls back to tick", () => {
  expect(visualUrlVersion(null, 3)).toBe("tick-3");
  expect(visualUrlVersion(undefined, 4)).toBe("tick-4");
  expect(visualUrlVersion(null, 3)).not.toBe(visualUrlVersion(null, 4));
});

test("FrameCard local-frame src uses visualUrlVersion, not raw thumbVersion", () => {
  // 源码级守卫（heartbeat.test.ts / download-url.test.tsx 同款模式）：
  // 防止回退到 state-only 版本号导致同名文件覆盖后不刷新。
  // （局限：字面量匹配可被空格/拆行绕过——与既有守卫同款取舍，知其有限。）
  const src = readFileSync(
    resolve(
      __dirname,
      "../../../..",
      "src/components/workspace/video-gen/video-gen-storyboard.tsx",
    ),
    "utf-8",
  );
  expect(src).toContain("visualUrlVersion(");
  expect(src).not.toContain("?v=${thumbVersion}");
});
