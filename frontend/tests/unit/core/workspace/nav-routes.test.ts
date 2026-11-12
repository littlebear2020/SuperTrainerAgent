import { describe, expect, it } from "vitest";

import {
  isNavItemActive,
  PALETTE_NAV_ITEMS,
  WORKSPACE_NAV_ITEMS,
} from "@/core/workspace/nav-routes";

function item(labelKey: "myWorks" | "myAssets") {
  const value = WORKSPACE_NAV_ITEMS.find(
    (entry) => entry.labelKey === labelKey,
  );
  if (!value) throw new Error(`missing ${labelKey}`);
  return value;
}

describe("workspace nav routes", () => {
  it("exposes works and assets to sidebar and command palette from one SoT", () => {
    expect(item("myWorks").href).toBe("/workspace/video-gen");
    expect(item("myAssets").href).toBe("/workspace/video-gen?tab=assets");
    expect(PALETTE_NAV_ITEMS).toEqual(
      expect.arrayContaining([item("myWorks"), item("myAssets")]),
    );
  });

  it("activates works for the bare path and assets for the assets query", () => {
    expect(isNavItemActive(item("myWorks"), "/workspace/video-gen", "")).toBe(
      true,
    );
    expect(isNavItemActive(item("myAssets"), "/workspace/video-gen", "")).toBe(
      false,
    );
    expect(
      isNavItemActive(item("myWorks"), "/workspace/video-gen", "tab=assets"),
    ).toBe(false);
    expect(
      isNavItemActive(item("myAssets"), "/workspace/video-gen", "tab=assets"),
    ).toBe(true);
  });

  it("normalizes invalid query values before applying active state", () => {
    expect(
      isNavItemActive(item("myWorks"), "/workspace/video-gen", "tab=invalid"),
    ).toBe(true);
    expect(
      isNavItemActive(item("myAssets"), "/workspace/video-gen", "tab=invalid"),
    ).toBe(false);
  });

  it("activates only works for task details and keeps template assets separate", () => {
    expect(
      isNavItemActive(
        item("myWorks"),
        "/workspace/video-gen/task-1",
        "tab=assets",
      ),
    ).toBe(true);
    expect(
      isNavItemActive(
        item("myAssets"),
        "/workspace/video-gen/task-1",
        "tab=assets",
      ),
    ).toBe(false);
    expect(isNavItemActive(item("myWorks"), "/workspace/assets", "")).toBe(
      false,
    );
    expect(isNavItemActive(item("myAssets"), "/workspace/assets", "")).toBe(
      false,
    );
  });
});
