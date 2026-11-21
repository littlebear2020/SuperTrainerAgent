import { beforeEach, describe, expect, it, vi } from "vitest";

import { fetch } from "@/core/api/fetcher";
import { formatInteger as consoleFormatInteger } from "@/core/console/format";
import { fetchPersonalUsage } from "@/core/usage/api";
import type { PersonalUsageResponse } from "@/core/usage/types";
import {
  formatCost,
  formatInteger,
  formatUsageCost,
} from "@/core/utils/format";

vi.mock("@/core/api/fetcher", () => ({ fetch: vi.fn() }));

const response: PersonalUsageResponse = {
  window: { days: 7, start_date: "2026-11-15", end_date: "2026-11-21" },
  summary: {
    cost: 1.5,
    currency: "CNY",
    cost_source: "real",
    total_tokens: 100,
  },
  tasks: [
    {
      task_key: "agent:thread-1",
      source_id: "thread-1",
      capability: "course-video",
      title: "真实费用",
      status: "completed",
      cost: 1.5,
      llm_cost: 1.5,
      media_cost: null,
      currency: "CNY",
      cost_source: "real",
      total_tokens: 100,
      models: [
        {
          name: "real-model",
          input_tokens: 60,
          output_tokens: 40,
          cache_read_tokens: null,
          total_tokens: 100,
          cost: 1.5,
          cost_source: "real",
        },
      ],
      updated_at: "2026-11-21T13:00:00Z",
    },
  ],
  total_tasks: 1,
  has_more: false,
};

describe("personal usage API and formatting", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(response),
    } as Response);
  });

  it.each([7, 14, 30] as const)(
    "sends every query parameter for %s days",
    async (days) => {
      const result = await fetchPersonalUsage({
        days,
        capability: "ppt-master",
        offset: 40,
        limit: 20,
        tzOffsetMinutes: 480,
      });

      const [url] = vi.mocked(fetch).mock.calls[0]!;
      if (typeof url !== "string")
        throw new Error("expected a string request URL");
      const parsed = new URL(url, "http://local");
      expect(parsed.pathname).toBe("/api/usage");
      expect(Object.fromEntries(parsed.searchParams)).toEqual({
        days: String(days),
        capability: "ppt-master",
        offset: "40",
        limit: "20",
        tz_offset_minutes: "480",
      });
      expect(result.summary.cost_source).toBe("real");
      expect(result.summary.currency).toBe("CNY");
      expect(result.summary.cost).toBeCloseTo(1.5, 8);
      expect(result.tasks[0]!.models[0]!.cache_read_tokens).toBeNull();
    },
  );

  it("keeps unknown, real zero, and the console integer re-export distinct", () => {
    expect(formatInteger(null)).toBe("—");
    expect(formatInteger(response.tasks[0]!.models[0]!.cache_read_tokens)).toBe(
      "—",
    );
    expect(formatInteger(0)).toBe("0");
    expect(formatInteger(123456789)).toBe("123,456,789");
    expect(consoleFormatInteger).toBe(formatInteger);
    expect(formatCost(null, "CNY")).toBe("—");
    expect(formatCost(0, "CNY")).toBe("¥0.00");
    expect(formatCost(response.summary.cost, response.summary.currency)).toBe(
      "¥1.50",
    );
  });

  it("does not round a known positive usage cost to displayed zero", () => {
    expect(formatUsageCost(0.00001, "CNY")).toBe("<¥0.0001");
    expect(formatUsageCost(0, "CNY")).toBe("¥0.00");
    expect(formatUsageCost(null, "CNY")).toBe("—");
  });
});
