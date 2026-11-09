import { describe, expect, it } from "vitest";

import { shouldCommitEdit } from "@/components/workspace/video-gen/video-gen-storyboard";

describe("shouldCommitEdit", () => {
  // E9: debounce 已 fire 后 blur 再触发一次 mutate（冗余重复同值）+ 无 in-flight 守卫。
  // 统一到单一判定：无变化不发、与上次提交同值不发（dedup in-flight/repeat）。

  it("值与乐观缓存现值相同 → false（无变化不发 PUT）", () => {
    expect(shouldCommitEdit("narration", "abc", "abc", null)).toBe(false);
  });

  it("值变化且无上次提交 → true", () => {
    expect(shouldCommitEdit("narration", "new", "old", null)).toBe(true);
  });

  it("与上次提交完全同值同字段 → false（debounce-fire-then-blur 去重）", () => {
    expect(
      shouldCommitEdit("narration", "new", "old", {
        field: "narration",
        value: "new",
      }),
    ).toBe(false);
  });

  it("同字段但值又变了 → true（last-write-wins，不误杀后续编辑）", () => {
    expect(
      shouldCommitEdit("narration", "newer", "old", {
        field: "narration",
        value: "new",
      }),
    ).toBe(true);
  });

  it("不同字段 → true（互不干扰）", () => {
    expect(
      shouldCommitEdit("image_prompt", "new", "old", {
        field: "narration",
        value: "new",
      }),
    ).toBe(true);
  });
});
