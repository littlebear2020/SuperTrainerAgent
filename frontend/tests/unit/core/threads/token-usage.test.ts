/**
 * D3 修订（2026-09-23 review #4）：thread token-usage API client。
 * 断言：端点字段 → TokenUsage 命名映射；不可用形态（缺 total_tokens /
 * 非 2xx / 网络错误）一律 null（回落消息聚合，不抛）。
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

import { fetchThreadTokenUsage } from "@/core/threads/token-usage";

const fetchMock = vi.hoisted(() => vi.fn());

vi.mock("@/core/api/fetcher", () => ({ fetch: fetchMock }));
vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://localhost:8001",
}));

const okBody = {
  thread_id: "t-1",
  total_tokens: 15_000_000,
  total_input_tokens: 10_000_000,
  total_output_tokens: 5_000_000,
};

function res(body: unknown, ok = true) {
  return { ok, json: async () => body } as Response;
}

beforeEach(() => {
  fetchMock.mockReset();
});

describe("fetchThreadTokenUsage", () => {
  it("maps the endpoint aggregate onto TokenUsage-shaped fields", async () => {
    fetchMock.mockResolvedValueOnce(res(okBody));
    const usage = await fetchThreadTokenUsage("t-1");
    expect(usage).toEqual({
      threadId: "t-1",
      inputTokens: 10_000_000,
      outputTokens: 5_000_000,
      totalTokens: 15_000_000,
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8001/api/threads/t-1/token-usage",
    );
  });

  it("returns null when total_tokens is missing (fresh thread, no runs)", async () => {
    fetchMock.mockResolvedValueOnce(res({ thread_id: "t-2" }));
    expect(await fetchThreadTokenUsage("t-2")).toBeNull();
  });

  it("returns null on non-2xx", async () => {
    fetchMock.mockResolvedValueOnce(res({ detail: "x" }, false));
    expect(await fetchThreadTokenUsage("t-3")).toBeNull();
  });

  it("returns null on network failure (silent fallback, never throws)", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("fetch failed"));
    expect(await fetchThreadTokenUsage("t-4")).toBeNull();
  });
});
