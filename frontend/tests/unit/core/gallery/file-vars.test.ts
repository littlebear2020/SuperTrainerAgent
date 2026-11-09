import { expect, test } from "vitest";

import { caseHasFileVars } from "@/core/gallery/api";

test("caseHasFileVars detects file vars in vars_config and recipe", () => {
  expect(
    caseHasFileVars({
      vars_config: { doc: { label: "源文档", type: "file" } },
    } as never),
  ).toBe(true);
  expect(
    caseHasFileVars({
      vars_config: { topic: { label: "主题", type: "string" } },
    } as never),
  ).toBe(false);
  expect(caseHasFileVars({} as never)).toBe(false);
});

test("caseHasFileVars detects file vars nested in recipe steps", () => {
  // 提取产出的 file 变量挂在 recipe.steps[].vars 里（T11），不只顶层 vars_config
  expect(
    caseHasFileVars({
      recipe: {
        steps: [
          { instruction: "上传文档", template: "分析{doc}", vars: {} },
          {
            instruction: "分析",
            template: "分析",
            vars: { doc: { label: "源文档", type: "file", required: true } },
          },
        ],
      },
    } as never),
  ).toBe(true);
  expect(
    caseHasFileVars({
      recipe: {
        steps: [
          {
            instruction: "填主题",
            template: "写{topic}",
            vars: { topic: { label: "主题", type: "string" } },
          },
        ],
      },
    } as never),
  ).toBe(false);
});
