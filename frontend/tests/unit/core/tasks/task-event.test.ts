import { describe, expect, it } from "vitest";

import { applySubtaskPatch } from "@/core/tasks/context";
import { mapTaskEvent } from "@/core/tasks/task-event";
import type { Subtask } from "@/core/tasks/types";

describe("mapTaskEvent", () => {
  it("maps task_completed", () => {
    expect(
      mapTaskEvent({ type: "task_completed", task_id: "t1", result: "ok" }),
    ).toEqual({
      id: "t1",
      status: "completed",
      result: "ok",
    });
  });

  it("maps task_failed with error", () => {
    expect(
      mapTaskEvent({ type: "task_failed", task_id: "t1", error: "boom" }),
    ).toEqual({
      id: "t1",
      status: "failed",
      error: "boom",
    });
  });

  it("maps task_timed_out with type fallback when error missing", () => {
    expect(mapTaskEvent({ type: "task_timed_out", task_id: "t1" })).toEqual({
      id: "t1",
      status: "failed",
      error: "task_timed_out",
    });
  });

  it("maps task_cancelled", () => {
    expect(
      mapTaskEvent({ type: "task_cancelled", task_id: "t1", error: "user" }),
    ).toEqual({
      id: "t1",
      status: "failed",
      error: "user",
    });
  });

  it("maps task_running to a latestMessage patch", () => {
    const msg = { content: "working" } as never;
    expect(
      mapTaskEvent({ type: "task_running", task_id: "t1", message: msg }),
    ).toEqual({
      id: "t1",
      status: "in_progress",
      latestMessage: msg,
    });
  });

  it("task_running 首补丁即带 status: in_progress（throttle 配套，1.5）", () => {
    // throttle 合帧下 custom 事件可先于 derive 通知到达 store——首补丁若不带
    // status，后续 derive 的 in_progress 会被终态守卫误删，子任务卡全程无转圈。
    const msg = { content: "spin" } as never;
    const patch = mapTaskEvent({
      type: "task_running",
      task_id: "t1",
      message: msg,
    });
    expect(patch?.status).toBe("in_progress");
  });

  it("returns null for unknown events", () => {
    expect(mapTaskEvent({ type: "llm_retry", message: "x" })).toBeNull();
  });

  it("returns null when task_id is missing", () => {
    expect(mapTaskEvent({ type: "task_completed", result: "ok" })).toBeNull();
  });
});

describe("task_running 钉 status 与终态守卫共存（1.5 配套）", () => {
  it("无 status 的既有条目收到 task_running 补丁后为 in_progress（不再依赖 derive）", () => {
    const patch = mapTaskEvent({
      type: "task_running",
      task_id: "t1",
      message: { content: "x" } as never,
    });
    // 从空 store 首建：status 直接落 in_progress，转圈可见
    const created = applySubtaskPatch(undefined, patch!);
    expect(created.status).toBe("in_progress");
  });

  it("终态守卫对 completed 仍生效：晚到的 in_progress 补丁不回退状态", () => {
    const completed: Subtask = {
      id: "t1",
      status: "completed",
      subagent_type: "researcher",
      description: "d",
      prompt: "p",
      result: "done",
    };
    const guarded = applySubtaskPatch(completed, {
      id: "t1",
      status: "in_progress",
    });
    expect(guarded.status).toBe("completed");
    expect(guarded).toBe(completed); // 无变化时返回原引用（#185 不变量）
  });
});
