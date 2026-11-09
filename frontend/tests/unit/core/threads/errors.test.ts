import { describe, expect, it } from "vitest";

import { friendlySendErrorMessage } from "@/core/threads/errors";

describe("friendlySendErrorMessage", () => {
  it("409 → 生成中提示", () => {
    expect(friendlySendErrorMessage({ status: 409 })).toBe(
      "当前对话正在生成中，请等待完成后再发送",
    );
  });

  it("message 含 already has an active run → 同样命中（SDK 错误无 status 字段的形态）", () => {
    expect(
      friendlySendErrorMessage(
        new Error("Thread t-1 already has an active run"),
      ),
    ).toBe("当前对话正在生成中，请等待完成后再发送");
  });

  it("其他错误 → null（走默认文案）", () => {
    expect(friendlySendErrorMessage({ status: 500 })).toBeNull();
    expect(friendlySendErrorMessage(new Error("network"))).toBeNull();
    expect(friendlySendErrorMessage(null)).toBeNull();
  });
});
