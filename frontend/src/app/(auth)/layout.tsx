import Link from "next/link";
import { redirect } from "next/navigation";
import { type ReactNode } from "react";

import { GatewayUnavailable } from "@/components/gateway-unavailable";
import { AuthProvider } from "@/core/auth/AuthProvider";
import { getServerSideUser } from "@/core/auth/server";
import { assertNever } from "@/core/auth/types";

export const dynamic = "force-dynamic";

export default async function AuthLayout({
  children,
}: {
  children: ReactNode;
}) {
  const result = await getServerSideUser();

  switch (result.tag) {
    case "authenticated":
      redirect("/workspace");
    case "needs_setup":
      // Allow access to setup page
      return <AuthProvider initialUser={result.user}>{children}</AuthProvider>;
    case "system_setup_required":
    case "unauthenticated":
      return <AuthProvider initialUser={null}>{children}</AuthProvider>;
    case "gateway_unavailable":
      return <GatewayUnavailable retryHref="/login" />;
    case "config_error":
      throw new Error(result.message);
    default:
      assertNever(result);
  }
}
