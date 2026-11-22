import type { Model } from "./types";

export type InputMode = "flash" | "standard" | "ultra";

export function normalizeMode(raw: unknown): InputMode | undefined {
  if (raw === "thinking" || raw === "pro") {
    return "standard";
  }
  if (raw === "flash" || raw === "standard" || raw === "ultra") {
    return raw;
  }
  return undefined;
}

export function modelsForMode(models: Model[], mode: InputMode): Model[] {
  if (models.every((model) => model.modes === undefined)) {
    return models;
  }
  return models.filter((model) => model.modes?.includes(mode));
}

export function defaultModelForMode(
  models: Model[],
  mode: InputMode,
  modeDefaults: Record<string, string> = {},
): Model | undefined {
  const availableModels = modelsForMode(models, mode);
  const defaultName = modeDefaults[mode];
  return (
    availableModels.find((model) => model.name === defaultName) ??
    availableModels[0] ??
    models[0]
  );
}

export function stripDefaultSuffix(displayName: string): string {
  return displayName.replace(/\s*(?:（默认）|\(默认\))$/, "");
}
