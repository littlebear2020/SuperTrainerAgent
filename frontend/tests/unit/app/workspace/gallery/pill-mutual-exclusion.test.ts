import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

/** P3-1 / BUG-001（2026-09-14）：Gallery 类目与「我的点赞」双高亮——静态守卫。 */
describe("Gallery pill 互斥（P3-1/BUG-001）", () => {
  const src = readFileSync(
    join(process.cwd(), "src/app/workspace/gallery/page.tsx"),
    "utf-8",
  );

  it("类目 pill 高亮条件含 !likedOnly", () => {
    // 锚点存在性先行（与 video-api-error 等源码守卫同口径）：任一操作数
    // 从源码消失时先在这里红，避免组合正则静默退化。
    expect(src).toContain("!likedOnly");
    expect(src).toContain("activeCategory === cat");
    // 允许操作数换序（!likedOnly && activeCategory === cat 或反向）——
    // 无害的重构不应碰红守卫。
    expect(src).toMatch(
      /!likedOnly\s*&&\s*activeCategory === cat|activeCategory === cat\s*&&\s*!likedOnly/,
    );
  });

  it("点类目 pill 时同时清 likedOnly（视图互斥）", () => {
    expect(src).toMatch(
      /setActiveCategory\(cat\);\s*\n\s*setLikedOnly\(false\);/,
    );
  });
});
