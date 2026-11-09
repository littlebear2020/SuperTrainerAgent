/**
 * 缺陷 1（2026-08-27）：resolveMode 全矩阵 —— 「模型思考能力」只门控「思考」档，
 * Pro/Ultra 对所有模型开放（不再被 supportsThinking=false 弹回 flash）。
 * reasoningEffortForMode 四档映射（从 input-box 内联三元抽出）。
 */

import { describe, expect, it } from "vitest";

import {
  reasoningEffortForMode,
  resolveMode,
} from "@/components/workspace/input-mode";

describe("resolveMode 全矩阵（supportsThinking × mode）", () => {
  // ── supportsThinking = true ──
  it("true × flash → flash", () => {
    expect(resolveMode("flash", true)).toBe("flash");
  });
  it("true × thinking → thinking", () => {
    expect(resolveMode("thinking", true)).toBe("thinking");
  });
  it("true × pro → pro", () => {
    expect(resolveMode("pro", true)).toBe("pro");
  });
  it("true × ultra → ultra", () => {
    expect(resolveMode("ultra", true)).toBe("ultra");
  });
  it("true × undefined → pro（现有默认）", () => {
    expect(resolveMode(undefined, true)).toBe("pro");
  });

  // ── supportsThinking = false ──
  it("false × flash → flash", () => {
    expect(resolveMode("flash", false)).toBe("flash");
  });
  it("false × thinking → flash（唯一保留的弹回；T25 置灰行为的语义来源）", () => {
    expect(resolveMode("thinking", false)).toBe("flash");
  });
  it("false × pro → pro（缺陷 1 核心：不弹回）", () => {
    expect(resolveMode("pro", false)).toBe("pro");
  });
  it("false × ultra → ultra（缺陷 1 核心：不弹回）", () => {
    expect(resolveMode("ultra", false)).toBe("ultra");
  });
  it("false × undefined → flash（现有默认）", () => {
    expect(resolveMode(undefined, false)).toBe("flash");
  });
});

describe("reasoningEffortForMode 四档映射", () => {
  it("ultra → high", () => {
    expect(reasoningEffortForMode("ultra")).toBe("high");
  });
  it("pro → medium", () => {
    expect(reasoningEffortForMode("pro")).toBe("medium");
  });
  it("thinking → low", () => {
    expect(reasoningEffortForMode("thinking")).toBe("low");
  });
  it("flash → minimal", () => {
    expect(reasoningEffortForMode("flash")).toBe("minimal");
  });
});
