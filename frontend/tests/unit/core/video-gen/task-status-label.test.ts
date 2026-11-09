import { describe, expect, it } from "vitest";

import {
  isTerminalVideoTaskStatus,
  VIDEO_TASK_TERMINAL_STATUSES,
} from "@/core/video-gen/task-status-label";

describe("VIDEO_TASK_TERMINAL_STATUSES（反馈1-项2）", () => {
  it("集合内容对齐后端 task_framework TERMINAL_STATUSES = {ready, failed, cancelled}", () => {
    // 事实源：backend/app/gateway/_shared/task_framework/schemas.py:39。
    // 后端新增终态（如 expired）时此处与两处消费闸须同步——宁可显式列出勿用推导。
    expect([...VIDEO_TASK_TERMINAL_STATUSES].sort()).toEqual([
      "cancelled",
      "failed",
      "ready",
    ]);
  });

  it.each(["ready", "failed", "cancelled"])("%s → 终态", (s) => {
    expect(isTerminalVideoTaskStatus(s)).toBe(true);
  });

  it.each(["pending", "running", "composing", "draft", ""])(
    "%s → 非终态",
    (s) => {
      expect(isTerminalVideoTaskStatus(s)).toBe(false);
    },
  );
});
