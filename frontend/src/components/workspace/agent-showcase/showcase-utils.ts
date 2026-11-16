import type { LocalizedText } from "@/core/agents/showcase";

export type ShowcaseLocale = "zh" | "en";

export function localized(
  value: LocalizedText,
  locale: ShowcaseLocale,
): string {
  return value[locale];
}

export function formatDuration(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(whole / 60)).padStart(2, "0")}:${String(whole % 60).padStart(2, "0")}`;
}

export function formatBytes(bytes: number, locale: ShowcaseLocale): string {
  const formatter = new Intl.NumberFormat(locale === "zh" ? "zh-CN" : "en-US", {
    maximumFractionDigits: 1,
  });
  return bytes >= 1_000_000
    ? `${formatter.format(bytes / 1_000_000)} MB`
    : `${formatter.format(bytes / 1_000)} KB`;
}
