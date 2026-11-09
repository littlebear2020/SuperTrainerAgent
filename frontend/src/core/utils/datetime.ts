import { formatDistanceToNow } from "date-fns";
import { enUS as dateFnsEnUS, zhCN as dateFnsZhCN } from "date-fns/locale";

import { detectLocale, type Locale } from "@/core/i18n";
import { getLocaleFromCookie } from "@/core/i18n/cookies";

function getDateFnsLocale(locale: Locale) {
  switch (locale) {
    case "zh-CN":
      return dateFnsZhCN;
    case "en-US":
    default:
      return dateFnsEnUS;
  }
}

/**
 * Normalize a backend timestamp into a JS Date.
 *
 * The backend may return timestamps in several formats:
 * - ISO 8601 with timezone:  "2026-05-13T10:00:00+00:00"
 * - ISO 8601 without tz:     "2026-05-13T10:00:00"  (treated as UTC)
 * - UNIX timestamp (float):  1747123456.789         (seconds, not ms)
 * - UNIX timestamp (string): "1747123456.789"
 */
export function parseTimestamp(value: Date | string | number): Date {
  if (value instanceof Date) return value;

  // Number: UNIX timestamp in SECONDS (LangGraph stores seconds, JS expects ms)
  if (typeof value === "number") {
    return new Date(value * 1000);
  }

  // String: could be ISO or numeric UNIX timestamp
  const trimmed = value.trim();
  if (!trimmed) return new Date(NaN);

  // Pure numeric string → UNIX timestamp in seconds
  if (/^\d+(\.\d+)?$/.test(trimmed)) {
    return new Date(Number.parseFloat(trimmed) * 1000);
  }

  // Try native parsing first (handles ISO 8601 with timezone, etc.)
  const native = new Date(trimmed);
  if (!Number.isNaN(native.getTime())) return native;

  // Fallback: append Z for naive ISO timestamps without timezone
  // (aiosqlite strips tzinfo, producing naive datetimes)
  return new Date(`${trimmed}Z`);
}

export function formatTimeAgo(date: Date | string | number, locale?: Locale) {
  const effectiveLocale =
    locale ??
    (getLocaleFromCookie() as Locale | null) ??
    // Fallback when cookie is missing (or on first render)
    detectLocale();
  return formatDistanceToNow(parseTimestamp(date), {
    addSuffix: true,
    locale: getDateFnsLocale(effectiveLocale),
  });
}

/**
 * 相对时间（2026-10-06 合并批：writer-page.relativeTime ≡ version-panel.formatTime）。
 * 后端时间串无时区后缀按 UTC；超过 24h 的呈现由 overDay 决定：
 * "days-ago"（N 天前，writer 列表口径）/ "date"（zh-CN 短日期，版本面板口径）。
 */
export function relativeTime(
  dateStr: string,
  overDay: "days-ago" | "date" = "days-ago",
): string {
  // Treat as UTC if no timezone suffix present
  const normalized =
    dateStr.endsWith("Z") || /[+-]\d{2}:\d{2}$/.test(dateStr)
      ? dateStr
      : `${dateStr}Z`;
  const d = new Date(normalized);
  const mins = Math.floor((Date.now() - d.getTime()) / 60_000);
  if (mins < 1) return "刚刚";
  if (mins < 60) return `${mins}分钟前`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}小时前`;
  if (overDay === "days-ago") return `${Math.floor(hrs / 24)}天前`;
  return formatDateTime(d);
}

/** zh-CN 短日期时间（月/日 + 时:分）——展示层统一口径。 */
export function formatDateTime(
  value: Date | string,
  opts: Intl.DateTimeFormatOptions = {},
): string {
  const d = value instanceof Date ? value : new Date(value);
  return d.toLocaleString("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    ...opts,
  });
}
