/**
 * T21: display formatting for the operations console.
 *
 * These matter because of the real data range: 869 runs whose token counts
 * span 0 to 256,517,593. Without compaction the table column is 11 characters
 * wide and forces horizontal scroll; without a "—" for null cost, an unpriced
 * deployment would show "0.00" and read as "this was free".
 */

import { describe, expect, it } from "vitest";

import {
  formatCompactNumber,
  formatCost,
  formatDateTime,
  formatDayLabel,
  formatDuration,
  formatInteger,
  localTzOffsetMinutes,
} from "@/core/console/format";

describe("formatInteger", () => {
  it("groups thousands", () => {
    expect(formatInteger(1234567)).toBe("1,234,567");
    // The real maximum observed in this deployment.
    expect(formatInteger(256517593)).toBe("256,517,593");
  });

  it("renders zero as 0, not as a dash", () => {
    // A run that genuinely used no tokens is a fact, not missing data.
    expect(formatInteger(0)).toBe("0");
  });

  it("renders missing values as a dash", () => {
    expect(formatInteger(null)).toBe("—");
    expect(formatInteger(undefined)).toBe("—");
    expect(formatInteger(Number.NaN)).toBe("—");
  });
});

describe("formatCompactNumber", () => {
  it("keeps the column narrow across the whole real range", () => {
    expect(formatCompactNumber(0)).toBe("0");
    expect(formatCompactNumber(999)).toBe("999");
    expect(formatCompactNumber(121182)).toBe("121.2K"); // observed median
    expect(formatCompactNumber(1500000)).toBe("1.5M");
    expect(formatCompactNumber(7462994)).toBe("7.5M"); // observed p95
    expect(formatCompactNumber(256517593)).toBe("256.5M"); // observed max
    expect(formatCompactNumber(2500000000)).toBe("2.5B");
  });

  it("never renders wider than 6 characters", () => {
    // The invariant the table layout depends on.
    for (const value of [
      0, 999, 1000, 121182, 9999999, 256517593, 999999999999,
    ]) {
      expect(formatCompactNumber(value).length).toBeLessThanOrEqual(6);
    }
  });

  it("drops a trailing .0 so 1M is not 1.0M", () => {
    expect(formatCompactNumber(1000000)).toBe("1M");
    expect(formatCompactNumber(2000)).toBe("2K");
  });

  it("handles missing values", () => {
    expect(formatCompactNumber(null)).toBe("—");
    expect(formatCompactNumber(undefined)).toBe("—");
  });
});

describe("formatCost", () => {
  it("distinguishes unpriced from free", () => {
    // The whole point: null means "no pricing configured", and must not be
    // rendered as a zero amount.
    expect(formatCost(null, "CNY")).toBe("—");
    expect(formatCost(undefined, "CNY")).toBe("—");
    expect(formatCost(0, "CNY")).toBe("¥0.00");
  });

  it("uses the currency symbol", () => {
    expect(formatCost(12.5, "CNY")).toBe("¥12.50");
    expect(formatCost(12.5, "USD")).toBe("$12.50");
    expect(formatCost(12.5, "EUR")).toBe("€12.50");
  });

  it("falls back to the raw currency code when unknown", () => {
    expect(formatCost(1, "JPY")).toBe("JPY 1.00");
  });

  it("keeps sub-unit spend visible instead of rounding it to zero", () => {
    // Per-run spend is often fractions of a yuan; 0.00 would hide it.
    expect(formatCost(0.0034, "CNY")).toBe("¥0.0034");
    expect(formatCost(1.5, "CNY")).toBe("¥1.50");
  });

  it("renders a null daily cost as unknown, matching the API's nullable day.cost", () => {
    // ConsoleUsageDay.cost is nullable: an unpriced day must not render "¥0.00",
    // which a reader would take as "this day was free".
    expect(formatCost(null, "CNY")).toBe("—");
    expect(formatCost(null, null)).toBe("—");
  });

  it("groups thousands in large totals", () => {
    expect(formatCost(1234567.891, "CNY")).toBe("¥1,234,567.89");
  });
});

describe("formatDuration", () => {
  it("scales the unit to the magnitude", () => {
    expect(formatDuration(0)).toBe("0.0s");
    expect(formatDuration(9.44)).toBe("9.4s");
    expect(formatDuration(42)).toBe("42s");
    expect(formatDuration(90)).toBe("1m 30s");
    expect(formatDuration(3661)).toBe("1h 1m");
  });

  it("clamps negatives rather than printing them", () => {
    // Clock skew between created_at and updated_at must not surface as "-3s".
    expect(formatDuration(-5)).toBe("0.0s");
  });

  it("handles missing values", () => {
    expect(formatDuration(null)).toBe("—");
    expect(formatDuration(undefined)).toBe("—");
  });
});

describe("formatDateTime", () => {
  it("renders a parseable ISO timestamp", () => {
    expect(formatDateTime("2026-08-03T12:34:00Z")).toMatch(/2026/);
  });

  it("does not throw on junk or absent input", () => {
    expect(formatDateTime(null)).toBe("—");
    expect(formatDateTime("")).toBe("—");
    expect(formatDateTime("not-a-date")).toBe("—");
  });
});

describe("formatDayLabel", () => {
  it("drops the year so axis labels stay short", () => {
    expect(formatDayLabel("2026-08-03")).toBe("08-03");
  });

  it("passes short strings through untouched", () => {
    expect(formatDayLabel("08-03")).toBe("08-03");
  });
});

describe("localTzOffsetMinutes", () => {
  it("returns the sign the API expects", () => {
    // getTimezoneOffset() is minutes to ADD to local to reach UTC, i.e. the
    // opposite sign from tz_offset_minutes. A sign error would shift every
    // day bucket by a full day for UTC+8.
    expect(localTzOffsetMinutes()).toBe(-new Date().getTimezoneOffset());
  });
});
