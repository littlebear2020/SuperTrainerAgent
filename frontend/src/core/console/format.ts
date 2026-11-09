/**
 * Display formatting for the operations console.
 *
 * These exist because the real data range is brutal: 869 runs whose token
 * counts span 0 to 256,517,593. Rendering raw integers in a table column
 * blows the layout apart, and `toLocaleString()` alone still produces
 * "256,517,593" — 11 characters in a numeric column that also has to fit a
 * header. So the table uses compact notation and keeps the exact value in a
 * tooltip; totals in the summary cards, where there is room, use full
 * grouping.
 */

/** Full thousands-separated form, e.g. `1234567` → `"1,234,567"`. */
export function formatInteger(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  return Math.round(value).toLocaleString("en-US");
}

/**
 * Compact token count for table cells, e.g. `256517593` → `"256.5M"`.
 *
 * Deliberately not `Intl.NumberFormat(notation: "compact")`: that renders
 * 1_500_000 as "1.5M" but 999_999 as "1000K", and localizes the suffix, so
 * column width becomes locale-dependent. Fixed suffixes keep the column
 * width predictable.
 */
export function formatCompactNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  const abs = Math.abs(value);
  if (abs < 1_000) return String(Math.round(value));
  if (abs < 1_000_000) return `${trimZero(value / 1_000)}K`;
  if (abs < 1_000_000_000) return `${trimZero(value / 1_000_000)}M`;
  return `${trimZero(value / 1_000_000_000)}B`;
}

function trimZero(value: number): string {
  // One decimal, but drop a trailing ".0" so "1M" doesn't render as "1.0M".
  const fixed = value.toFixed(1);
  return fixed.endsWith(".0") ? fixed.slice(0, -2) : fixed;
}

// Money formatting lives in core/utils/format.ts now (批次 C Phase D) so task
// views outside the console can share it; re-export keeps console imports
// byte-compatible.
export { currencySymbol, formatCost } from "@/core/utils/format";

/** Wall-clock duration, e.g. `3661` → `"1h 1m"`, `12.4` → `"12.4s"`. */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) {
    return "—";
  }
  const total = Math.max(0, seconds);
  if (total < 60) {
    return `${total < 10 ? total.toFixed(1) : Math.round(total)}s`;
  }
  if (total < 3_600) {
    const minutes = Math.floor(total / 60);
    return `${minutes}m ${Math.round(total % 60)}s`;
  }
  const hours = Math.floor(total / 3_600);
  return `${hours}h ${Math.floor((total % 3_600) / 60)}m`;
}

/** Local date+time for a UTC ISO string, or "—" when absent/unparseable. */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return "—";
  return parsed.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Short label for a daily chart axis, e.g. `"2026-08-03"` → `"08-03"`. */
export function formatDayLabel(date: string): string {
  return date.length >= 10 ? date.slice(5) : date;
}

/**
 * Minutes to subtract from UTC to get local time, for day bucketing.
 *
 * `getTimezoneOffset()` returns minutes to ADD to local to reach UTC (+480
 * behind UTC), which is the opposite sign of what the API's
 * `tz_offset_minutes` expects, hence the negation.
 */
export function localTzOffsetMinutes(): number {
  return -new Date().getTimezoneOffset();
}
