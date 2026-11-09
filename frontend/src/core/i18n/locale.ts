export const SUPPORTED_LOCALES = ["en-US", "zh-CN"] as const;
export type Locale = (typeof SUPPORTED_LOCALES)[number];
// DSN-003b（2026-09-14 产品拍板）：默认语言改中文——仅影响未存 localStorage
// 语言偏好的新访客；已选英文的用户不受影响（localStorage 优先于默认值）。
export const DEFAULT_LOCALE: Locale = "zh-CN";

export function isLocale(value: string): value is Locale {
  return (SUPPORTED_LOCALES as readonly string[]).includes(value);
}

export function normalizeLocale(locale: string | null | undefined): Locale {
  if (!locale) {
    return DEFAULT_LOCALE;
  }

  if (isLocale(locale)) {
    return locale;
  }

  if (locale.toLowerCase().startsWith("zh")) {
    return "zh-CN";
  }

  return DEFAULT_LOCALE;
}

// Helper function to detect browser locale
export function detectLocale(): Locale {
  if (typeof window === "undefined") {
    return DEFAULT_LOCALE;
  }

  const browserLang =
    navigator.language ||
    (navigator as unknown as { userLanguage: string }).userLanguage;

  return normalizeLocale(browserLang);
}
