import { expect, test } from "vitest";

import { recomposeActionFor } from "@/core/video-gen/resume-actions";

test("failed + 无编辑 → 可点「重试合成」（豁免 dirty_frames 门控）", () => {
  // 修前:分镜条按钮 disabled=dirty_frames.length===0 → failed+无编辑灰死,
  // 渲染失败任务只剩删除一条路(vg_f74c8fa2331a:95 张图在盘上却无从续跑)
  const a = recomposeActionFor({
    status: "failed",
    task_type: "ai_image",
    dirty_frames: [],
  });
  expect(a).toEqual({ visible: true, disabled: false, label: "重试合成" });
});

test("failed + dirty_frames 缺省(undefined)→ 仍可点", () => {
  const a = recomposeActionFor({ status: "failed", task_type: "ai_video" });
  expect(a.visible).toBe(true);
  expect(a.disabled).toBe(false);
});

test("ready + 无编辑 → 展示但 disabled（旧行为不变）", () => {
  const a = recomposeActionFor({
    status: "ready",
    task_type: "ai_image",
    dirty_frames: [],
  });
  expect(a).toEqual({ visible: true, disabled: true, label: "重新合成" });
});

test("ready + 有编辑 → 可点「重新合成」（旧行为不变）", () => {
  const a = recomposeActionFor({
    status: "ready",
    task_type: "ai_image",
    dirty_frames: [1, 3],
  });
  expect(a).toEqual({ visible: true, disabled: false, label: "重新合成" });
});

test("vimax/remotion 的 failed → 不展示（recompose 仅支持 segments 管线）", () => {
  expect(
    recomposeActionFor({ status: "failed", task_type: "vimax" }).visible,
  ).toBe(false);
  expect(
    recomposeActionFor({ status: "failed", task_type: "remotion" }).visible,
  ).toBe(false);
});

test("ai_visual 属 segments 管线：failed → visible（防未来误判为非 segments）", () => {
  // types.ts:10 task_type 含 ai_visual;后端 _RECOMPOSE_ALLOWED_TYPES 同样含 ai_visual
  // (routers/video_gen.py:921)。若未来有人把 segments 判断改成 ["ai_image","ai_video"]
  // 白名单,ai_visual 的「重试合成」会静默消失(后端支持但前端藏按钮)→ 钉死。
  const a = recomposeActionFor({
    status: "failed",
    task_type: "ai_visual",
    dirty_frames: [],
  });
  expect(a).toEqual({ visible: true, disabled: false, label: "重试合成" });
});

test("draft/running/composing/pending → 不展示", () => {
  for (const status of ["draft", "running", "composing", "pending"]) {
    expect(recomposeActionFor({ status, task_type: "ai_image" }).visible).toBe(
      false,
    );
  }
});

test("ready + no dirty frames + subtitle_locked → recompose enabled (字幕编辑后最后一环)", () => {
  // 回归（2026-07-28 服务器实测）：PUT /subtitle 置锁不产 dirty_frames，
  // 按钮曾恒 disabled，新字幕烧不进成片。
  const action = recomposeActionFor(
    { status: "ready", task_type: "ai_image", dirty_frames: [] },
    { subtitleLocked: true },
  );
  expect(action.visible).toBe(true);
  expect(action.disabled).toBe(false);
  expect(action.label).toBe("重新合成");
});

test("ready + no dirty frames + no lock → still disabled (现行为锁定)", () => {
  const action = recomposeActionFor(
    { status: "ready", task_type: "ai_image", dirty_frames: [] },
    { subtitleLocked: false },
  );
  expect(action.disabled).toBe(true);
});

test("subtitleLocked does not change failed/vimax branches", () => {
  expect(
    recomposeActionFor(
      { status: "failed", task_type: "ai_image", dirty_frames: [] },
      { subtitleLocked: true },
    ).label,
  ).toBe("重试合成");
  expect(
    recomposeActionFor(
      { status: "ready", task_type: "vimax", dirty_frames: [] },
      { subtitleLocked: true },
    ).visible,
  ).toBe(false);
});
