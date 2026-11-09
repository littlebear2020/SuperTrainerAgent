import { describe, expect, it } from "vitest";

import { buildCharacterSheetUploadFormData } from "@/core/video-gen/character-sheet-upload";

// A3：后端 /character-sheets/upload 要求 front/side/back 三字段全到齐，
// 故纯函数构造完整 FormData（per-view 选择/预览在组件层，这里只测表单构造）。

const f = (n: string) => new File(["x"], n, { type: "image/png" });

describe("buildCharacterSheetUploadFormData (A3)", () => {
  it("attaches front/side/back + name", () => {
    const fd = buildCharacterSheetUploadFormData(
      { front: f("a.png"), side: f("b.png"), back: f("c.png") },
      "小明",
    );
    expect(fd.get("name")).toBe("小明");
    expect((fd.get("front") as File).name).toBe("a.png");
    expect((fd.get("side") as File).name).toBe("b.png");
    expect((fd.get("back") as File).name).toBe("c.png");
  });

  it("omits features when not provided", () => {
    const fd = buildCharacterSheetUploadFormData(
      { front: f("a"), side: f("b"), back: f("c") },
      "x",
    );
    expect(fd.get("features")).toBeNull();
  });

  it("includes features when provided", () => {
    const fd = buildCharacterSheetUploadFormData(
      { front: f("a"), side: f("b"), back: f("c") },
      "x",
      "红发,铠甲",
    );
    expect(fd.get("features")).toBe("红发,铠甲");
  });
});

it("attaches only provided views (front-only, U1)", () => {
  const fd = buildCharacterSheetUploadFormData({ front: f("a.png") }, "x");
  expect((fd.get("front") as File).name).toBe("a.png");
  expect(fd.get("side")).toBeNull();
  expect(fd.get("back")).toBeNull();
});
