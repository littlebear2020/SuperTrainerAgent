import { describe, expect, it } from "vitest";

import {
  resolveSheetImgUrl,
  SHEET_LABEL,
} from "@/components/workspace/video-gen/character-sheet-lightbox";

// Bug 1 fix: resolveSheetImgUrl 返回 serve 的 HTTP URL（非 FS 路径）。
// M1/B3 订正：serve 已改稳定 id 寻址（下标随删除/排序漂移会串图）——
// 测试同步为 id 语义（此前传 number 靠字符串化侥幸通过但 tsc 报 TS2345）。
// 项目约定：.test.ts 只测纯函数（无 JSX）。

describe("resolveSheetImgUrl (Bug 1 fix)", () => {
  it("returns the serve-by-id URL (not the FS path)", () => {
    expect(resolveSheetImgUrl("front", "p1", "ab12cd34")).toBe(
      "/api/video/projects/p1/character-sheets/ab12cd34/front",
    );
    expect(resolveSheetImgUrl("side", "pX", "zz99yy88")).toBe(
      "/api/video/projects/pX/character-sheets/zz99yy88/side",
    );
  });

  it("SHEET_LABEL covers three views", () => {
    expect(SHEET_LABEL.front).toBe("正面");
    expect(SHEET_LABEL.side).toBe("侧面");
    expect(SHEET_LABEL.back).toBe("背面");
  });
});
