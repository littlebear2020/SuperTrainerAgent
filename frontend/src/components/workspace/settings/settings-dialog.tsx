"use client";

import {
  BellIcon,
  InfoIcon,
  BrainIcon,
  MessageSquareIcon,
  PaletteIcon,
  SparklesIcon,
  UserIcon,
  WrenchIcon,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { AboutSettingsPage } from "@/components/workspace/settings/about-settings-page";
import { AccountSettingsPage } from "@/components/workspace/settings/account-settings-page";
import { AppearanceSettingsPage } from "@/components/workspace/settings/appearance-settings-page";
import { ChannelSettingsPage } from "@/components/workspace/settings/channel-settings-page";
import { MemorySettingsPage } from "@/components/workspace/settings/memory-settings-page";
import { NotificationSettingsPage } from "@/components/workspace/settings/notification-settings-page";
import { SkillSettingsPage } from "@/components/workspace/settings/skill-settings-page";
import { ToolSettingsPage } from "@/components/workspace/settings/tool-settings-page";
import { useAuth } from "@/core/auth/AuthProvider";
import { useI18n } from "@/core/i18n/hooks";
import { useGlassStyle } from "@/core/theme/glass";
import { cn } from "@/lib/utils";

type SettingsSection =
  | "account"
  | "appearance"
  | "memory"
  | "tools"
  | "skills"
  | "channels"
  | "notification"
  | "about";

type SettingsDialogProps = React.ComponentProps<typeof Dialog> & {
  defaultSection?: SettingsSection;
};

export function SettingsDialog(props: SettingsDialogProps) {
  const { defaultSection = "appearance", ...dialogProps } = props;
  const { t } = useI18n();
  const { isAdmin } = useAuth();
  const [activeSection, setActiveSection] =
    useState<SettingsSection>(defaultSection);

  useEffect(() => {
    // When opening the dialog, ensure the active section follows the caller's intent.
    // This allows triggers like "About" to open the dialog directly on that page.
    if (dialogProps.open) {
      setActiveSection(defaultSection);
    }
  }, [defaultSection, dialogProps.open]);

  const sections = useMemo(() => {
    const allSections = [
      {
        id: "account",
        label: t.settings.sections.account,
        icon: UserIcon,
      },
      {
        id: "appearance",
        label: t.settings.sections.appearance,
        icon: PaletteIcon,
      },
      {
        id: "notification",
        label: t.settings.sections.notification,
        icon: BellIcon,
      },
      {
        id: "memory",
        label: t.settings.sections.memory,
        icon: BrainIcon,
      },
      { id: "tools", label: t.settings.sections.tools, icon: WrenchIcon },
      { id: "skills", label: t.settings.sections.skills, icon: SparklesIcon },
      {
        id: "channels",
        label: t.settings.sections.channels,
        icon: MessageSquareIcon,
      },
      { id: "about", label: t.settings.sections.about, icon: InfoIcon },
    ];
    return isAdmin
      ? allSections
      : allSections.filter(
          (section) => section.id === "account" || section.id === "appearance",
        );
  }, [
    isAdmin,
    t.settings.sections.account,
    t.settings.sections.appearance,
    t.settings.sections.memory,
    t.settings.sections.tools,
    t.settings.sections.skills,
    t.settings.sections.channels,
    t.settings.sections.notification,
    t.settings.sections.about,
  ]);
  const effectiveActiveSection = sections.some(
    (section) => section.id === activeSection,
  )
    ? activeSection
    : "account";
  const { glassStyle, isDark } = useGlassStyle();

  return (
    <Dialog
      {...dialogProps}
      onOpenChange={(open) => props.onOpenChange?.(open)}
    >
      <DialogContent
        className="flex h-[75vh] max-h-[calc(100vh-2rem)] flex-col border-0 bg-transparent shadow-none sm:max-w-5xl md:max-w-6xl"
        aria-describedby={undefined}
      >
        <div
          style={
            isDark
              ? glassStyle()
              : {
                  background:
                    "linear-gradient(135deg, rgba(255,255,255,0.92) 0%, rgba(243,245,255,0.88) 100%)",
                  backdropFilter: "blur(32px) saturate(240%)",
                  WebkitBackdropFilter: "blur(32px) saturate(240%)",
                  borderTop: "1.5px solid rgba(255,255,255,0.95)",
                  borderLeft: "1.5px solid rgba(255,255,255,0.95)",
                  borderBottom: "1.5px solid rgba(99,102,241,0.12)",
                  borderRight: "1.5px solid rgba(99,102,241,0.12)",
                  boxShadow:
                    "0 20px 60px -10px rgba(99,102,241,0.1), inset 0 1px 0 rgba(255,255,255,0.9), inset 0 12px 24px rgba(255,255,255,0.4)",
                  position: "relative" as const,
                  overflow: "hidden" as const,
                }
          }
          className="interactive-glass flex h-full flex-col overflow-hidden rounded-2xl"
        >
          <DialogHeader className="gap-1 px-6 pt-5">
            <DialogTitle
              className={cn(
                "text-lg font-extrabold",
                isDark ? "text-white" : "text-slate-800",
              )}
            >
              {t.settings.title}
            </DialogTitle>
            <p
              className={cn(
                "text-sm",
                isDark ? "text-slate-400" : "text-slate-500",
              )}
            >
              {t.settings.description}
            </p>
          </DialogHeader>
          <div className="grid min-h-0 flex-1 gap-4 px-6 pt-3 pb-5 md:grid-cols-[220px_minmax(0,1fr)]">
            <nav
              className={cn(
                "min-h-0 overflow-y-auto rounded-xl p-2",
                isDark ? "bg-white/5" : "bg-white/60",
              )}
            >
              <ul className="space-y-1 pr-1">
                {sections.map(({ id, label, icon: Icon }) => {
                  const active = effectiveActiveSection === id;
                  return (
                    <li key={id}>
                      <button
                        type="button"
                        onClick={() => setActiveSection(id as SettingsSection)}
                        className={cn(
                          "flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-all",
                          active
                            ? isDark
                              ? "border border-white/20 bg-white/15 text-white shadow-sm"
                              : "bg-indigo-500 text-white shadow-sm"
                            : isDark
                              ? "text-slate-400 hover:bg-white/10 hover:text-white"
                              : "text-slate-500 hover:bg-indigo-500/10 hover:text-indigo-600",
                        )}
                      >
                        <Icon className="size-4" />
                        <span>{label}</span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </nav>
            <ScrollArea
              className={cn(
                "h-full min-h-0 rounded-xl",
                isDark ? "bg-white/5" : "bg-white/60",
              )}
            >
              <div className="space-y-8 p-6">
                {effectiveActiveSection === "account" && (
                  <AccountSettingsPage />
                )}
                {effectiveActiveSection === "appearance" && (
                  <AppearanceSettingsPage />
                )}
                {effectiveActiveSection === "memory" && <MemorySettingsPage />}
                {effectiveActiveSection === "tools" && <ToolSettingsPage />}
                {effectiveActiveSection === "skills" && (
                  <SkillSettingsPage
                    onClose={() => props.onOpenChange?.(false)}
                  />
                )}
                {effectiveActiveSection === "notification" && (
                  <NotificationSettingsPage />
                )}
                {effectiveActiveSection === "channels" && (
                  <ChannelSettingsPage />
                )}
                {effectiveActiveSection === "about" && <AboutSettingsPage />}
              </div>
            </ScrollArea>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
