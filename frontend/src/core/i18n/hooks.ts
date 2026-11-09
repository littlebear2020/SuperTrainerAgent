"use client";

import { useEffect, useRef } from "react";

import { useI18nContext } from "./context";
import { getLocaleFromCookie, setLocaleInCookie } from "./cookies";
import { translations } from "./translations";

import { DEFAULT_LOCALE, normalizeLocale, type Locale } from "./index";

export function useI18n() {
  const { locale, setLocale } = useI18nContext();

  const t = translations[locale] ?? translations[DEFAULT_LOCALE];

  const changeLocale = (newLocale: Locale) => {
    setLocale(newLocale);
    setLocaleInCookie(newLocale);
  };

  // Initialize locale on mount. This only seeds state from the cookie/browser
  // language, so it must run once — every consumer of useI18n mounts its own
  // copy of this effect, and re-running them on provider renders re-set state
  // and rewrote the cookie on each pass. The ran-guard plus the no-op checks
  // keep this out of the render loop (React #185).
  const initializedRef = useRef(false);
  useEffect(() => {
    if (initializedRef.current) {
      return;
    }
    initializedRef.current = true;

    const saved = getLocaleFromCookie();
    if (saved) {
      const normalizedSaved = normalizeLocale(saved);
      if (normalizedSaved !== locale) {
        setLocale(normalizedSaved);
      }
      if (saved !== normalizedSaved) {
        setLocaleInCookie(normalizedSaved);
      }
      return;
    }

    // DSN-003b（2026-09-14 拍板 #6）：无 cookie 的新访客一律默认中文。修前此处
    // 先做浏览器语言检测（navigator.language）再落 cookie——英文浏览器新访客 SSR
    // 首帧中文、水合后翻英文并写 cookie，与拍板验收「无痕窗口首访全站中文」不符
    // （review-②）。浏览器语言检测保留在 detectLocale 导出（datetime.ts 仍用），
    // 不再是语言决议链的一环；用户手动切换不受影响（changeLocale 写 cookie）。
    if (locale !== DEFAULT_LOCALE) {
      setLocale(DEFAULT_LOCALE);
    }
    setLocaleInCookie(DEFAULT_LOCALE);
  }, [locale, setLocale]);

  return {
    locale,
    t,
    changeLocale,
  };
}
