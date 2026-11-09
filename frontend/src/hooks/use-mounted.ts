import { useEffect, useState } from "react";

/**
 * Returns `true` only after the component has mounted on the client.
 * Use to prevent SSR/CSR hydration mismatches in components that
 * rely on client-only state (useSearchParams, browser APIs, etc.)
 * or generate dynamic IDs (Radix UI DropdownMenu, Dialog, etc.)
 */
export function useMounted() {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  return mounted;
}
