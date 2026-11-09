import { afterEach, describe, expect, it } from "vitest";

import { seedStreamRejoinKey } from "@/core/gallery/stream-rejoin";

function fakeWindow() {
  const store = new Map<string, string>();
  return {
    store,
    window: {
      sessionStorage: {
        setItem: (k: string, v: string) => void store.set(k, v),
        getItem: (k: string) => store.get(k) ?? null,
      },
    },
  };
}

describe("seedStreamRejoinKey", () => {
  afterEach(() => {
    delete (globalThis as Record<string, unknown>).window;
  });

  it("writes lg:stream:<threadId> = runId（SDK 重连键格式）", () => {
    const { store, window } = fakeWindow();
    (globalThis as Record<string, unknown>).window = window;
    seedStreamRejoinKey("t-1", "r-9");
    expect(store.get("lg:stream:t-1")).toBe("r-9");
  });

  it("runId 为空时不写键", () => {
    const { store, window } = fakeWindow();
    (globalThis as Record<string, unknown>).window = window;
    seedStreamRejoinKey("t-1", undefined);
    expect(store.size).toBe(0);
  });

  it("无 window（SSR）时不抛异常", () => {
    expect(() => seedStreamRejoinKey("t-1", "r-9")).not.toThrow();
  });

  it("sessionStorage 抛异常（隐私模式）时静默降级", () => {
    (globalThis as Record<string, unknown>).window = {
      sessionStorage: {
        setItem: () => {
          throw new Error("QuotaExceededError");
        },
      },
    };
    expect(() => seedStreamRejoinKey("t-1", "r-9")).not.toThrow();
  });
});
