import { describe, expect, it } from "vitest";

import { getTabularDelimiter } from "@/core/artifacts/preview";
import { checkCodeFile } from "@/core/utils/files";

// PR-3 规划侧 ruling（2026-10-18）：tsv 映射进 extensionMap 后，
// .tsv 文件应被识别为 tsv 语言并被 getTabularDelimiter 判为 tab 表格化文件。
describe("files ↔ tabular preview wiring", () => {
  it("maps .tsv files to the tsv language", () => {
    expect(checkCodeFile("data.tsv")).toEqual({
      isCodeFile: true,
      language: "tsv",
    });
  });

  it("csv/tsv resolve to their delimiters; others do not", () => {
    expect(getTabularDelimiter("csv")).toBe(",");
    expect(getTabularDelimiter("tsv")).toBe("\t");
    expect(getTabularDelimiter("markdown")).toBeNull();
    expect(getTabularDelimiter(null)).toBeNull();
  });
});
