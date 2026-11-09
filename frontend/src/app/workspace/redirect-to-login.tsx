"use client";

import { usePathname, useSearchParams, useRouter } from "next/navigation";
import { useEffect } from "react";

export function RedirectToLogin() {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const router = useRouter();

  useEffect(() => {
    const query = searchParams.toString();
    const currentUrl = query ? `${pathname}?${query}` : pathname;
    router.replace(`/login?next=${encodeURIComponent(currentUrl)}`);
  }, [pathname, searchParams, router]);

  return (
    <div className="flex h-screen items-center justify-center">
      <div className="border-primary h-8 w-8 animate-spin rounded-full border-4 border-t-transparent" />
    </div>
  );
}
