"use client";

import { useCallback, useSyncExternalStore } from "react";

export function useMediaQuery(query: string, onChange?: () => void): boolean {
  const subscribe = useCallback(
    (listener: () => void) => {
      const media = window.matchMedia(query);
      const handleChange = () => {
        listener();
        onChange?.();
      };
      media.addEventListener("change", handleChange);
      return () => media.removeEventListener("change", handleChange);
    },
    [query, onChange],
  );
  const getSnapshot = useCallback(
    () => window.matchMedia(query).matches,
    [query],
  );
  const getServerSnapshot = useCallback(() => true, []);
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
