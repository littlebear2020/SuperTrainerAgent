/**
 * clear-document helper tests.
 *
 * Bug: the old `handleClear` only called `editor.commands.clearContent()` and
 * leaned on the implicit `clearContent → onUpdate → setMarkdown → autosave`
 * chain. When that chain didn't propagate, the clear was lost on reload. The
 * extracted `clearDocument` helper makes persistence explicit and is tested
 * here with injected fakes (DOM-free).
 *
 * W29 §9.6: `clearDocument` is now async to support Promise-based confirms
 * (useConfirm). Tests use `await` and both sync + async confirm fakes.
 */

import { expect, test, vi } from "vitest";

import { clearDocument } from "@/components/workspace/writer/clear-document";

test("when confirmed (sync), clears the editor AND persists empty content", async () => {
  const clearEditor = vi.fn();
  const persistEmpty = vi.fn();

  await clearDocument({ confirm: () => true, clearEditor, persistEmpty });

  expect(clearEditor).toHaveBeenCalledTimes(1);
  expect(persistEmpty).toHaveBeenCalledTimes(1);
});

test("when confirmed (async), clears the editor AND persists empty content", async () => {
  const clearEditor = vi.fn();
  const persistEmpty = vi.fn();

  await clearDocument({
    confirm: () => Promise.resolve(true),
    clearEditor,
    persistEmpty,
  });

  expect(clearEditor).toHaveBeenCalledTimes(1);
  expect(persistEmpty).toHaveBeenCalledTimes(1);
});

test("when the user cancels the confirm (sync), does nothing", async () => {
  const clearEditor = vi.fn();
  const persistEmpty = vi.fn();

  await clearDocument({ confirm: () => false, clearEditor, persistEmpty });

  expect(clearEditor).not.toHaveBeenCalled();
  expect(persistEmpty).not.toHaveBeenCalled();
});

test("when the user cancels the confirm (async), does nothing", async () => {
  const clearEditor = vi.fn();
  const persistEmpty = vi.fn();

  await clearDocument({
    confirm: () => Promise.resolve(false),
    clearEditor,
    persistEmpty,
  });

  expect(clearEditor).not.toHaveBeenCalled();
  expect(persistEmpty).not.toHaveBeenCalled();
});

test("clears the editor before persisting so isLocalEditRef skips the sync effect", async () => {
  const order: string[] = [];
  await clearDocument({
    confirm: () => true,
    clearEditor: () => order.push("clear"),
    persistEmpty: () => order.push("persist"),
  });

  // clearContent()'s onUpdate sets isLocalEditRef synchronously before the
  // persist-driven re-render, so the editor's content-sync effect is skipped.
  expect(order).toEqual(["clear", "persist"]);
});
