import Link from "next/link";
import { redirect } from "next/navigation";

import { GatewayUnavailable } from "@/components/gateway-unavailable";
import { AuthProvider } from "@/core/auth/AuthProvider";
import { getServerSideUser } from "@/core/auth/server";
import { assertNever } from "@/core/auth/types";

import { RedirectToLogin } from "./redirect-to-login";
import { WorkspaceContent } from "./workspace-content";

export const dynamic = "force-dynamic";

export default async function WorkspaceLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const result = await getServerSideUser();

  switch (result.tag) {
    case "authenticated":
      return (
        <AuthProvider initialUser={result.user}>
          <WorkspaceContent>{children}</WorkspaceContent>
        </AuthProvider>
      );
    case "needs_setup":
      redirect("/setup");
    case "system_setup_required":
      redirect("/setup");
    case "unauthenticated":
      return <RedirectToLogin />;
    case "gateway_unavailable":
      return <GatewayUnavailable retryHref="/workspace" variant="workspace" />;
    case "config_error":
      throw new Error(result.message);
    default:
      assertNever(result);
  }
}
