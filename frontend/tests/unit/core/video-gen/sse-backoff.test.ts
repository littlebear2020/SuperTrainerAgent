import { describe, expect, it } from "vitest";

import { MAX_SSE_RETRIES, nextBackoffMs } from "@/core/video-gen/sse-backoff";

describe("nextBackoffMs", () => {
  it("grows exponentially and caps at 30s", () => {
    expect(nextBackoffMs(0)).toBe(1000);
    expect(nextBackoffMs(1)).toBe(2000);
    expect(nextBackoffMs(2)).toBe(4000);
    expect(nextBackoffMs(10)).toBe(30_000);
  });
});

describe("MAX_SSE_RETRIES", () => {
  it("is bounded so a dead endpoint does not reconnect forever", () => {
    expect(MAX_SSE_RETRIES).toBeGreaterThanOrEqual(5);
    expect(MAX_SSE_RETRIES).toBeLessThanOrEqual(20);
  });
});
