import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, test } from "vitest";

// （PR-2 4.2：上游同款源断言——虚拟化的关键机制点位（实测行高/稳定 key/
// 流式末组常驻/超扫描距）任一被无意拆除时红。组件级滚动行为由 >60 组长会话
// 人工冒烟覆盖（plan §5 行为级清单）。）
describe("message history virtualization", () => {
  const source = readFileSync(
    path.resolve(
      import.meta.dirname,
      "../../../../../src/components/workspace/messages/virtual-message-list.tsx",
    ),
    "utf8",
  );

  test("uses measured stable-key rows and keeps the active tail mounted", () => {
    expect(source).toContain("useVirtualizer");
    expect(source).toContain("measureElement");
    expect(source).toContain("getItemKey");
    expect(source).toContain("activeIndex");
    expect(source).toContain("overscan: 8");
  });

  test("虚拟化阈值钉死 60 组（阈值上=虚拟、阈值下=静态全渲染）", () => {
    expect(source).toContain("VIRTUALIZATION_THRESHOLD = 60");
    expect(source).toContain("groups.length >= VIRTUALIZATION_THRESHOLD");
  });

  test("prepend 锚定与首挂载双帧定位在位", () => {
    expect(source).toContain("anchorRef");
    expect(source).toContain("positionedInitialVirtualWindowRef");
  });
});
