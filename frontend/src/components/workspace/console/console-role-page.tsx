"use client";

import { ConsolePage } from "@/components/workspace/console/console-page";
import { PersonalUsagePage } from "@/components/workspace/usage/personal-usage-page";
import { useIsConsoleAdmin } from "@/core/console/hooks";

export function ConsoleRolePage() {
  const isAdmin = useIsConsoleAdmin();
  return isAdmin ? <ConsolePage /> : <PersonalUsagePage />;
}
