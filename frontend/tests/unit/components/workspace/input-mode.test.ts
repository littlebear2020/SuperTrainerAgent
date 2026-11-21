import { describe, expect, it } from "vitest";

import {
  reasoningEffortForMode,
  resolveMode,
} from "@/components/workspace/input-mode";
import { normalizeMode } from "@/core/models/modes";

describe("resolveMode", () => {
  it.each(["flash", "standard", "ultra"] as const)(
    "preserves %s without consulting model thinking capability",
    (mode) => {
      expect(resolveMode(mode)).toBe(mode);
    },
  );

  it("defaults an unset mode to standard", () => {
    expect(resolveMode(undefined)).toBe("standard");
  });
});

describe("normalizeMode", () => {
  it.each(["thinking", "pro"])("migrates legacy %s to standard", (mode) => {
    expect(normalizeMode(mode)).toBe("standard");
  });

  it("returns undefined for unknown values", () => {
    expect(normalizeMode("other")).toBeUndefined();
  });
});

describe("reasoningEffortForMode", () => {
  it("maps flash to minimal", () => {
    expect(reasoningEffortForMode("flash")).toBe("minimal");
  });

  it.each(["standard", "ultra"] as const)("maps %s to high", (mode) => {
    expect(reasoningEffortForMode(mode)).toBe("high");
  });
});
