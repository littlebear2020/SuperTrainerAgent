/**
 * PR-3 5.2 行为级断言：CSV artifact 表格化渲染 + 超 1MiB 内容截断标志。
 * useDelimitedPreview 的 worker 链路另有用例（use-delimited-preview.dom）；
 * 本件 mock 该 hook 直灌结果，钉渲染层契约（默认表格化 + 抽样标志）。
 * 另附源断言：artifact-file-detail 对 tabular 默认 preview 态（i18n 硬编码
 * 常量不在本 PR allow，i18n 化留规划侧）。
 */

import { readFileSync } from "node:fs";
import path from "node:path";

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

vi.mock("@/core/artifacts/use-delimited-preview", () => ({
  useDelimitedPreview: vi.fn(),
}));

import { ArtifactTablePreview } from "@/components/workspace/artifacts/artifact-table-preview";
import { useDelimitedPreview } from "@/core/artifacts/use-delimited-preview";

const useDelimitedPreviewMock = vi.mocked(useDelimitedPreview);

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => {
    root.unmount();
  });
  container.remove();
  useDelimitedPreviewMock.mockReset();
});

test("CSV 表格化渲染：表头/数据行/行号落地", async () => {
  useDelimitedPreviewMock.mockReturnValue({
    result: {
      rows: [
        ["name", "value"],
        ["alpha", "1"],
        ["beta", "2"],
      ],
      columnCount: 2,
      limited: false,
      unevenRows: false,
    },
    status: "ready",
    retry: () => undefined,
  });

  await act(async () => {
    root.render(
      <ArtifactTablePreview
        content="name,value\nalpha,1\nbeta,2"
        delimiter=","
        truncated={false}
        identity="f.csv"
      />,
    );
  });

  const table = container.querySelector(
    "[data-testid='artifact-table-preview'] table",
  );
  expect(table).toBeTruthy();
  // 首行为表头（默认勾选）
  expect(table?.querySelectorAll("thead th")[1]?.textContent).toBe("name");
  // 2 数据行 ×（行号 + 2 列）
  expect(table?.querySelectorAll("tbody tr")).toHaveLength(2);
  expect(table?.textContent).toContain("alpha");
  expect(table?.textContent).toContain("beta");
});

test("超 1MiB 内容（limited=true）：显示抽样/截断标志", async () => {
  useDelimitedPreviewMock.mockReturnValue({
    result: {
      rows: Array.from({ length: 250 }, (_, i) => [`r${i}`, String(i)]),
      columnCount: 2,
      limited: true,
      unevenRows: false,
    },
    status: "ready",
    retry: () => undefined,
  });

  await act(async () => {
    root.render(
      <ArtifactTablePreview
        content={"a,b\n" + "x,1\n".repeat(200000)}
        delimiter=","
        truncated={false}
        identity="big.csv"
      />,
    );
  });

  const preview = container.querySelector(
    "[data-testid='artifact-table-preview']",
  );
  expect(preview).toBeTruthy();
  // 截断标志：抽样预览（前 200 行）文案 + 首页 50 行（分页 PAGE_SIZE）
  expect(preview?.textContent).toContain("抽样预览");
  expect(preview?.querySelectorAll("tbody tr")).toHaveLength(50);
});

test("artifact-file-detail 对 tabular 文件默认 preview 态（源断言）", () => {
  const source = readFileSync(
    path.resolve(
      import.meta.dirname,
      "../../../../../src/components/workspace/artifacts/artifact-file-detail.tsx",
    ),
    "utf8",
  );
  // isSupportPreview 含 tabularPreviewReady（评审订正后含 write-file 守卫：
  // pending write_file 的 CSV 压回 source 态，上游不变量的本地等价）
  expect(source).toContain("getTabularDelimiter(language) !== null");
  expect(source).toMatch(
    /language === "html" \|\| language === "markdown" \|\| tabularPreviewReady/,
  );
  expect(source).toContain(
    "const tabularPreviewReady = isTabular && !isWriteFile;",
  );
  // preview 分支挂 ArtifactTablePreview + fetch 期 active 门（防「文件为空」误导终态）
  expect(source).toContain("<ArtifactTablePreview");
  expect(source).toContain("active={!contentLoading}");
});
