"use client";

import type { Editor } from "@tiptap/react";
import { Image as ImageIcon, Mic, X } from "lucide-react";
import { memo, useEffect, useState } from "react";

import type { DocTypeConfig } from "@/core/writer/doc-type-config";
import type { DocType } from "@/core/writer/types";
import { cn } from "@/lib/utils";

import { DesignPanel } from "./design-panel";
import { VoicePanel } from "./voice-panel";

// ── Types ────────────────────────────────────────────────────────────────

interface ExtensionPanelProps {
  docType: DocType;
  typeConfig: DocTypeConfig;
  markdown: string;
  /** 编辑器实例（透传给 VoicePanel 的选区订阅，见其 props 注释）。 */
  editor: Editor | null;
  docTitle?: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onInsertImage: (src: string, alt?: string) => void;
}

// ── Tab definitions ──────────────────────────────────────────────────────

export type PanelTab = "voice" | "design";

interface TabDef {
  id: PanelTab;
  label: string;
  icon: typeof Mic;
  enabled: boolean;
}

/**
 * Pick the default active tab from the visible tab ids.
 *
 * Returns the first visible tab id, or `null` when no tabs are visible.
 * Previously the fallback was hardcoded to `"voice"`, which left the panel on
 * a non-rendered tab for design-only doc types (article/case) until the
 * correction effect ran — and produced a brief flash of empty content.
 */
export function pickDefaultTab(
  visibleTabIds: readonly PanelTab[],
): PanelTab | null {
  return visibleTabIds[0] ?? null;
}

// ── Component ────────────────────────────────────────────────────────────

function ExtensionPanelInner({
  docType,
  typeConfig,
  markdown,
  editor,
  docTitle,
  open,
  onOpenChange,
  onInsertImage,
}: ExtensionPanelProps) {
  const tabs: TabDef[] = [
    {
      id: "voice",
      label: "语音试听",
      icon: Mic,
      enabled: typeConfig.showVoicePanel,
    },
    {
      id: "design",
      label: "配图",
      icon: ImageIcon,
      enabled: typeConfig.showDesignPanel,
    },
  ];

  const visibleTabs = tabs.filter((t) => t.enabled);
  const [activeTab, setActiveTab] = useState<PanelTab | null>(
    pickDefaultTab(visibleTabs.map((t) => t.id)),
  );

  // Reset active tab when doc_type changes and current tab becomes hidden
  useEffect(() => {
    if (!visibleTabs.find((t) => t.id === activeTab)) {
      setActiveTab(pickDefaultTab(visibleTabs.map((t) => t.id)));
    }
  }, [visibleTabs, activeTab]);

  // P1-12 keep-alive: once opened, stay mounted (hidden) so in-flight task
  // polling/progress inside Voice/Design panels survives collapsing the
  // panel or switching tabs. Cost: the voice list request stays resident once
  // opened — acceptable. (Hooks must precede the early returns below.)
  const [everOpened, setEverOpened] = useState(false);
  useEffect(() => {
    if (open) setEverOpened(true);
  }, [open]);

  // Don't render if no panels are enabled
  if (visibleTabs.length === 0) return null;

  if (!everOpened) return null;

  return (
    <aside
      className={
        open
          ? "flex w-72 shrink-0 flex-col border-l border-[var(--border)] bg-[var(--background)]"
          : "hidden"
      }
    >
      {/* Tab header */}
      <div className="flex shrink-0 items-center justify-between border-b border-[var(--border)] px-2">
        <div className="flex">
          {visibleTabs.map((tab) => {
            const Icon = tab.icon;
            return (
              <button
                key={tab.id}
                type="button"
                onClick={() => setActiveTab(tab.id)}
                className={cn(
                  "flex items-center gap-1 border-b-2 px-3 py-2 text-xs font-medium transition-colors",
                  activeTab === tab.id
                    ? "border-[var(--primary)] text-[var(--primary)]"
                    : "border-transparent text-[var(--muted-foreground)] hover:text-[var(--foreground)]",
                )}
              >
                <Icon size={12} />
                {tab.label}
              </button>
            );
          })}
        </div>
        <button
          type="button"
          onClick={() => onOpenChange(false)}
          title="收起面板"
          className="rounded p-1 text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)] hover:text-[var(--foreground)]"
        >
          <X size={14} />
        </button>
      </div>

      {/* Tab content — all panels stay mounted once opened (P1-12);
          inactive ones are hidden, not unmounted, so their task polling
          and local state survive tab switches. */}
      <div className="min-h-0 flex-1">
        {typeConfig.showVoicePanel && (
          <div className={activeTab === "voice" ? "h-full" : "hidden"}>
            <VoicePanel
              markdown={markdown}
              editor={editor}
              docType={docType}
              docTitle={docTitle}
            />
          </div>
        )}
        {typeConfig.showDesignPanel && (
          <div className={activeTab === "design" ? "h-full" : "hidden"}>
            <DesignPanel
              markdown={markdown}
              docTitle={docTitle}
              onInsertImage={onInsertImage}
            />
          </div>
        )}
      </div>
    </aside>
  );
}

// 2026-10-06 性能批：memo 化——markdown 变化（击键）时 props 未变的子树跳过重渲
export const ExtensionPanel = memo(ExtensionPanelInner);
