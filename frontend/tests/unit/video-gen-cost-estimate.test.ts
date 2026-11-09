import { describe, expect, it } from "vitest";

import {
  formatCostEstimate,
  shouldRequestCostEstimate,
} from "@/core/video-gen/cost-estimate";

describe("formatCostEstimate", () => {
  const base = {
    currency: "CNY",
    basis: "price_table" as const,
    n_shots: 8,
    video_seconds: 64,
    n_characters: 1,
    breakdown: {},
    unpriced: [],
    low_cny: 31.97,
    high_cny: 53.29,
    point_cny: 42.63,
    spent_cny: 3.54,
  };

  it("formats duration, rounded range and spent", () => {
    const got = formatCostEstimate(base);
    expect(got.durationLine).toBe("预计成片 ≈ 8 镜 × 8 秒 = 64 秒");
    expect(got.costLine).toBe("预计费用 ¥32–53");
    expect(got.spentLine).toBe("草稿已花费 ¥3.54");
    expect(got.partialNote).toBeNull();
    expect(got.disclaimer).toBe("以实际账单为准");
    expect(got.unavailable).toBe(false);
  });

  it("no point → unavailable + 暂无法预估费用 (no fake amount)", () => {
    const got = formatCostEstimate({
      ...base,
      point_cny: null,
      low_cny: null,
      high_cny: null,
      basis: "partial",
      unpriced: ["video"],
    });
    expect(got.costLine).toBe("暂无法预估费用");
    expect(got.unavailable).toBe(true);
    expect(got.partialNote).toBe("视频 未按精确单价计价，预估为参考值");
  });

  it("partial note reflects unpriced items instead of assuming video/token", () => {
    const got = formatCostEstimate({
      ...base,
      basis: "partial",
      unpriced: ["image", "transition"],
    });
    expect(got.partialNote).toBe("图片/转场 未按精确单价计价，预估为参考值");
  });

  it("spent=None hides the spent line; zero/negative values do not display", () => {
    const got = formatCostEstimate({ ...base, spent_cny: null });
    expect(got.spentLine).toBeNull();
    const zero = formatCostEstimate({
      ...base,
      low_cny: 0,
      high_cny: 0,
      point_cny: 0,
      spent_cny: -1,
    });
    expect(zero.costLine).toBe("暂无法预估费用");
    expect(zero.spentLine).toBeNull();
  });

  it("falls back when point exists but range missing", () => {
    const got = formatCostEstimate({ ...base, low_cny: null, high_cny: null });
    expect(got.costLine).toBe("预计费用 约¥43");
  });
});

describe("shouldRequestCostEstimate", () => {
  it("only requests when dialog open and task is vimax", () => {
    expect(shouldRequestCostEstimate(false, "vimax")).toBe(false);
    expect(shouldRequestCostEstimate(true, "ai_image")).toBe(false);
    expect(shouldRequestCostEstimate(true, "vimax")).toBe(true);
  });
});
