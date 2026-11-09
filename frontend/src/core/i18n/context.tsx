"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import type { Locale } from "@/core/i18n";

export interface I18nContextType {
  locale: Locale;
  setLocale: (locale: Locale) => void;
}

export const I18nContext = createContext<I18nContextType | null>(null);

export function I18nProvider({
  children,
  initialLocale,
}: {
  children: ReactNode;
  initialLocale: Locale;
}) {
  const [locale, setLocale] = useState<Locale>(initialLocale);

  // Stable identity: useI18n's init effect lists setLocale in its deps, so a
  // per-render function made that effect re-run on every render (and rewrite
  // the cookie each time). Combined with the value memo below this keeps
  // consumers from re-rendering on unrelated provider renders.
  const handleSetLocale = useCallback((newLocale: Locale) => {
    setLocale(newLocale);
    document.cookie = `locale=${newLocale}; path=/; max-age=31536000`;
  }, []);

  const value = useMemo(
    () => ({ locale, setLocale: handleSetLocale }),
    [locale, handleSetLocale],
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18nContext() {
  const context = useContext(I18nContext);
  if (!context) {
    throw new Error("useI18n must be used within I18nProvider");
  }
  return context;
}
