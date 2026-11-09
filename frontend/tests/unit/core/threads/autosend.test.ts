import { describe, expect, it } from "vitest";

import { shouldAutoSend } from "@/core/threads/autosend";

describe("shouldAutoSend", () => {
  it("新线程 + autosend → 发送（gallery 模板卡既有路径）", () => {
    expect(
      shouldAutoSend({
        autosend: true,
        text: "hi",
        isNewThread: true,
        allowExistingThread: false,
      }),
    ).toBe(true);
  });

  it("既有线程 + 无 existingThread 标记 → 不发送（保持旧守卫行为）", () => {
    expect(
      shouldAutoSend({
        autosend: true,
        text: "hi",
        isNewThread: false,
        allowExistingThread: false,
      }),
    ).toBe(false);
  });

  it("既有线程 + existingThread 标记 → 发送（灵感库文件上传流）", () => {
    expect(
      shouldAutoSend({
        autosend: true,
        text: "hi",
        isNewThread: false,
        allowExistingThread: true,
      }),
    ).toBe(true);
  });

  it("autosend=false 或空文本 → 不发送", () => {
    expect(
      shouldAutoSend({
        autosend: false,
        text: "hi",
        isNewThread: true,
        allowExistingThread: true,
      }),
    ).toBe(false);
    expect(
      shouldAutoSend({
        autosend: true,
        text: "",
        isNewThread: true,
        allowExistingThread: true,
      }),
    ).toBe(false);
  });
});
