"use client";

/**
 * 网关不可用占位（2026-10-06 合并批）：auth 与 workspace 布局各持一份。
 * workspace 变体多一行说明与 Logout 入口。
 */
import Link from "next/link";

export function GatewayUnavailable({
  retryHref,
  variant = "auth",
}: {
  retryHref: string;
  variant?: "auth" | "workspace";
}) {
  return (
    <div className="flex h-screen flex-col items-center justify-center gap-4">
      <p className="text-muted-foreground">Service temporarily unavailable.</p>
      {variant === "workspace" && (
        <p className="text-muted-foreground text-xs">
          The backend may be restarting. Please wait a moment and try again.
        </p>
      )}
      <div className="flex gap-3">
        <Link
          href={retryHref}
          className="bg-primary text-primary-foreground hover:bg-primary/90 rounded-md px-4 py-2 text-sm"
        >
          Retry
        </Link>
        {variant === "workspace" && (
          <Link
            href="/api/v1/auth/logout"
            className="text-muted-foreground hover:bg-muted rounded-md border px-4 py-2 text-sm"
          >
            Logout &amp; Reset
          </Link>
        )}
      </div>
    </div>
  );
}
