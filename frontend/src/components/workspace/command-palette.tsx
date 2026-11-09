"use client";

import {
  KeyboardIcon,
  MessageSquarePlusIcon,
  SettingsIcon,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandShortcut,
} from "@/components/ui/command";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useIsConsoleAdmin } from "@/core/console/hooks";
import { useI18n } from "@/core/i18n/hooks";
import { PALETTE_NAV_ITEMS } from "@/core/workspace/nav-routes";
import { useGlobalShortcuts } from "@/hooks/use-global-shortcuts";

import { SettingsDialog } from "./settings";

export function CommandPalette() {
  const { t } = useI18n();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [isMac, setIsMac] = useState(false);
  // console 项标签随角色变化（与侧栏同源 nav-routes + 同一 hook）
  const isAdmin = useIsConsoleAdmin();

  const handleNewChat = useCallback(() => {
    router.push("/workspace/chats/new");
    setOpen(false);
  }, [router]);

  // DSN-002（2026-09-14）：⌘K 页面导航——从 nav-routes 单一事实源 map 渲染
  //（默认 6 页，2026-09-14 产品拍板；搜索按 label/href 匹配由 cmdk 自动完成）。
  const pageItems = useMemo(
    () =>
      PALETTE_NAV_ITEMS.map((item) => ({
        ...item,
        onSelect: () => {
          router.push(item.href);
          setOpen(false);
        },
      })),
    [router],
  );

  const handleOpenSettings = useCallback(() => {
    setOpen(false);
    setSettingsOpen(true);
  }, []);

  const handleShowShortcuts = useCallback(() => {
    setOpen(false);
    setShortcutsOpen(true);
  }, []);

  const shortcuts = useMemo(
    () => [
      { key: "k", meta: true, action: () => setOpen((o) => !o) },
      { key: "n", meta: true, shift: true, action: handleNewChat },
      { key: ",", meta: true, action: handleOpenSettings },
      { key: "/", meta: true, action: handleShowShortcuts },
    ],
    [handleNewChat, handleOpenSettings, handleShowShortcuts],
  );

  useGlobalShortcuts(shortcuts);

  useEffect(() => {
    setIsMac(navigator.userAgent.includes("Mac"));
  }, []);
  const metaKey = isMac ? "⌘" : "Ctrl+";
  const shiftKey = isMac ? "⇧" : "Shift+";

  return (
    <>
      <SettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} />
      <CommandDialog open={open} onOpenChange={setOpen}>
        <CommandInput placeholder={t.shortcuts.searchActions} />
        <CommandList>
          <CommandEmpty>{t.shortcuts.noResults}</CommandEmpty>
          <CommandGroup heading={t.shortcuts.actions}>
            <CommandItem onSelect={handleNewChat}>
              <MessageSquarePlusIcon className="mr-2 h-4 w-4" />
              {t.sidebar.newChat}
              <CommandShortcut>
                {metaKey}
                {shiftKey}N
              </CommandShortcut>
            </CommandItem>
            <CommandItem onSelect={handleOpenSettings}>
              <SettingsIcon className="mr-2 h-4 w-4" />
              {t.common.settings}
              <CommandShortcut>{metaKey},</CommandShortcut>
            </CommandItem>
            <CommandItem onSelect={handleShowShortcuts}>
              <KeyboardIcon className="mr-2 h-4 w-4" />
              {t.shortcuts.keyboardShortcuts}
              <CommandShortcut>{metaKey}/</CommandShortcut>
            </CommandItem>
          </CommandGroup>
          <CommandGroup heading={t.shortcuts.pages}>
            {pageItems.map((item) => {
              const Icon = item.icon;
              return (
                <CommandItem key={item.href} onSelect={item.onSelect}>
                  <Icon className="mr-2 h-4 w-4" />
                  {item.labelRole === "console"
                    ? isAdmin
                      ? t.sidebar.consoleAdmin
                      : t.sidebar.consoleSelf
                    : t.sidebar[item.labelKey]}
                </CommandItem>
              );
            })}
          </CommandGroup>
        </CommandList>
      </CommandDialog>

      <Dialog open={shortcutsOpen} onOpenChange={setShortcutsOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{t.shortcuts.keyboardShortcuts}</DialogTitle>
            <DialogDescription>
              {t.shortcuts.keyboardShortcutsDescription}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 text-sm">
            {[
              { keys: `${metaKey}K`, label: t.shortcuts.openCommandPalette },
              { keys: `${metaKey}${shiftKey}N`, label: t.sidebar.newChat },
              { keys: `${metaKey}B`, label: t.shortcuts.toggleSidebar },
              { keys: `${metaKey},`, label: t.common.settings },
              {
                keys: `${metaKey}/`,
                label: t.shortcuts.keyboardShortcuts,
              },
            ].map(({ keys, label }) => (
              <div key={keys} className="flex items-center justify-between">
                <span className="text-muted-foreground">{label}</span>
                <kbd className="bg-muted text-muted-foreground rounded px-2 py-0.5 font-mono text-xs">
                  {keys}
                </kbd>
              </div>
            ))}
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
