/* eslint-disable @typescript-eslint/no-explicit-any -- ProseMirror EditorView / ClipboardEvent stubs are intentionally loose in tests */
/**
 * Image paste/drop extraction tests (issue ①).
 *
 * `extractImageFiles` filters a clipboard/drop DataTransfer down to image
 * files only (skipping text, PDFs, etc.), so the editor inserts only images.
 */

import { describe, expect, it, test, vi } from "vitest";

import {
  createImagePasteDropHandlers,
  extractImageFiles,
} from "@/components/workspace/writer/tiptap-paste-images";

test("returns only image files from a DataTransfer, preserving order", () => {
  const dt = new DataTransfer();
  dt.items.add(new File(["a"], "a.png", { type: "image/png" }));
  dt.items.add(new File(["b"], "notes.txt", { type: "text/plain" }));
  dt.items.add(new File(["c"], "c.jpg", { type: "image/jpeg" }));

  const files = extractImageFiles(dt);
  expect(files.map((f) => f.name)).toEqual(["a.png", "c.jpg"]);
});

test("returns [] when the DataTransfer is null or empty", () => {
  expect(extractImageFiles(null)).toEqual([]);
  expect(extractImageFiles(new DataTransfer())).toEqual([]);
});

test("returns [] when only non-image files are present", () => {
  const dt = new DataTransfer();
  dt.items.add(new File(["x"], "doc.pdf", { type: "application/pdf" }));
  expect(extractImageFiles(dt)).toEqual([]);
});

// ── P1-11: a failed paste/drop upload must surface via onError, not silence ──

function fakeView() {
  return {
    state: {
      schema: { nodes: { image: { create: (attrs: unknown) => ({ attrs }) } } },
      tr: { replaceSelectionWith: (n: unknown) => ({ n }) },
    },
    dispatch: vi.fn(),
  } as any;
}

function pasteEvent(file: File) {
  return {
    clipboardData: {
      items: [{ kind: "file", getAsFile: () => file }],
      files: [],
    },
    preventDefault: vi.fn(),
  } as any;
}

describe("createImagePasteDropHandlers", () => {
  it("reports a failed upload via onError instead of silence (P1-11)", async () => {
    const upload = vi.fn().mockRejectedValue(new Error("413"));
    const onError = vi.fn();
    const handlers = createImagePasteDropHandlers(upload, onError);
    const file = new File(["x"], "shot.png", { type: "image/png" });
    const handled = handlers.handlePaste(fakeView(), pasteEvent(file));
    expect(handled).toBe(true);
    await new Promise((r) => setTimeout(r, 0));
    expect(onError).toHaveBeenCalledWith(file);
  });
});
