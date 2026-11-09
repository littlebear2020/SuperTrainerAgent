/**
 * B6 (2026-08-13): PdfExportDialog 零测试补测。
 *
 * 覆盖：基础渲染（标题/说明/导出按钮）、确认导出触发 submitPdfExport（mock api
 * 层断言参数）、任务状态流转（pending → ready → 下载触发 getPdfDownloadUrl +
 * window.open）、失败态（提交失败错误态 + 任务失败错误文案）、关闭回调。
 *
 * Radix Dialog 在 happy-dom 下不可靠（Portal + focus trap），按本组件只消费的
 * 表面 mock 掉 ui/dialog；仓库无 @testing-library/react，沿用 createRoot + act
 * 手写 harness（message-group-bash-step.dom.test.tsx 同款）。
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

vi.mock("@/components/ui/dialog", () => ({
  Dialog: ({ open, children }: { open: boolean; children: React.ReactNode }) =>
    open ? <div data-testid="dialog-root">{children}</div> : null,
  DialogContent: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="dialog-content">{children}</div>
  ),
  DialogDescription: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="dialog-description">{children}</div>
  ),
  DialogFooter: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  DialogHeader: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  DialogTitle: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="dialog-title">{children}</div>
  ),
}));

vi.mock("@/core/writer/api", () => ({
  getPdfDownloadUrl: vi.fn((taskId: string) => `/pdf/${taskId}`),
  submitPdfExport: vi.fn(),
}));

const { taskState } = vi.hoisted(() => ({
  taskState: {
    data: null as null | {
      status: string;
      progress?: number;
      error_message?: string;
      cost?: number | null;
      currency?: string | null;
    },
  },
}));

vi.mock("@/core/writer/hooks", () => ({
  useContentTask: () => ({ data: taskState.data }),
}));

import { PdfExportDialog } from "@/components/workspace/writer/pdf-export-dialog";
import { getPdfDownloadUrl, submitPdfExport } from "@/core/writer/api";
import type { TaskBrief } from "@/core/writer/types";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const mockSubmit = vi.mocked(submitPdfExport);
const mockDownloadUrl = vi.mocked(getPdfDownloadUrl);

/** Minimal TaskBrief — the dialog only reads ``id`` off the submit result. */
function brief(id: string): TaskBrief {
  return {
    id,
    user_id: "u-1",
    domain: "writer",
    task_type: "pdf_export",
    status: "pending",
    progress: 0,
    error_message: null,
    retry_count: 0,
    created_at: null,
    updated_at: null,
    cost: null,
    currency: null,
  };
}

let container: HTMLDivElement;
let root: Root;
let caughtErrors: unknown[];
let onOpenChange: ReturnType<typeof vi.fn<(open: boolean) => void>>;

beforeEach(() => {
  caughtErrors = [];
  onOpenChange = vi.fn<(open: boolean) => void>();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container, {
    onCaughtError: (err) => caughtErrors.push(err),
    onUncaughtError: (err) => caughtErrors.push(err),
  });
  vi.clearAllMocks();
  taskState.data = null;
  vi.spyOn(window, "open").mockImplementation(() => null);
});

afterEach(async () => {
  await act(async () => {
    root.unmount();
  });
  container.remove();
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

function renderDialog(open = true) {
  return act(async () => {
    root.render(
      <PdfExportDialog
        open={open}
        onOpenChange={onOpenChange}
        docId="doc-1"
        docTitle="测试文档"
      />,
    );
  });
}

function clickButton(text: string) {
  const btn = [...container.querySelectorAll("button")].find((b) =>
    b.textContent?.includes(text),
  );
  expect(btn, `button "${text}" should exist`).toBeTruthy();
  btn!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
}

test("基础渲染：标题/说明/导出按钮存在", async () => {
  await renderDialog();

  expect(container.textContent).toContain("导出 PDF");
  expect(container.textContent).toContain("将文档导出为 PDF 文件");
  expect(container.textContent).toContain("生成 PDF");
  expect(container.textContent).toContain("测试文档");
  expect(caughtErrors).toEqual([]);
});

test("确认导出触发 submitPdfExport，随后显示生成中状态", async () => {
  mockSubmit.mockResolvedValue(brief("t-1"));
  taskState.data = { status: "pending", progress: 30 };
  await renderDialog();

  await act(async () => {
    clickButton("生成 PDF");
  });

  expect(mockSubmit).toHaveBeenCalledWith({
    doc_id: "doc-1",
    title: "测试文档",
  });
  expect(container.textContent).toContain("正在生成 PDF");
  expect(container.textContent).toContain("30%");
  expect(caughtErrors).toEqual([]);
});

test("ready 状态显示下载按钮，点击触发 getPdfDownloadUrl + window.open", async () => {
  mockSubmit.mockResolvedValue(brief("t-1"));
  taskState.data = { status: "ready" };
  await renderDialog();

  await act(async () => {
    clickButton("生成 PDF");
  });

  expect(container.textContent).toContain("生成完成");
  expect(container.textContent).toContain("下载 PDF");

  await act(async () => {
    clickButton("下载 PDF");
  });
  expect(mockDownloadUrl).toHaveBeenCalledWith("t-1");
  expect(window.open).toHaveBeenCalledWith(
    "/pdf/t-1",
    "_blank",
    "noopener,noreferrer",
  );
  expect(caughtErrors).toEqual([]);
});

test("提交失败展示错误态", async () => {
  mockSubmit.mockRejectedValue(new Error("后端不可用"));
  await renderDialog();

  await act(async () => {
    clickButton("生成 PDF");
  });

  expect(container.textContent).toContain("后端不可用");
  expect(caughtErrors).toEqual([]);
});

test("ready 状态显示花费：cost=12.5 + CNY → ¥12.50（反馈4-D2）", async () => {
  mockSubmit.mockResolvedValue(brief("t-1"));
  taskState.data = { status: "ready", cost: 12.5, currency: "CNY" };
  await renderDialog();

  await act(async () => {
    clickButton("生成 PDF");
  });

  expect(container.textContent).toContain("生成完成");
  expect(container.textContent).toContain("花费：¥12.50");
  expect(caughtErrors).toEqual([]);
});

test("ready 状态 cost=null → 花费显示「—」占位（反馈4-D2）", async () => {
  mockSubmit.mockResolvedValue(brief("t-1"));
  taskState.data = { status: "ready", cost: null, currency: null };
  await renderDialog();

  await act(async () => {
    clickButton("生成 PDF");
  });

  expect(container.textContent).toContain("生成完成");
  expect(container.textContent).toContain("花费：—");
  expect(caughtErrors).toEqual([]);
});

test("任务失败态展示 error_message", async () => {
  mockSubmit.mockResolvedValue(brief("t-1"));
  taskState.data = { status: "failed", error_message: "PDF 渲染超时" };
  await renderDialog();

  await act(async () => {
    clickButton("生成 PDF");
  });

  expect(container.textContent).toContain("生成失败");
  expect(container.textContent).toContain("PDF 渲染超时");
  expect(caughtErrors).toEqual([]);
});

test("取消/关闭回调触发 onOpenChange(false)", async () => {
  await renderDialog();

  await act(async () => {
    clickButton("取消");
  });

  expect(onOpenChange).toHaveBeenCalledWith(false);
});
