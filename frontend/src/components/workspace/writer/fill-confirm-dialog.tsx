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
 * Fill-into-editor confirm dialog (W17 — §5.2E).
 *
 * When the editor is non-empty and the user clicks "填入编辑器" in the
 * FromScratchPanel, this dialog offers three options:
 * - 替换当前内容 (replace): wholesale overwrite (destructive — force-snapshot)
 * - 追加到末尾 (append): preserve existing, append new (non-destructive)
 * - 取消 (cancel): no-op
 *
 * Matches the shadcn Dialog pattern used by ShareDialog / NewDocDialog.
 */
interface FillConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onReplace: () => void;
  onAppend: () => void;
}

export function FillConfirmDialog({
  open,
  onOpenChange,
  onReplace,
  onAppend,
}: FillConfirmDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md" showCloseButton={false}>
        <DialogHeader>
          <DialogTitle>填入编辑器</DialogTitle>
          <DialogDescription>
            编辑器已有内容，请选择如何填入新内容。
          </DialogDescription>
        </DialogHeader>
        <DialogFooter className="flex-row justify-center gap-2 sm:justify-center">
          <Button
            variant="outline"
            size="sm"
            onClick={() => onOpenChange(false)}
          >
            取消
          </Button>
          <Button variant="secondary" size="sm" onClick={onAppend}>
            追加到末尾
          </Button>
          <Button variant="destructive" size="sm" onClick={onReplace}>
            替换当前内容
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
