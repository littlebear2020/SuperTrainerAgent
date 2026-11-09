/**
 * Render-loop regression guard (React error #185, "Maximum update depth
 * exceeded" — chat page white screen after the 2026-07-24 deploy).
 *
 * The loop was a 3-link cycle, all in this repo:
 *
 *   1. useThreadStream rebuilds `mergeMessages(...)` on EVERY render, so
 *      `thread.messages` is a fresh array identity each time.
 *   2. MessageList calls useSyncSubtaskStatus(messages); its useEffect deps are
 *      [messages, updateSubtask] → the effect re-runs on every render.
 *   3. The effect calls updateSubtask() per derived task. applySubtaskPatch
 *      unconditionally spread `{...existing, ...patch}`, so setTasks always
 *      stored a NEW object → context value changes → re-render → back to (1).
 *
 * React bails out of a setState only when the next state is *identical*
 * (Object.is) to the previous. So the fix is: when a patch changes nothing
 * semantically, applySubtaskPatch must return the EXISTING object reference,
 * letting React bail out and breaking the cycle.
 *
 * These tests pin reference identity, not just field values — value-equality
 * assertions (subtask-merge.test.ts) pass happily while the app spins.
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

describe("applySubtaskPatch reference identity (loop guard)", () => {
  it("returns the SAME object when a re-derived patch changes nothing", () => {
    const existing = seed("t1");
    // deriveSubtasks() rebuilds an equal-but-new patch on every render.
    const merged = applySubtaskPatch(existing, seed("t1"));
    expect(merged).toBe(existing);
  });

  it("returns the SAME object when a terminal status would be regressed", () => {
    // The status-regression guard already clamped the value; with an
    // unconditional spread it still allocated a new object every render, which
    // is exactly the streaming-completed-subtask loop.
    const existing = seed("t2", { status: "completed", result: "ok" });
    const merged = applySubtaskPatch(existing, seed("t2"));
    expect(merged.status).toBe("completed");
    expect(merged).toBe(existing);
  });

  it("still returns a NEW object when the patch actually changes a field", () => {
    const existing = seed("t3");
    const merged = applySubtaskPatch(existing, {
      id: "t3",
      status: "completed",
      result: "done",
    });
    expect(merged).not.toBe(existing);
    expect(merged.status).toBe("completed");
    expect(merged.result).toBe("done");
    // untouched metadata still merges through
    expect(merged.subagent_type).toBe("general-purpose");
  });

  it("treats an added field as a real change", () => {
    const existing = seed("t4");
    const merged = applySubtaskPatch(existing, { id: "t4", error: "boom" });
    expect(merged).not.toBe(existing);
    expect(merged.error).toBe("boom");
  });

  it("ignores explicit-undefined patch fields instead of clobbering", () => {
    // latestMessage comes only from streaming events; a re-derivation carrying
    // latestMessage: undefined must neither clobber it nor allocate.
    const existing = seed("t5", {
      latestMessage: { id: "m1", type: "ai", content: "hi" },
    } as Partial<Subtask>);
    const merged = applySubtaskPatch(existing, {
      id: "t5",
      latestMessage: undefined,
    } as Partial<Subtask> & { id: string });
    expect(merged.latestMessage).toEqual({
      id: "m1",
      type: "ai",
      content: "hi",
    });
    expect(merged).toBe(existing);
  });

  it("seeds a brand-new task (no existing) as a new object", () => {
    const merged = applySubtaskPatch(undefined, seed("t6"));
    expect(merged.status).toBe("in_progress");
    expect(merged.id).toBe("t6");
  });
});

/**
 * The store keeps TWO identity guards and both matter:
 *   1. applySubtaskPatch returns `existing` on a no-op patch (above), and
 *   2. useUpdateSubtask returns the previous map when (1) reported a no-op.
 *
 * Guard 2 had no coverage, which a mutation check caught: reverting guard 1
 * alone left the E2E render-loop spec passing because the useMemo in
 * useThreadStream independently breaks the cycle. Any single layer can be
 * reverted without a red E2E, so each layer needs its own unit guard.
 *
 * useUpdateSubtask is a hook, and @testing-library/react is not installed
 * (repo convention — see subtask-merge.test.ts), so we exercise the exact
 * reducer body it passes to setTasks.
 */
function subtaskReducer(
  prev: Record<string, Subtask>,
  task: Partial<Subtask> & { id: string },
): Record<string, Subtask> {
  const merged = applySubtaskPatch(prev[task.id], task);
  if (prev[task.id] === merged) {
    return prev;
  }
  return { ...prev, [task.id]: merged };
}

describe("subtask map identity (useUpdateSubtask reducer)", () => {
  it("returns the SAME map when the patch is a no-op", () => {
    const prev = { t1: seed("t1") };
    expect(subtaskReducer(prev, seed("t1"))).toBe(prev);
  });

  it("returns a NEW map when a field actually changes", () => {
    const prev = { t1: seed("t1") };
    const next = subtaskReducer(prev, { id: "t1", status: "completed" });
    expect(next).not.toBe(prev);
    expect(next.t1?.status).toBe("completed");
    // other entries are carried over untouched
    expect(Object.keys(next)).toEqual(["t1"]);
  });

  it("returns a NEW map when an unseen task is added", () => {
    const prev = { t1: seed("t1") };
    const next = subtaskReducer(prev, seed("t2"));
    expect(next).not.toBe(prev);
    expect(Object.keys(next).sort()).toEqual(["t1", "t2"]);
    // untouched entry keeps its reference
    expect(next.t1).toBe(prev.t1);
  });

  it("stays stable across repeated identical re-derivations", () => {
    // This is the actual production loop shape: useSyncSubtaskStatus re-derives
    // an equal patch on every render. The map must stop changing after the
    // first write, otherwise the context value churns forever.
    let map: Record<string, Subtask> = {};
    map = subtaskReducer(map, seed("t1"));
    const afterFirstWrite = map;
    for (let i = 0; i < 5; i++) {
      map = subtaskReducer(map, seed("t1"));
    }
    expect(map).toBe(afterFirstWrite);
  });
});
