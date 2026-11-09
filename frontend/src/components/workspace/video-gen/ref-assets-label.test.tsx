/**
 * P2-2（2026-08-31）：refAssetsLabel 纯函数单测——修前该函数（M5 参考资产标签 +
 * 机制 M 的 entity="preview" → 「预览图（首帧）」分支）完全无测试覆盖。
 * 直接对函数断言，无需渲染组件；PR-1（2026-10-17）vitest 分层后降 node 档
 * （无 `.dom.` 中缀 = node project）——在此新增 DOM 断言会炸，须先改名升档。
 */

import { describe, expect, it } from "vitest";

import type { RefInjectionRecord } from "@/core/video-gen/types";

import { refAssetsLabel } from "./video-gen-frame-ops-panel";

function rec(...refs: { entity: string }[]): RefInjectionRecord {
  return { frame_index: 0, injected_refs: refs };
}

describe("refAssetsLabel（M5 参考资产标签）", () => {
  it("无注入 → null（不渲染标签）", () => {
    expect(refAssetsLabel(undefined)).toBeNull();
    expect(refAssetsLabel({ frame_index: 3 })).toBeNull();
    expect(refAssetsLabel(rec())).toBeNull();
  });

  it("单条 preview → 「预览图（首帧）」（机制 M 分支）", () => {
    expect(refAssetsLabel(rec({ entity: "preview" }))).toBe(
      "预览图（首帧） ×1",
    );
  });

  it("混合来源 → 三标签齐 + 同实体聚合计数（按首次出现顺序 join）", () => {
    const label = refAssetsLabel(
      rec(
        { entity: "preview" },
        { entity: "style_anchor" },
        { entity: "preview" },
        { entity: "ref" },
      ),
    );
    expect(label).toBe("预览图（首帧） ×2 / 锚图 ×1 / 参考图 ×1");
  });

  it("未知名 entity → 原样输出 entity 字符串（角色名等 roster 来源）", () => {
    expect(refAssetsLabel(rec({ entity: "小林" }))).toBe("小林 ×1");
  });
});
