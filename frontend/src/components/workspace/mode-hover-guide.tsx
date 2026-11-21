"use client";

import { useI18n } from "@/core/i18n/hooks";
import type { Translations } from "@/core/i18n/locales/types";
import type { InputMode } from "@/core/models/modes";

import { Tooltip } from "./tooltip";

export type AgentMode = InputMode;

function getModeLabelKey(
  mode: AgentMode,
): keyof Pick<
  Translations["inputBox"],
  "flashMode" | "standardMode" | "ultraMode"
> {
  switch (mode) {
    case "flash":
      return "flashMode";
    case "standard":
      return "standardMode";
    case "ultra":
      return "ultraMode";
  }
}

function getModeDescriptionKey(
  mode: AgentMode,
): keyof Pick<
  Translations["inputBox"],
  "flashModeDescription" | "standardModeDescription" | "ultraModeDescription"
> {
  switch (mode) {
    case "flash":
      return "flashModeDescription";
    case "standard":
      return "standardModeDescription";
    case "ultra":
      return "ultraModeDescription";
  }
}

export function ModeHoverGuide({
  mode,
  children,
  showTitle = true,
}: {
  mode: AgentMode;
  children: React.ReactNode;
  /** When true, tooltip shows "ModeName: Description". When false, only description. */
  showTitle?: boolean;
}) {
  const { t } = useI18n();
  const label = t.inputBox[getModeLabelKey(mode)];
  const description = t.inputBox[getModeDescriptionKey(mode)];
  const content = showTitle ? `${label}: ${description}` : description;

  return <Tooltip content={content}>{children}</Tooltip>;
}
