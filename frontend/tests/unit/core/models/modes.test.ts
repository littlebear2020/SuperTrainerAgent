import { describe, expect, it } from "vitest";

import {
  defaultModelForMode,
  type InputMode,
  modelsForMode,
  normalizeMode,
} from "@/core/models/modes";
import type { Model } from "@/core/models/types";

function makeModel(name: string, modes?: string[]): Model {
  return {
    id: name,
    name,
    model: name,
    display_name: name,
    ...(modes === undefined ? {} : { modes }),
  };
}

describe("normalizeMode", () => {
  it.each([
    ["flash", "flash"],
    ["standard", "standard"],
    ["ultra", "ultra"],
    ["thinking", "standard"],
    ["pro", "standard"],
    ["unknown", undefined],
    [undefined, undefined],
    [null, undefined],
  ])("%s → %s", (raw, expected) => {
    expect(normalizeMode(raw)).toBe(expected);
  });
});

describe("modelsForMode", () => {
  const roster = [
    makeModel("qwen", ["flash"]),
    makeModel("deepseek", ["flash", "standard"]),
    makeModel("claude", ["standard", "ultra"]),
    makeModel("internal", []),
  ];

  it("returns only models declared for the selected mode", () => {
    expect(modelsForMode(roster, "flash").map((model) => model.name)).toEqual([
      "qwen",
      "deepseek",
    ]);
    expect(
      modelsForMode(roster, "standard").map((model) => model.name),
    ).toEqual(["deepseek", "claude"]);
    expect(modelsForMode(roster, "ultra").map((model) => model.name)).toEqual([
      "claude",
    ]);
  });

  it("falls back to the full roster only when every model omits modes", () => {
    const legacy = [makeModel("one"), makeModel("two")];
    for (const mode of ["flash", "standard", "ultra"] as const) {
      expect(modelsForMode(legacy, mode)).toEqual(legacy);
    }
  });

  it("does not treat a mixed new roster as a legacy backend", () => {
    const mixed = [makeModel("legacy"), makeModel("flash", ["flash"])];
    expect(modelsForMode(mixed, "standard")).toEqual([]);
  });
});

describe("defaultModelForMode", () => {
  const roster = [
    makeModel("deepseek", ["flash", "standard"]),
    makeModel("claude", ["standard", "ultra"]),
    makeModel("qwen", ["flash"]),
  ];

  it("uses an explicit default that belongs to the mode", () => {
    expect(
      defaultModelForMode(roster, "standard", { standard: "claude" })?.name,
    ).toBe("claude");
  });

  it("falls back to the first model in the mode when the default is invalid", () => {
    expect(
      defaultModelForMode(roster, "standard", { standard: "qwen" })?.name,
    ).toBe("deepseek");
    expect(defaultModelForMode(roster, "standard", {})?.name).toBe("deepseek");
  });

  it("falls back to the full roster when the mode has no available model", () => {
    const ultraOnly = [makeModel("claude", ["standard", "ultra"])];
    expect(defaultModelForMode(ultraOnly, "flash", {})?.name).toBe("claude");
  });

  it("handles empty rosters and unknown modes", () => {
    expect(defaultModelForMode([], "standard", {})).toBeUndefined();
    expect(defaultModelForMode(roster, "unknown" as InputMode, {})?.name).toBe(
      "deepseek",
    );
  });
});
