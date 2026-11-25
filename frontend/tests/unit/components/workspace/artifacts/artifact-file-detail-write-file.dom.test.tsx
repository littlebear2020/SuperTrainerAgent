/**
 * PR4（2026-11-24 前端可靠性批）：write-file 引用在详情面板里的五态渲染。
 * harness 参照 artifact-file-detail-discard-confirm.dom.test.tsx（无 @testing-library/react）。
 * useArtifactContent 以替身驱动（五态判定本身由 write-file-content-state 用例覆盖）。
 */

import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  writeFileState: null as unknown,
  refetchHistory: undefined as (() => Promise<void>) | undefined,
  hasMoreHistory: false,
  tBase: {} as Record<string, unknown>,
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({ getQueryData: vi.fn(), setQueryData: vi.fn() }),
}));

vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({ t: h.tBase }),
}));

vi.mock("@/env", () => ({
  env: { NEXT_PUBLIC_STATIC_WEBSITE_ONLY: "false" },
}));

// CodeMirror 重库：用只读 textarea 暴露 value（断言正文来源）
vi.mock("next/dynamic", () => ({
  default: () => (props: Record<string, unknown>) => (
    <textarea
      data-testid="code-editor"
      readOnly
      value={props.value as string}
    />
  ),
}));

vi.mock("streamdown", () => ({
  Streamdown: () => null,
}));

vi.mock("@/core/streamdown", () => ({
  streamdownPlugins: [],
}));

vi.mock("@/core/skills/api", () => ({
  installSkill: vi.fn(),
}));

vi.mock("@/core/artifacts/hooks", () => ({
  useArtifactContent: () => ({
    content:
      h.writeFileState &&
      (h.writeFileState as WriteFileContentState).status === "ready"
        ? (h.writeFileState as { content: string }).content
        : undefined,
    url: undefined,
    sha256: undefined,
    isLoading:
      h.writeFileState &&
      (h.writeFileState as WriteFileContentState).status === "pending",
    error: null,
    writeFileState: h.writeFileState as WriteFileContentState,
  }),
}));

vi.mock("@/components/workspace/artifacts/context", () => ({
  useArtifacts: () => ({
    artifacts: [],
    setOpen: vi.fn(),
    select: vi.fn(),
    drafts: {},
    setDrafts: vi.fn(),
    editingPath: null,
    setEditingPath: vi.fn(),
  }),
}));

vi.mock("@/components/workspace/messages/context", () => ({
  useThread: () => ({
    thread: { isLoading: false, messages: [] },
    isHistoryLoading: false,
    hasMoreHistory: h.hasMoreHistory,
    refetchHistory: h.refetchHistory,
  }),
}));

vi.mock("@/components/workspace/tooltip", () => ({
  Tooltip: (p: Record<string, unknown>) => <div>{p.children as ReactNode}</div>,
}));

vi.mock("@/components/workspace/citations/artifact-link", () => ({
  ArtifactLink: () => null,
}));

vi.mock("@/components/ai-elements/artifact", () => ({
  Artifact: (p: Record<string, unknown>) => <div {...p} />,
  ArtifactHeader: (p: Record<string, unknown>) => <div {...p} />,
  ArtifactTitle: (p: Record<string, unknown>) => <div {...p} />,
  ArtifactActions: (p: Record<string, unknown>) => (
    <div {...p} data-testid="artifact-actions" />
  ),
  ArtifactAction: (p: Record<string, unknown>) => (
    <button
      type="button"
      disabled={p.disabled as boolean | undefined}
      data-action={p.label as string}
    >
      {p.label as string}
    </button>
  ),
  ArtifactContent: (p: Record<string, unknown>) => <div {...p} />,
}));

vi.mock("@/components/ui/select", () => ({
  Select: () => <div data-testid="artifact-select" />,
  SelectContent: () => null,
  SelectGroup: () => null,
  SelectItem: () => null,
  SelectTrigger: () => null,
  SelectValue: () => null,
}));

vi.mock("@/components/ui/toggle-group", () => ({
  ToggleGroup: (p: Record<string, unknown>) => <div {...p} />,
  ToggleGroupItem: () => null,
}));

import { ArtifactFileDetail } from "@/components/workspace/artifacts/artifact-file-detail";
import { DialogProvider } from "@/components/workspace/dialog-provider";
import type { WriteFileContentState } from "@/core/artifacts/loader";
import { zhCN } from "@/core/i18n/locales/zh-CN";

h.tBase = zhCN as unknown as Record<string, unknown>;

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const REF =
  "write-file:?path=%2Fmnt%2Fdata%2F100%25%20final%231.txt&message_id=msg-1&tool_call_id=call-1";

describe("write-file 引用在详情面板里的五态渲染", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    h.writeFileState = null;
    h.refetchHistory = undefined;
    h.hasMoreHistory = false;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    document.body.innerHTML = "";
  });

  async function renderDetail() {
    await act(async () => {
      root.render(
        <DialogProvider>
          <ArtifactFileDetail filepath={REF} threadId="th_1" />
        </DialogProvider>,
      );
    });
  }

  it("ready：标题是特殊文件名，正文来自定位到的 tool call", async () => {
    h.writeFileState = {
      status: "ready",
      content: "正文内容",
    } as WriteFileContentState;
    await renderDetail();
    expect(container.textContent).toContain("100% final#1.txt");
    expect(
      container.querySelector<HTMLTextAreaElement>(
        "[data-testid='code-editor']",
      )?.value,
    ).toBe("正文内容");
    // 合法的空文件也是 ready：正文为空但不是「没找到」
    expect(container.textContent).not.toContain("没有找到");
  });

  it("pending：显示加载中，不渲染编辑器", async () => {
    h.writeFileState = { status: "pending" } as WriteFileContentState;
    await renderDetail();
    expect(container.textContent).toContain(zhCN.writeFileRef.pending);
    expect(container.querySelector("[data-testid='code-editor']")).toBeNull();
  });

  it("failed：显示工具错误原文，不渲染正文", async () => {
    h.writeFileState = {
      status: "failed",
      message: "Error: write_file content exceeds the limit",
    } as WriteFileContentState;
    await renderDetail();
    expect(container.textContent).toContain(
      "Error: write_file content exceeds the limit",
    );
    expect(container.querySelector("[data-testid='code-editor']")).toBeNull();
  });

  it("invalid：显示引用无效", async () => {
    h.writeFileState = { status: "invalid" } as WriteFileContentState;
    await renderDetail();
    expect(container.textContent).toContain(zhCN.writeFileRef.invalid);
    expect(container.querySelector("[data-testid='code-editor']")).toBeNull();
  });

  it("not_found + 仍有未加载的旧 run：显示「加载完整历史」，点击调用并等待 refetchHistory", async () => {
    h.writeFileState = { status: "not_found" } as WriteFileContentState;
    h.hasMoreHistory = true;
    let resolve!: () => void;
    const refetchHistory = vi.fn(
      () =>
        new Promise<void>((r) => {
          resolve = r;
        }),
    );
    h.refetchHistory = refetchHistory;
    await renderDetail();
    expect(container.textContent).toContain(zhCN.writeFileRef.notFound);
    const button = [...container.querySelectorAll("button")].find((b) =>
      b.textContent?.includes(zhCN.writeFileRef.loadFullHistory),
    );
    expect(button).toBeTruthy();
    await act(async () => {
      button!.click();
    });
    expect(refetchHistory).toHaveBeenCalledTimes(1);
    await act(async () => {
      resolve();
    });
  });

  it("not_found + 历史已全部加载：重试也真实调用 refetchHistory；没有该能力时只显示关闭", async () => {
    h.writeFileState = { status: "not_found" } as WriteFileContentState;
    h.hasMoreHistory = false;
    const refetchHistory = vi.fn(async () => undefined);
    h.refetchHistory = refetchHistory;
    await renderDetail();
    const retry = [...container.querySelectorAll("button")].find((b) =>
      b.textContent?.includes(zhCN.writeFileRef.retry),
    );
    expect(retry).toBeTruthy();
    await act(async () => {
      retry!.click();
    });
    expect(refetchHistory).toHaveBeenCalledTimes(1);

    // 没有 refetchHistory 的消费者（agents/new 等）：只有关闭
    h.refetchHistory = undefined;
    await renderDetail();
    const actions = [...container.querySelectorAll("button")].map((b) =>
      b.getAttribute("data-action"),
    );
    expect(actions).toContain(zhCN.common.close);
    expect(actions).not.toContain(zhCN.writeFileRef.loadFullHistory);
    expect(actions).not.toContain(zhCN.writeFileRef.retry);
    expect(
      [...container.querySelectorAll("button")].some((b) =>
        b.textContent?.includes(zhCN.writeFileRef.retry),
      ),
    ).toBe(false);
    expect(container.textContent).toContain(zhCN.writeFileRef.notFound);
  });
});
