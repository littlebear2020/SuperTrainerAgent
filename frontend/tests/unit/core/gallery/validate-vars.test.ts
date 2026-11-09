import { describe, expect, it } from "vitest";

import { validateVarsWithConfigs, type VarConfig } from "@/core/gallery/api";

const file = (name: string) => ({ name }) as File;

describe("validateVarsWithConfigs", () => {
  const configs: Record<string, VarConfig> = {
    topic: { label: "主题", type: "string", required: true },
    doc: {
      label: "参考文档",
      type: "file",
      required: true,
      accept: ".docx,.pdf",
    },
    extra: { label: "补充材料", type: "file", required: false },
  };

  it("文本必填为空 + 文件必填未选 → 返回两个 label", () => {
    expect(validateVarsWithConfigs(configs, { topic: "  " }, {})).toEqual([
      "主题",
      "参考文档",
    ]);
  });

  it("文件必填已选 → 只报文本缺失", () => {
    expect(
      validateVarsWithConfigs(configs, {}, { doc: [file("a.docx")] }),
    ).toEqual(["主题"]);
  });

  it("全部满足 → 空数组（可选文件不强制）", () => {
    expect(
      validateVarsWithConfigs(
        configs,
        { topic: "微课" },
        { doc: [file("a.docx")] },
      ),
    ).toEqual([]);
  });
});
