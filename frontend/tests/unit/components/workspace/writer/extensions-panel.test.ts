/**
 * ExtensionPanel — pickDefaultTab helper tests (Task W26).
 *
 * The `activeTab` default previously fell back to `"voice"` even when no voice
 * panel was visible (e.g. article/case doc types show only `design`). This
 * meant the panel mounted on a non-rendered tab and relied on a correction
 * effect to fix it — producing a brief flash of empty content.
 *
 * `pickDefaultTab` returns the first visible tab id (or `null` if none),
 * so a design-only type defaults to `design`, not `voice`.
 *
 * B6-T4: video panel removed — PanelTab is now "voice" | "design" only.
 */

import { describe, expect, test } from "vitest";

import { pickDefaultTab } from "@/components/workspace/writer/extensions-panel";

describe("pickDefaultTab", () => {
  test("returns the first visible tab id when tabs are present", () => {
    expect(pickDefaultTab(["voice", "design"])).toBe("voice");
    expect(pickDefaultTab(["design"])).toBe("design");
  });

  test("design-only type defaults to design, not voice", () => {
    // article/case doc types: showDesignPanel only
    expect(pickDefaultTab(["design"])).toBe("design");
    expect(pickDefaultTab(["design"])).not.toBe("voice");
  });

  test("returns null when no tabs are visible", () => {
    expect(pickDefaultTab([])).toBeNull();
  });

  test("voice-only type defaults to voice", () => {
    expect(pickDefaultTab(["voice"])).toBe("voice");
  });
});
