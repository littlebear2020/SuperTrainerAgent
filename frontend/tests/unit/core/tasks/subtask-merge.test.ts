/**
 * Tests for the pure subtask-merge decision extracted from useUpdateSubtask.
 *
 * @testing-library/react is not installed (Vitest runs in Node); per the
 * repo convention (see tests/unit/core/writer/hooks.test.ts) the merge logic
 * is extracted into a pure applySubtaskPatch(existing, patch) we can invoke
 * directly. This pins: terminal status persists, and is never regressed back
 * to in_progress by a late re-derivation (e.g. useSyncSubtaskStatus before the
 * tool-result message streams in).
 */
import { describe, expect, it } from "vitest";

import { applySubtaskPatch } from "@/core/tasks/context";
import type { Subtask } from "@/core/tasks/types";

function seed(id: string, overrides: Partial<Subtask> = {}): Subtask {
  return {
    id,
    status: "in_progress",
    subagent_type: "general-purpose",
    description: "d",
    prompt: "p",
    ...overrides,
  };
}

describe("applySubtaskPatch", () => {
  it("merges a terminal patch onto a seeded task", () => {
    const merged = applySubtaskPatch(seed("t1"), {
      id: "t1",
      status: "completed",
      result: "done",
    });
    expect(merged.status).toBe("completed");
    expect(merged.result).toBe("done");
    // metadata preserved
    expect(merged.subagent_type).toBe("general-purpose");
  });

  it("seeds a new task when none exists", () => {
    const merged = applySubtaskPatch(undefined, {
      id: "t1",
      status: "in_progress",
      subagent_type: "general-purpose",
      description: "d",
      prompt: "p",
    });
    expect(merged.status).toBe("in_progress");
    expect(merged.subagent_type).toBe("general-purpose");
  });

  it("metadata-only patch (uncertain status): seeds a new task as in_progress, keeps an existing terminal status", () => {
    // PR3：history loading / 断线 / 未知 run 时不判终态，patch 不带 status。
    // 新任务不能没有 status（卡片要渲染），按旧调用方行为记为进行中；
    // 已有任务的状态原样保留——不会被改写也不会被回退
    const seeded = applySubtaskPatch(undefined, {
      id: "t-new",
      description: "d",
    });
    expect(seeded.status).toBe("in_progress");

    const existing = seed("t1", { status: "cancelled" });
    expect(
      applySubtaskPatch(existing, { id: "t1", description: "d2" }),
    ).toEqual({
      ...existing,
      description: "d2",
    });
  });

  it("metadata-only patch (uncertain status): seeds a new task as in_progress, keeps an existing terminal status", () => {
    // PR3：history loading / 断线 / 未知 run 时不判终态，patch 不带 status。
    // 新任务不能没有 status（卡片要渲染），按旧调用方行为记为进行中；
    // 已有任务的状态原样保留——不会被改写也不会被回退
    const seeded = applySubtaskPatch(undefined, {
      id: "t-new",
      description: "d",
    });
    expect(seeded.status).toBe("in_progress");

    const existing = seed("t1", { status: "cancelled" });
    expect(
      applySubtaskPatch(existing, { id: "t1", description: "d2" }),
    ).toEqual({
      ...existing,
      description: "d2",
    });
  });

  it("does NOT regress a terminal status back to in_progress", () => {
    // onCustomEvent marked it completed; a later full-metadata in_progress
    // re-derivation (useSyncSubtaskStatus) must not clobber the status.
    const existing = seed("t2", { status: "completed", result: "ok" });
    const merged = applySubtaskPatch(existing, seed("t2")); // status: in_progress + full metadata
    expect(merged.status).toBe("completed");
    // but metadata still merges through
    expect(merged.subagent_type).toBe("general-purpose");
  });

  it("allows a real re-creation (full patch) to seed even when in_progress", () => {
    // No existing task → the first full patch seeds regardless of status.
    const merged = applySubtaskPatch(undefined, seed("t3"));
    expect(merged.status).toBe("in_progress");
  });
});
