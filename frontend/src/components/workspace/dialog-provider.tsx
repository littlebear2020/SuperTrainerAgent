"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
} from "react";

import { type ConfirmOptions, ConfirmDialog } from "./confirm-dialog";
import { type PromptOptions, PromptDialog } from "./prompt-dialog";

/**
 * Imperative dialog provider (W29 — §9.6).
 *
 * Replaces `window.confirm` / `window.prompt` with Promise-returning hooks
 * backed by shadcn Dialogs. Mount once near the writer module (or globally
 * near `<Toaster />`); then call `useConfirm()` / `usePrompt()` anywhere
 * inside the provider tree.
 *
 * - `useConfirm() → (opts: ConfirmOptions) => Promise<boolean>`
 *   true = confirmed, false = cancelled (backdrop / escape / cancel button).
 * - `usePrompt() → (opts: PromptOptions) => Promise<string | null>`
 *   string = submitted value (may be ""), null = cancelled.
 *
 * The provider holds at most one confirm + one prompt dialog at a time
 * (matching `window.confirm` / `window.prompt` blocking semantics, but
 * non-blocking for the rest of the UI). If a second call is made while a
 * dialog is already open, the first promise resolves with false/null before
 * the new dialog shows.
 */

interface DialogContextValue {
  confirm: (opts: ConfirmOptions) => Promise<boolean>;
  prompt: (opts: PromptOptions) => Promise<string | null>;
}

const DialogContext = createContext<DialogContextValue | null>(null);

interface ConfirmState {
  opts: ConfirmOptions;
  resolve: (value: boolean) => void;
}

interface PromptState {
  opts: PromptOptions;
  resolve: (value: string | null) => void;
}

export function DialogProvider({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const [confirmState, setConfirmState] = useState<ConfirmState | null>(null);
  const [promptState, setPromptState] = useState<PromptState | null>(null);

  const confirm = useCallback(
    (opts: ConfirmOptions): Promise<boolean> =>
      new Promise((resolve) => {
        // If a confirm is already open, reject the previous one first.
        setConfirmState((prev) => {
          prev?.resolve(false);
          return { opts, resolve };
        });
      }),
    [],
  );

  const prompt = useCallback(
    (opts: PromptOptions): Promise<string | null> =>
      new Promise((resolve) => {
        setPromptState((prev) => {
          prev?.resolve(null);
          return { opts, resolve };
        });
      }),
    [],
  );

  const closeConfirm = useCallback((result: boolean) => {
    setConfirmState((prev) => {
      prev?.resolve(result);
      return null;
    });
  }, []);

  const closePrompt = useCallback((result: string | null) => {
    setPromptState((prev) => {
      prev?.resolve(result);
      return null;
    });
  }, []);

  const value = useMemo<DialogContextValue>(
    () => ({ confirm, prompt }),
    [confirm, prompt],
  );

  return (
    <DialogContext.Provider value={value}>
      {children}
      {confirmState && (
        <ConfirmDialog
          open
          onOpenChange={(open) => {
            if (!open) closeConfirm(false);
          }}
          opts={confirmState.opts}
          onConfirm={() => closeConfirm(true)}
          onCancel={() => closeConfirm(false)}
        />
      )}
      {promptState && (
        <PromptDialog
          open
          onOpenChange={(open) => {
            if (!open) closePrompt(null);
          }}
          opts={promptState.opts}
          onSubmit={(v) => closePrompt(v)}
          onCancel={() => closePrompt(null)}
        />
      )}
    </DialogContext.Provider>
  );
}

/** Imperative confirm — returns true if the user confirmed, false if cancelled. */
export function useConfirm() {
  const ctx = useContext(DialogContext);
  if (!ctx) {
    throw new Error("useConfirm must be used within a <DialogProvider>");
  }
  return ctx.confirm;
}

/** Imperative prompt — returns the entered string (may be ""), or null if cancelled. */
export function usePrompt() {
  const ctx = useContext(DialogContext);
  if (!ctx) {
    throw new Error("usePrompt must be used within a <DialogProvider>");
  }
  return ctx.prompt;
}

// ── Pure helpers (for DOM-free unit testing) ──────────────────────────────

/**
 * Build structured `ConfirmOptions` from a plain confirm message.
 *
 * The writer module's legacy `window.confirm(msg)` calls used a single
 * sentence as both title and description. This helper splits them: the
 * message becomes the `description`, and a short `title` is derived (or
 * provided via `overrides.title`). This keeps the dialog readable while
 * preserving the original message text.
 *
 * @param message The original confirm message (becomes `description`).
 * @param overrides Optional fields to override (title, labels, variant).
 */
export function buildConfirmOptions(
  message: string,
  overrides?: Partial<ConfirmOptions>,
): ConfirmOptions {
  return {
    title: overrides?.title ?? "确认操作",
    description: message,
    confirmLabel: overrides?.confirmLabel,
    cancelLabel: overrides?.cancelLabel,
    variant: overrides?.variant,
  };
}

/**
 * Build structured `PromptOptions` from a legacy `window.prompt` call.
 *
 * @param title The dialog title (e.g. "插入链接").
 * @param defaultValue The pre-filled value (e.g. "https://").
 * @param overrides Optional fields to override (label, placeholder).
 */
export function buildPromptOptions(
  title: string,
  defaultValue?: string,
  overrides?: Partial<PromptOptions>,
): PromptOptions {
  return {
    title,
    label: overrides?.label,
    defaultValue: defaultValue ?? overrides?.defaultValue,
    placeholder: overrides?.placeholder,
  };
}
