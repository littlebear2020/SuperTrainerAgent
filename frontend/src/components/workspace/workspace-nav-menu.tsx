"use client";

import { Settings2Icon } from "lucide-react";
import { useState } from "react";

import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar";
import { useAuth } from "@/core/auth/AuthProvider";
import { useI18n } from "@/core/i18n/hooks";

import { SettingsDialog } from "./settings";

function NavUserContent({
  email,
  isAdmin,
  isSidebarOpen,
  t,
}: {
  email: string;
  isAdmin: boolean;
  isSidebarOpen: boolean;
  t: ReturnType<typeof useI18n>["t"];
}) {
  const displayName = email.split("@").at(0) ?? t.sidebar.roleUser;
  const initial = displayName.charAt(0).toUpperCase();
  const avatar = (
    <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-[#6366f1] to-[#ec4899] text-[11px] font-bold text-white">
      {initial}
    </div>
  );

  if (!isSidebarOpen) {
    return (
      <div className="flex size-full items-center justify-center">{avatar}</div>
    );
  }

  return (
    <div
      title={email}
      className="flex min-w-0 flex-1 items-center gap-2.5 text-left text-sm"
    >
      {avatar}
      <div className="flex min-w-0 flex-col">
        <span className="text-foreground truncate text-[13px] font-medium">
          {displayName}
        </span>
        <span className="text-muted-foreground text-[10px]">
          {isAdmin ? t.sidebar.roleAdmin : t.sidebar.roleUser}
        </span>
      </div>
    </div>
  );
}

export function WorkspaceNavMenu() {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const { open: isSidebarOpen } = useSidebar();
  const { t } = useI18n();
  const { user, isAdmin } = useAuth();
  const email = user?.email ?? "";

  return (
    <>
      <SettingsDialog
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
        defaultSection="appearance"
      />
      <SidebarMenu className="w-full">
        <SidebarMenuItem className="flex items-center gap-1">
          <NavUserContent
            email={email}
            isAdmin={isAdmin}
            isSidebarOpen={isSidebarOpen}
            t={t}
          />
          <SidebarMenuButton
            aria-label={t.common.settings}
            className="size-8 shrink-0"
            onClick={() => setSettingsOpen(true)}
          >
            <Settings2Icon />
          </SidebarMenuButton>
        </SidebarMenuItem>
      </SidebarMenu>
    </>
  );
}
