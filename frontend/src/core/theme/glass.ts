"use client";

import { useTheme } from "next-themes";
import type React from "react";

/**
 * CSS-variable-based glass style — follows theme instantly via CSS,
 * no JS resolvedTheme delay, no light→dark flash on mount or navigation.
 */
export function glassStyle(transform?: string): React.CSSProperties {
  return {
    background:
      "linear-gradient(135deg, var(--glass-bg-from) 0%, var(--glass-bg-to) 100%)",
    backdropFilter: "blur(var(--glass-blur)) saturate(var(--glass-saturate))",
    WebkitBackdropFilter:
      "blur(var(--glass-blur)) saturate(var(--glass-saturate))",
    borderTop: "1.5px solid var(--glass-border-light)",
    borderLeft: "1.5px solid var(--glass-border-light)",
    borderBottom: "1.5px solid var(--glass-border-accent)",
    borderRight: "1.5px solid var(--glass-border-accent)",
    boxShadow: "var(--glass-shadow)",
    transform: transform ?? undefined,
    transition:
      "box-shadow 0.4s cubic-bezier(0.16, 1, 0.3, 1), border-color 0.4s cubic-bezier(0.16, 1, 0.3, 1)",
    position: "relative",
    overflow: "hidden",
    isolation: "isolate" as React.CSSProperties["isolation"],
  };
}

/**
 * Glass panel without `overflow: hidden` — for containers that host
 * popover/dropdown children (Radix Popover Portal still anchors to the
 * trigger, but an `overflow: hidden` ancestor can make Radix's collision
 * detection reject the floating layer). Keeps `isolation: isolate` (harmless
 * to glass children, e.g. `.ws-glass-input`). Use this variant for form
 * panels that contain a VoiceSelector / Popover-based dropdown.
 */
export function glassStyleNoClip(transform?: string): React.CSSProperties {
  return {
    ...glassStyle(transform),
    overflow: "visible",
  };
}

/** Light mode glass panel — explicit JS values (for non-CSS-variable contexts) */
export function lightGlassStyle(transform?: string): React.CSSProperties {
  return {
    background:
      "linear-gradient(135deg, rgba(255, 255, 255, 0.55) 0%, rgba(243, 245, 255, 0.25) 100%)",
    backdropFilter: "blur(32px) saturate(240%)",
    WebkitBackdropFilter: "blur(32px) saturate(240%)",
    borderTop: "1.5px solid rgba(255, 255, 255, 0.9)",
    borderLeft: "1.5px solid rgba(255, 255, 255, 0.9)",
    borderBottom: "1.5px solid rgba(99, 102, 241, 0.15)",
    borderRight: "1.5px solid rgba(99, 102, 241, 0.15)",
    boxShadow:
      "0 20px 40px -10px rgba(99, 102, 241, 0.06), 0 0 20px -2px rgba(99, 102, 241, 0.02), inset 0 1px 0 rgba(255, 255, 255, 0.8), inset 0 12px 24px rgba(255, 255, 255, 0.3)",
    transform: transform ?? undefined,
    transition:
      "box-shadow 0.4s cubic-bezier(0.16, 1, 0.3, 1), border-color 0.4s cubic-bezier(0.16, 1, 0.3, 1)",
    position: "relative",
    overflow: "hidden",
    isolation: "isolate" as React.CSSProperties["isolation"],
  };
}

/** Dark mode glass panel — explicit JS values (for non-CSS-variable contexts) */
export function darkGlassStyle(transform?: string): React.CSSProperties {
  return {
    background:
      "linear-gradient(135deg, rgba(10, 18, 36, 0.45) 0%, rgba(5, 10, 24, 0.2) 100%)",
    backdropFilter: "blur(40px) saturate(260%)",
    WebkitBackdropFilter: "blur(40px) saturate(260%)",
    borderTop: "1.5px solid rgba(255, 255, 255, 0.25)",
    borderLeft: "1.5px solid rgba(255, 255, 255, 0.25)",
    borderBottom: "1.5px solid rgba(0, 0, 0, 0.5)",
    borderRight: "1.5px solid rgba(0, 0, 0, 0.5)",
    boxShadow:
      "0 25px 50px -12px rgba(0, 0, 0, 0.65), 0 0 30px -5px rgba(0, 242, 254, 0.15), inset 0 1px 0 rgba(255, 255, 255, 0.25), inset 0 15px 30px rgba(255, 255, 255, 0.05)",
    transform: transform ?? undefined,
    transition:
      "box-shadow 0.4s cubic-bezier(0.16, 1, 0.3, 1), border-color 0.4s cubic-bezier(0.16, 1, 0.3, 1)",
    position: "relative",
    overflow: "hidden",
    isolation: "isolate" as React.CSSProperties["isolation"],
  };
}

/** Fallback glass panel — no blur, solid semi-transparent background */
export function fallbackGlassStyle(isDark: boolean): React.CSSProperties {
  return {
    background: isDark ? "rgba(10, 18, 36, 0.85)" : "rgba(255, 255, 255, 0.85)",
    border: isDark
      ? "1.5px solid rgba(255, 255, 255, 0.1)"
      : "1.5px solid rgba(255, 255, 255, 0.9)",
    position: "relative",
    overflow: "hidden",
  };
}

/** React hook: provides glassStyle functions bound to current theme */
export function useGlassStyle() {
  const { resolvedTheme } = useTheme();
  const isDark = resolvedTheme === "dark";

  return {
    isDark,
    glassStyle,
    glassStyleNoClip,
    lightGlassStyle,
    darkGlassStyle,
    fallbackGlassStyle: () => fallbackGlassStyle(isDark),
  };
}
