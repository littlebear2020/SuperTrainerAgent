import { describe, expect, it } from "vitest";

import { getActiveStatusLabel } from "@/components/workspace/video-gen/video-gen-page";

describe("getActiveStatusLabel", () => {
  // E16: composing 在列表页 StatusBadge 误显示"排队中"；应显示"重新合成中"。

  it("composing → 重新合成中", () => {
    expect(getActiveStatusLabel("composing")).toBe("重新合成中");
  });

  it("running → 处理中", () => {
    expect(getActiveStatusLabel("running")).toBe("处理中");
  });

  it("pending（及其它未知 active 态）→ 排队中", () => {
    expect(getActiveStatusLabel("pending")).toBe("排队中");
  });
});
