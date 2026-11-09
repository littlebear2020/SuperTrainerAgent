/**
 * Clear-document helper (extracted for DOM-free unit testing).
 *
 * Bug background: the old `handleClear` only called
 * `editor.commands.clearContent()` and relied on the implicit
 * `clearContent → onUpdate → setMarkdown → autosave` chain. When that chain
 * didn't propagate (e.g. stale editor ref in React StrictMode, or onUpdate not
 * firing), the clear was lost on reload. This helper makes persistence
 * explicit: on user confirm it clears the editor AND calls `persistEmpty`
 * (which drives `setMarkdown("")` + localStorage draft + debounced autosave),
 * so the empty state survives a reload regardless of the onUpdate chain.
 *
 * Order matters: `clearEditor` runs before `persistEmpty` so that
 * `clearContent()`'s `onUpdate` sets `isLocalEditRef` synchronously (before the
 * persist-driven re-render), letting the editor's content-sync effect skip the
 * redundant re-sync. See `tiptap-editor.tsx` sync effect.
 */

export interface ClearDocumentOptions {
  /**
   * Returns true if the user confirmed the destructive clear.
   * Supports both sync (`() => boolean`) and async (`() => Promise<boolean>`)
   * confirms so it can back a `useConfirm()` Promise-based dialog (W29 §9.6).
   */
  confirm: () => boolean | Promise<boolean>;
  /** Clear the TipTap editor's visual content (e.g. `editor.commands.clearContent()`). */
  clearEditor: () => void;
  /** Explicitly persist the empty document (`setMarkdown("")` + draft + autosave). */
  persistEmpty: () => void;
}

/**
 * Clear the document after a destructive-action confirmation.
 *
 * No-ops when the user cancels the confirm. Otherwise clears the editor then
 * persists the empty content so the clear survives a reload.
 *
 * Async-aware (W29): `confirm` may return a Promise — `clearDocument` awaits
 * it before proceeding. Callers should `await clearDocument(...)` (fire-and-
 * forget also works since the clear/persist are synchronous side-effects).
 */
export async function clearDocument(opts: ClearDocumentOptions): Promise<void> {
  if (!(await opts.confirm())) return;
  opts.clearEditor();
  opts.persistEmpty();
}
