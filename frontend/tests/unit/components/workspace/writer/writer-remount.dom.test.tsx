/**
 * B1-T1: WriterEditor remounts on docId change (Bug 1/2/4).
 *
 * The bug: WriterEditor had no `key` prop -> component instance + all
 * refs/state (modals, streaming, titleManuallySetRef) persisted across
 * document switches -> content from doc A leaked into doc B, modals
 * stayed open, and isFirstLoad prevented the load effect from firing.
 *
 * Fix: `<WriterEditor key={docId} docId={docId} />` in WriterPage.
 *
 * Behavioral (2026-10-05 review; was a source-regex guard): mounts the real
 * WriterPage via the createRoot + act + happy-dom harness (same as
 * writer-page-load-gate.test.tsx — the "can't test without RTL" claim was
 * outdated) with a mount-counting TiptapEditor stub, then switches docId.
 * With the key, the old editor subtree unmounts and a fresh one mounts;
 * without it the same instance would re-render in place (0 unmounts).
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  editorMounts: [] as string[],
  editorUnmounts: 0,
  confirmImpl: async () => true as boolean,
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), back: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/workspace/writer",
}));

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}));

// The doc query resolves immediately for ANY docId, so the editor passes the
// docLoaded gate on every mount without network involvement.
vi.mock("@/core/writer/hooks", () => ({
  useWriterDoc: (docId: string) => ({
    data: {
      id: docId,
      title: `文档 ${docId}`,
      doc_type: "general",
      content_json: `# ${docId}\n正文`,
      version: 3,
      updated_at: "2026-09-14T01:00:00Z",
    },
    isLoading: false,
    isError: false,
  }),
  useWriterDocs: () => ({
    data: { items: [], has_more: false },
    isLoading: false,
    isError: false,
  }),
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

// Mount-counting editor stub: mounts record the content seen AT MOUNT (a
// remount — not a re-render — is the signal this test pins), unmounts are
// counted.
vi.mock("@/components/workspace/writer/tiptap-editor", async () => {
  const { useEffect, useRef } = await import("react");
  return {
    TiptapEditor: ({ content }: { content?: string }) => {
      const mountContent = useRef(content ?? "");
      useEffect(() => {
        h.editorMounts.push(mountContent.current);
        return () => {
          h.editorUnmounts += 1;
        };
      }, []);
      return <div data-testid="tiptap-stub" />;
    },
  };
});

// 其余面板/弹层 stub：测试只关心编辑器子树的挂载/卸载
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

describe("B1-T1: WriterEditor remounts on docId change", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    h.editorMounts.length = 0;
    h.editorUnmounts = 0;
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

  it("switching docId unmounts the old editor and mounts a fresh one (key={docId})", async () => {
    const qc = new QueryClient();
    const render = (docId: string) =>
      act(async () => {
        root.render(
          <QueryClientProvider client={qc}>
            <WriterPage docId={docId} />
          </QueryClientProvider>,
        );
      });

    await render("doc_1");
    // Flush the first-load effect (async IIFE → setDocLoaded) so the editor mounts.
    await act(async () => {
      await Promise.resolve();
    });
    expect(container.querySelector('[data-testid="tiptap-stub"]')).toBeTruthy();
    expect(h.editorMounts).toEqual(["# doc_1\n正文"]);
    expect(h.editorUnmounts).toBe(0);

    await render("doc_2");
    await act(async () => {
      await Promise.resolve();
    });

    // key={docId} → React unmounts the doc_1 WriterEditor subtree and mounts a
    // fresh one for doc_2. Without the key this stays 1 mount / 0 unmounts
    // (same instance re-rendered with a new docId prop) — the leak this
    // regression guard pins.
    expect(h.editorUnmounts).toBe(1);
    expect(h.editorMounts).toEqual(["# doc_1\n正文", "# doc_2\n正文"]);
    expect(container.querySelector('[data-testid="tiptap-stub"]')).toBeTruthy();
  });
});
