import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import {
  clearFramePreviewError,
  clearFramePreviewState,
  failedPreviewCount,
  frameVisualFingerprint,
  isLegacyBackendPreviewUpdate,
  isPreviewResolved,
  isPreviewTimedOut,
  pendingPreviewFrames,
  unresolvedPreviewCount,
  PREVIEW_TIMEOUT_MS,
} from "@/core/video-gen/previewAll";

describe("pendingPreviewFrames (M1-2)", () => {
  it("returns indices of frames lacking visual_url", () => {
    const frames = [
      { index: 0, visual_url: "https://x/0.png" },
      { index: 1 },
      { index: 2, visual_url: null },
      { index: 3 },
    ];
    expect(pendingPreviewFrames(frames)).toEqual([1, 2, 3]);
  });

  it("returns empty when all frames have visual_url", () => {
    expect(
      pendingPreviewFrames([
        { index: 0, visual_url: "a" },
        { index: 1, visual_url: "b" },
      ]),
    ).toEqual([]);
  });
});

describe("isPreviewResolved（preview_state 状态机，2026-09-07）", () => {
  it("核心修复：有旧 visual_url 但 preview_state 缺失 → 未结案（重生旧图帧不再点击瞬间误结案）", () => {
    expect(
      isPreviewResolved({ index: 0, visual_url: "http://x/old.png" }),
    ).toBe(false);
  });
  it("有旧 visual_url + running → 未结案（生成中）", () => {
    expect(
      isPreviewResolved({
        index: 0,
        visual_url: "http://x/old.png",
        preview_state: "running",
      }),
    ).toBe(false);
  });
  it("done → 结案", () => {
    expect(isPreviewResolved({ index: 0, preview_state: "done" })).toBe(true);
  });
  it("error → 结案（失败也算结案，停止转圈）", () => {
    expect(isPreviewResolved({ index: 0, preview_state: "error" })).toBe(true);
  });
  it("无 state 但有 preview_error → 结案（存量/批量兜底腿）", () => {
    expect(
      isPreviewResolved({ index: 0, preview_error: "RuntimeError: boom" }),
    ).toBe(true);
  });
  it("皆无 / undefined → 未结案", () => {
    expect(isPreviewResolved({ index: 0 })).toBe(false);
    expect(isPreviewResolved(undefined)).toBe(false);
  });
});

describe("isPreviewTimedOut（后台预览超时兜底）", () => {
  it("超过 300s → true", () => {
    expect(isPreviewTimedOut(1000, 1000 + PREVIEW_TIMEOUT_MS + 1)).toBe(true);
  });
  it("未超 → false", () => {
    expect(isPreviewTimedOut(1000, 1000 + 60_000)).toBe(false);
  });
});

describe("clearFramePreviewError（重试时乐观清旧错）", () => {
  it("清除目标帧 preview_error；其他帧不动；原对象不可变", () => {
    const data = {
      title: "t",
      frames: [
        { index: 0, preview_error: "old boom" as string | null },
        { index: 1, preview_error: "keep" as string | null },
      ],
    };
    const next = clearFramePreviewError(data, 0);
    expect(next?.frames?.[0]?.preview_error).toBeNull();
    expect(next?.frames?.[1]?.preview_error).toBe("keep");
    expect(data.frames[0]?.preview_error).toBe("old boom"); // 不可变
    // 清除后 isPreviewResolved 变 false（无 visual_url）→ spinner 持续
    expect(isPreviewResolved(next?.frames?.[0])).toBe(false);
  });

  it("undefined / 无 frames 安全返回", () => {
    expect(clearFramePreviewError(undefined, 0)).toBeUndefined();
    const noFrames = { title: "t" } as { title: string; frames?: never[] };
    expect(clearFramePreviewError(noFrames, 0)).toBe(noFrames);
  });
});

describe("clearFramePreviewState（重生时乐观清旧状态，2026-09-07）", () => {
  it("清除目标帧 preview_state；其他帧不动；原对象不可变", () => {
    const data = {
      title: "t",
      frames: [
        { index: 0, preview_state: "done" as string | null },
        { index: 1, preview_state: "running" as string | null },
      ],
    };
    const next = clearFramePreviewState(data, 0);
    expect(next?.frames?.[0]?.preview_state).toBeNull();
    expect(next?.frames?.[1]?.preview_state).toBe("running");
    expect(data.frames[0]?.preview_state).toBe("done"); // 不可变
    // 双清后（error+state 皆清）不再误结案 → spinner 持续到新终态
    expect(isPreviewResolved(next?.frames?.[0])).toBe(false);
  });

  it("undefined / 无 frames 安全返回", () => {
    expect(clearFramePreviewState(undefined, 0)).toBeUndefined();
    const noFrames = { title: "t" } as { title: string; frames?: never[] };
    expect(clearFramePreviewState(noFrames, 0)).toBe(noFrames);
  });
});

describe("failedPreviewCount (2026-07-28 T4 批量预览 UX)", () => {
  it("counts frames with preview_error and no visual", () => {
    // 语义：preview_error 非空且无 visual_url（有图帧即使曾有 error 也不算失败）
    expect(
      failedPreviewCount([
        { index: 0, visual_url: "https://x/0.png" },
        { index: 1, preview_error: "429 rate limit" },
        { index: 2, preview_error: "timeout", visual_url: "https://x/2.png" },
        { index: 3 },
      ]),
    ).toBe(1);
  });
});

describe("unresolvedPreviewCount (2026-07-28 评审修复：批量结案判定)", () => {
  it("counts only frames with neither visual nor error (失败帧也算结案)", () => {
    // 回归：批量结案曾用 pendingPreviewCount===0（!visual_url），
    // 失败帧（preview_error 无图）永远计入 pending → 批量永不结案，
    // 5 分钟后误报「批量预览超时」（其余帧早已出结果）。
    expect(
      unresolvedPreviewCount([
        { index: 0, visual_url: "https://x/0.png" }, // 成功 → 已结案
        { index: 1, preview_error: "429" }, // 失败 → 也算结案
        { index: 2, preview_error: "timeout", visual_url: "https://x/2.png" },
        { index: 3 }, // 无图无 error → 真未结案
        { index: 4 },
      ]),
    ).toBe(2);
    expect(unresolvedPreviewCount([])).toBe(0);
  });
});

describe("bulk stall watchdog wiring (2026-07-28 评审二轮 Major)", () => {
  it("storyboard.tsx stall watchdog uses unresolvedPreviewCount as progress signal", () => {
    // 回归：评审 #1 只修了结案判定（unresolvedPreviewCount===0），停滞兜底仍看
    // pendingPreviewCount（!visual_url，失败帧不让它下降）——429/长重试下帧逐一
    // 失败（有进展）却在最后一帧落定前误报「批量预览超时」并提前停轮询。
    // 结案与停滞的进展信号必须同源。源码级守卫（heartbeat/download-url 同款模式）。
    const src = readFileSync(
      resolve(
        __dirname,
        "../../../..",
        "src/components/workspace/video-gen/video-gen-storyboard.tsx",
      ),
      "utf-8",
    );
    expect(src).not.toContain(
      "bulkPrevPendingCountRef.current = pendingPreviewCount",
    );
    // 结案与停滞进展信号同源：均消费 unresolvedPreviewCount 的产物（unresolvedCnt）
    expect(src).toContain("unresolvedCnt === 0");
    expect(src).toContain("unresolvedCnt !== bulkPrevPendingCountRef.current");
  });
});

describe("上传帧保护", () => {
  const uploaded = {
    index: 0,
    visual_url: null,
    local_visual_path: "visuals/frame_000.png",
  };
  const pendingFrame = { index: 1, visual_url: null };
  const localizedAI = {
    index: 2,
    visual_url: "https://cdn/x.png",
    local_visual_path: "visuals/img_0002.png",
  };

  it("上传帧不计入待预览（否则会被 AI 图覆盖）", () => {
    expect(pendingPreviewFrames([uploaded, pendingFrame, localizedAI])).toEqual(
      [1],
    );
  });

  it("上传帧不计入未结案（否则批量永不结案、5 分钟误报超时）", () => {
    expect(unresolvedPreviewCount([uploaded, pendingFrame])).toBe(1);
  });

  it("上传帧不计入失败数（上传不清 preview_error，否则按钮说可重试却点不动）", () => {
    const uploadedAfterFailedPreview = {
      ...uploaded,
      preview_error: "上次生图失败",
    };
    expect(failedPreviewCount([uploadedAfterFailedPreview])).toBe(0);
  });

  it("本地化的 AI 图（img_ 前缀）仍可重新预览——不得误判为用户素材", () => {
    const localizedNoUrl = {
      index: 3,
      visual_url: null,
      local_visual_path: "visuals/img_0003.png",
    };
    expect(pendingPreviewFrames([localizedNoUrl])).toEqual([3]);
  });
});

describe("isLegacyBackendPreviewUpdate（条目 34：旧后端探测）", () => {
  it("指纹变化且 preview_state 缺席 → 旧后端已写回新图（结案）", () => {
    const baseline = frameVisualFingerprint({
      index: 0,
      visual_url: "http://x/old.png",
    });
    expect(
      isLegacyBackendPreviewUpdate(baseline, {
        index: 0,
        visual_url: "http://x/new.png",
      }),
    ).toBe(true);
  });

  it("local_visual_path 变化同样算指纹变化（预览图本地化路径换新）", () => {
    const baseline = frameVisualFingerprint({
      index: 0,
      local_visual_path: "visuals/img_0001.png",
    });
    expect(
      isLegacyBackendPreviewUpdate(baseline, {
        index: 0,
        local_visual_path: "visuals/img_0002.png",
      }),
    ).toBe(true);
  });

  it("preview_state 存在（新后端）→ 恒 false（走正常状态机结案，含 running）", () => {
    const baseline = frameVisualFingerprint({ index: 0 });
    expect(
      isLegacyBackendPreviewUpdate(baseline, {
        index: 0,
        visual_url: "http://x/new.png",
        preview_state: "running",
      }),
    ).toBe(false);
    expect(
      isLegacyBackendPreviewUpdate(baseline, {
        index: 0,
        preview_state: "done",
      }),
    ).toBe(false);
  });

  it("指纹未变（旧后端尚未写回）→ false（继续等，不误结案）", () => {
    const baseline = frameVisualFingerprint({
      index: 0,
      visual_url: "http://x/old.png",
    });
    expect(
      isLegacyBackendPreviewUpdate(baseline, {
        index: 0,
        visual_url: "http://x/old.png",
      }),
    ).toBe(false);
  });

  it("无基线（未 track）或帧缺失 → false", () => {
    expect(isLegacyBackendPreviewUpdate(undefined, { index: 0 })).toBe(false);
    const baseline = frameVisualFingerprint({ index: 0 });
    expect(isLegacyBackendPreviewUpdate(baseline, undefined)).toBe(false);
  });
});
