import { describe, expect, test } from "vitest";

import { toastableCustomEventMessage } from "@/core/threads/hooks";

describe("toastableCustomEventMessage（R3 空响应事件 + llm_retry 白名单路由）", () => {
  test("llm_retry 保留既有行为：后端中文 message 直出", () => {
    expect(
      toastableCustomEventMessage({
        type: "llm_retry",
        message: "模型调用失败，正在重试（第 1/3 次）…",
      }),
    ).toBe("模型调用失败，正在重试（第 1/3 次）…");
  });

  test("llm_empty_retry：空响应自动重试提示（含「第 N/M 次」）", () => {
    const msg = "模型空响应，自动重试中（第 2/3 次）…";
    expect(
      toastableCustomEventMessage({
        type: "llm_empty_retry",
        attempt: 2,
        max_retries: 3,
        message: msg,
      }),
    ).toBe(msg);
  });

  test("llm_empty_fallback：兜底内容提示", () => {
    const msg = "抱歉，模型多次返回空响应，已保留现场。";
    expect(
      toastableCustomEventMessage({ type: "llm_empty_fallback", message: msg }),
    ).toBe(msg);
  });

  test("白名单外的 custom 事件不 toast（task_* 等走 mapTaskEvent）", () => {
    expect(
      toastableCustomEventMessage({ type: "task_started", task_id: "t1" }),
    ).toBeNull();
    expect(
      toastableCustomEventMessage({ type: "llm_slow", message: "慢" }),
    ).toBeNull();
  });

  test("message 缺失 / 非字符串 / 空白 → null", () => {
    expect(toastableCustomEventMessage({ type: "llm_empty_retry" })).toBeNull();
    expect(
      toastableCustomEventMessage({ type: "llm_retry", message: 42 }),
    ).toBeNull();
    expect(
      toastableCustomEventMessage({ type: "llm_retry", message: "   " }),
    ).toBeNull();
  });

  test("非对象 / null / 无 type → null", () => {
    expect(toastableCustomEventMessage("llm_retry")).toBeNull();
    expect(toastableCustomEventMessage(null)).toBeNull();
    expect(toastableCustomEventMessage({ message: "x" })).toBeNull();
  });
});
