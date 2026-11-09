"use client";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

/**
 * Generic confirm dialog (W29 — §9.6).
 *
 * Replaces `window.confirm` with a shadcn Dialog styled consistently with the
 * rest of the writer module (matches FillConfirmDialog / ShareDialog). Used
 * via the imperative `useConfirm()` hook (see `dialog-provider.tsx`) which
 * returns `Promise<boolean>`.
 *
 * Props are the structured `ConfirmOptions` (title, description, labels,
 * variant). The provider holds the resolve callback; this component is purely
 * presentational — it calls `onConfirm` / `onCancel` and the provider resolves
 * the promise.
 */

export type ConfirmVariant = "default" | "destructive";

export interface ConfirmOptions {
  title: string;
  description?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Destructive confirms render the confirm button in red. */
  variant?: ConfirmVariant;
}

interface ConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  opts: ConfirmOptions;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  open,
  onOpenChange,
  opts,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const isDestructive = opts.variant === "destructive";
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md" showCloseButton={false}>
        <DialogHeader>
          <DialogTitle>{opts.title}</DialogTitle>
          {opts.description && (
            <DialogDescription>{opts.description}</DialogDescription>
          )}
        </DialogHeader>
        <DialogFooter className="flex-row justify-center gap-2 sm:justify-center">
          <Button variant="outline" size="sm" onClick={onCancel}>
            {opts.cancelLabel ?? "取消"}
          </Button>
          <Button
            variant={isDestructive ? "destructive" : "default"}
            size="sm"
            onClick={onConfirm}
          >
            {opts.confirmLabel ?? "确定"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
