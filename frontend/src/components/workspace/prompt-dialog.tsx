"use client";

import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";

/**
 * Generic prompt dialog (W29 — §9.6).
 *
 * Replaces `window.prompt` with a shadcn Dialog + text input, styled
 * consistently with the rest of the writer module. Used via the imperative
 * `usePrompt()` hook (see `dialog-provider.tsx`) which returns
 * `Promise<string | null>` (null = cancelled, empty string = submitted blank).
 *
 * Behaviour matches `window.prompt`:
 * - Enter submits the current value.
 * - Escape / backdrop click / close button cancels (returns null).
 * - The input is auto-focused on open.
 * - Submitting an empty string returns "" (not null) — same as window.prompt.
 */

export interface PromptOptions {
  title: string;
  label?: string;
  defaultValue?: string;
  placeholder?: string;
}

interface PromptDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  opts: PromptOptions;
  onSubmit: (value: string) => void;
  onCancel: () => void;
}

export function PromptDialog({
  open,
  onOpenChange,
  opts,
  onSubmit,
  onCancel,
}: PromptDialogProps) {
  const [value, setValue] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  // Reset to default each time the dialog opens (not on every render).
  useEffect(() => {
    if (open) {
      setValue(opts.defaultValue ?? "");
      // Focus after the dialog animation paints.
      const timer = setTimeout(() => inputRef.current?.focus(), 0);
      return () => clearTimeout(timer);
    }
  }, [open, opts.defaultValue]);

  const handleSubmit = () => {
    onSubmit(value);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md" showCloseButton={false}>
        <DialogHeader>
          <DialogTitle>{opts.title}</DialogTitle>
          {opts.label && <DialogDescription>{opts.label}</DialogDescription>}
        </DialogHeader>
        <Input
          ref={inputRef}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={opts.placeholder}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              handleSubmit();
            }
          }}
        />
        <DialogFooter className="flex-row justify-center gap-2 sm:justify-center">
          <Button variant="outline" size="sm" onClick={onCancel}>
            取消
          </Button>
          <Button size="sm" onClick={handleSubmit}>
            确定
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
