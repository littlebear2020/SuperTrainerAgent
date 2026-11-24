import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

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

describe("cancelled 终态停轮询（E4）", () => {
  const storageWrite = vi.fn();

  beforeEach(() => {
    // node 档没有 Web Storage：装上记录桩，任何写入都会被断言抓到。
    storageWrite.mockReset();
    const storage = {
      setItem: storageWrite,
      getItem: storageWrite,
      removeItem: storageWrite,
      clear: storageWrite,
    };
    vi.stubGlobal("sessionStorage", storage);
    vi.stubGlobal("localStorage", storage);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  test("cancelled 立即停轮询，pollWhile 为真也不继续", () => {
    expect(videoTaskRefetchInterval("cancelled")).toBe(false);
    expect(videoTaskRefetchInterval("cancelled", () => true)).toBe(false);
  });

  test.each(["ready", "failed"])(
    "%s 无 grace 停止、有 grace 维持 2 秒",
    (status) => {
      expect(videoTaskRefetchInterval(status)).toBe(false);
      expect(videoTaskRefetchInterval(status, () => false)).toBe(false);
      expect(videoTaskRefetchInterval(status, () => true)).toBe(2000);
    },
  );

  // 轮询决策与任务类型无关：本批不为任何类型提前删除 legacy recompose grace。
  test.each(["ai_image", "ai_video", "ai_visual", "remotion_course", "vimax"])(
    "%s 的 legacy recompose grace 保持（ready/failed + grace 仍轮询）",
    () => {
      expect(videoTaskRefetchInterval("ready", () => true)).toBe(2000);
      expect(videoTaskRefetchInterval("failed", () => true)).toBe(2000);
    },
  );

  test.each(["running", "pending", "composing", "qc_repair"])(
    "%s 等活动态保持 2 秒轮询",
    (status) => {
      expect(videoTaskRefetchInterval(status)).toBe(2000);
      expect(videoTaskRefetchInterval(status, () => true)).toBe(2000);
      expect(videoTaskRefetchInterval(status, () => false)).toBe(2000);
    },
  );

  test("轮询决策不写 sessionStorage / localStorage", () => {
    for (const status of [
      "cancelled",
      "ready",
      "failed",
      "running",
      undefined,
    ]) {
      videoTaskRefetchInterval(status, () => true);
      videoTaskRefetchInterval(status, () => false);
    }
    expect(storageWrite).not.toHaveBeenCalled();
  });

  test("video-gen 不含 accepted-operation 实现，也不碰 Web Storage", () => {
    const coreDir = join(process.cwd(), "src/core/video-gen");
    const hooksSrc = readFileSync(join(coreDir, "hooks.ts"), "utf-8");
    expect(hooksSrc).not.toMatch(/sessionStorage|localStorage/);
    expect(existsSync(join(coreDir, "accepted-operation.ts"))).toBe(false);
    expect(hooksSrc).not.toContain("accepted-operation");
  });

  test("详情页的 recomposeGraceUntilRef 宽限仍在（不得随终态修复删除）", () => {
    const detailSrc = readFileSync(
      join(
        process.cwd(),
        "src/components/workspace/video-gen/video-gen-detail.tsx",
      ),
      "utf-8",
    );
    expect(detailSrc).toContain("recomposeGraceUntilRef");
    expect(detailSrc).toMatch(/pollWhile:\s*\(\)\s*=>/);
  });
});
