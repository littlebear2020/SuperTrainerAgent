import type { InputMode } from "@/core/models/modes";

export type { InputMode };

export type ReasoningEffort = "minimal" | "low" | "medium" | "high";

export function resolveMode(mode: InputMode | undefined): InputMode {
  return mode ?? "standard";
}

export function reasoningEffortForMode(mode: InputMode): ReasoningEffort {
  return mode === "flash" ? "minimal" : "high";
}
