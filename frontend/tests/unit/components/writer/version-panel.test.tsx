import { describe, expect, it } from "vitest";

import { reasonLabel } from "@/components/workspace/writer/version-panel";

describe("reasonLabel", () => {
  it("maps destructive to a Chinese label (P2-9)", () => {
    expect(reasonLabel("destructive")).toBe("覆盖保存");
  });
  it("keeps existing mappings", () => {
    expect(reasonLabel("auto")).toBe("自动保存");
    expect(reasonLabel("restore")).toBe("版本回滚");
  });
});
