/**
 * P2-2 / BUG-004：Writer 重命名→空文档覆盖链（行为级）。
 *
 * 契约：
 * - 缺陷 a：列表卡片重命名 input 按 Enter → **不**冒泡触发卡片导航（router.push
 *   未被调用）——修前 keydown 未 stopPropagation，Enter 冒泡进卡片 onKeyDown 误进编辑器。
 * - 缺陷 b：编辑器首载完成前渲染加载占位（无可输入元素）；加载完成后 TiptapEditor
 *   才挂载——修前 currentDoc 未就绪时 TiptapEditor 已挂载且可交互（空保存窗口）。
 *
 * 仓库无 @testing-library/react：createRoot + act + happy-dom 手写 harness。
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  push: vi.fn(),
  confirmImpl: async () => true as boolean,
  docQuery: {
    data: undefined as Record<string, unknown> | undefined,
    isLoading: false,
    isError: false,
  },
  listQuery: {
    data: {
      items: [
        {
          id: "doc_1",
          title: "会话纪要",
          doc_type: "general",
          content_json: "# 会话纪要\n正文",
          updated_at: "2026-09-14T01:00:00Z",
        },
      ],
      has_more: false,
    },
    isLoading: false,
    isError: false,
  },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: h.push, back: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/workspace/writer",
}));

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}));

vi.mock("@/core/writer/hooks", () => ({
  useWriterDoc: () => h.docQuery,
  useWriterDocs: () => h.listQuery,
  useUpdateWriterDoc: () => ({ mutate: vi.fn(), mutateAsync: vi.fn() }),
  useDeleteWriterDoc: () => ({ mutate: vi.fn(), mutateAsync: vi.fn() }),
}));

vi.mock("@/core/writer/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/core/writer/api")>();
  return {
    ...actual,
    getWriterDoc: vi.fn().mockResolvedValue({ id: "doc_1", version: 3 }),
    updateWriterDoc: vi.fn().mockResolvedValue({ id: "doc_1", version: 4 }),
  };
});

// 编辑器与各面板 stub：测试只关心挂载时序与导航
vi.mock("@/components/workspace/writer/tiptap-editor", () => ({
  TiptapEditor: () => <div data-testid="tiptap-stub" />,
}));
vi.mock("@/components/workspace/writer/editor-toolbar", () => ({
  EditorToolbar: () => null,
}));
vi.mock("@/components/workspace/writer/extensions-panel", () => ({
  ExtensionPanel: () => null,
}));
vi.mock("@/components/workspace/writer/from-scratch-panel", () => ({
  FromScratchPanel: () => null,
}));
vi.mock("@/components/workspace/writer/full-draft-modal", () => ({
  FullDraftModal: () => null,
}));
vi.mock("@/components/workspace/writer/ppt-export-dialog", () => ({
  PptExportDialog: () => null,
}));
vi.mock("@/components/workspace/writer/pdf-export-dialog", () => ({
  PdfExportDialog: () => null,
}));
vi.mock("@/components/workspace/writer/version-panel", () => ({
  VersionPanel: () => null,
}));
vi.mock("@/components/workspace/writer/fill-confirm-dialog", () => ({
  FillConfirmDialog: () => null,
}));
vi.mock("@/components/workspace/writer/selection-popover", () => ({
  SelectionPopover: () => null,
}));
vi.mock("@/components/workspace/writer/continue-writing-button", () => ({
  ContinueWritingButton: () => null,
}));
vi.mock("@/components/workspace/writer/new-doc-dialog", () => ({
  NewDocDialog: () => null,
}));
vi.mock("@/components/workspace/dialog-provider", () => ({
  DialogProvider: (p: { children: React.ReactNode }) => <>{p.children}</>,
  useConfirm: () => h.confirmImpl,
  usePrompt: () => async () => null,
}));

import { WriterPage } from "@/components/workspace/writer/writer-page";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

describe("P2-2/BUG-004 缺陷 a：重命名 Enter 不触发卡片导航", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    h.push.mockClear();
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

  it("重命名 input 按 Enter → 停留列表页（router.push 未被调用）", async () => {
    const qc = new QueryClient();
    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <WriterPage />
        </QueryClientProvider>,
      );
    });

    // 进入重命名态（铅笔按钮 title=重命名）
    const renameBtn = container.querySelector('button[title="重命名"]')!;
    expect(renameBtn).toBeTruthy();
    await act(async () => {
      renameBtn.dispatchEvent(
        new MouseEvent("click", { bubbles: true, cancelable: true }),
      );
    });

    const input = container.querySelector("input")!;
    expect(input).toBeTruthy();
    // Enter 冒泡 keydown——修前卡片 onKeyDown 会 router.push 进编辑器
    await act(async () => {
      input.dispatchEvent(
        new KeyboardEvent("keydown", {
          key: "Enter",
          bubbles: true,
          cancelable: true,
        }),
      );
    });
    expect(h.push).not.toHaveBeenCalled();
  });
});

describe("P2-2/BUG-004 缺陷 b：首载加载门控（docLoaded）", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    h.docQuery.data = undefined;
    h.docQuery.isLoading = true;
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
    try {
      localStorage.removeItem("writer_draft_doc_1");
    } catch {
      /* ignore */
    }
  });

  it("加载完成前：占位态、无 TiptapEditor；完成后：编辑器挂载", async () => {
    const qc = new QueryClient();
    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <WriterPage docId="doc_1" />
        </QueryClientProvider>,
      );
    });

    // 首载未完成：编辑区为加载占位，无可输入元素
    expect(container.querySelector('[data-testid="tiptap-stub"]')).toBeNull();
    expect(container.textContent).toContain("加载中");

    // 文档就绪（含 version 整数）→ rerender
    h.docQuery.data = {
      id: "doc_1",
      title: "会话纪要",
      doc_type: "general",
      content_json: "# 会话纪要\n正文内容",
      version: 3,
      updated_at: "2026-09-14T01:00:00Z",
    };
    h.docQuery.isLoading = false;
    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <WriterPage docId="doc_1" />
        </QueryClientProvider>,
      );
    });
    // 首载 effect 完成（async IIFE）→ docLoaded=true → TiptapEditor 挂载
    await act(async () => {
      await Promise.resolve();
    });
    expect(container.querySelector('[data-testid="tiptap-stub"]')).toBeTruthy();
  });
});

describe("review-③：confirm reject 不得永久卡加载占位（docLoaded 必置位）", () => {
  let container: HTMLDivElement;
  let root: Root;
  // happy-dom 的 localStorage 是残缺对象（无 setItem/clear——本批已知坑），
  // 该 describe 用内存桩替换。
  const memStore = new Map<string, string>();

  beforeEach(() => {
    memStore.clear();
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => memStore.get(k) ?? null,
      setItem: (k: string, v: string) => void memStore.set(k, v),
      removeItem: (k: string) => void memStore.delete(k),
      clear: () => void memStore.clear(),
    });
    h.docQuery.data = undefined;
    h.docQuery.isLoading = true;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    h.confirmImpl = async () => true;
    vi.unstubAllGlobals();
    await act(async () => {
      root.unmount();
    });
    container.remove();
    document.body.innerHTML = "";
  });

  it("恢复草稿 confirm 的 Promise reject → 编辑器仍挂载（外层 catch 兜底 + finally 收尾）", async () => {
    // 较新本地草稿（baseVersionInt 与服务端 version 不一致 + ts 更晚）→ 首载
    // effect 走 await confirm(...) 分支；令其 reject。
    localStorage.setItem(
      "writer_draft_doc_1",
      JSON.stringify({
        content: "本地草稿正文",
        ts: Date.now(),
        baseVersionInt: 2,
      }),
    );
    h.confirmImpl = async () => {
      throw new Error("dialog crashed");
    };

    const qc = new QueryClient();
    const render = () =>
      act(async () => {
        root.render(
          <QueryClientProvider client={qc}>
            <WriterPage docId="doc_1" />
          </QueryClientProvider>,
        );
      });
    await render();

    h.docQuery.data = {
      id: "doc_1",
      title: "会话纪要",
      doc_type: "general",
      content_json: "# 服务端内容",
      version: 3,
      updated_at: "2026-09-14T01:00:00Z",
    };
    h.docQuery.isLoading = false;
    await render();
    await act(async () => {
      await Promise.resolve();
    });
    // 不变量：无论 confirm 走向（resolve/reject/异常），docLoaded 必置位
    expect(container.querySelector('[data-testid="tiptap-stub"]')).toBeTruthy();
    expect(container.textContent).not.toContain("加载中");
  });
});
