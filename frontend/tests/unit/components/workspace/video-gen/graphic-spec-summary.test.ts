/** content-match T8④: graphic spec 只读摘要纯函数（类型标签 + 摘要 + tooltip）。 */
import { describe, expect, it } from "vitest";

import {
  graphicSpecSummary,
  graphicSpecTooltip,
  graphicSpecTypeLabel,
} from "@/components/workspace/video-gen/video-gen-storyboard";

describe("graphicSpecTypeLabel", () => {
  it("三种 spec 类型 + 未知兜底", () => {
    expect(graphicSpecTypeLabel("big_number")).toBe("大数字");
    expect(graphicSpecTypeLabel("steps")).toBe("步骤");
    expect(graphicSpecTypeLabel("text_card")).toBe("要点");
    expect(graphicSpecTypeLabel("future")).toBe("图文");
    expect(graphicSpecTypeLabel(undefined)).toBe("图文");
  });
});

describe("graphicSpecSummary", () => {
  it("big_number → 数字+单位", () => {
    expect(graphicSpecSummary({ number: "37", unit: "%" })).toBe("37%");
  });
  it("steps → 步数", () => {
    expect(graphicSpecSummary({ steps: ["a", "b", "c"] })).toBe("3 步");
  });
  it("text_card → 要点数，无要点退标题", () => {
    expect(graphicSpecSummary({ points: ["a", "b"] })).toBe("2 点");
    expect(graphicSpecSummary({ title: "免疫力" })).toBe("免疫力");
  });
});

describe("graphicSpecTooltip", () => {
  it("拼 title/description/points/steps", () => {
    const tip = graphicSpecTooltip({
      title: "核心",
      points: ["要点一", "要点二"],
      steps: ["步骤"],
    });
    expect(tip).toContain("核心");
    expect(tip).toContain("• 要点一");
    expect(tip).toContain("1. 步骤");
  });
  it("空 spec 兜底文案", () => {
    expect(graphicSpecTooltip({})).toBe("graphic spec（无内容）");
  });
});
