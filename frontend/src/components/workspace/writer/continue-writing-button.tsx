"use client";

import type { Editor } from "@tiptap/react";
import { Sparkles, X } from "lucide-react";
import { memo, useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { buildSelectionContext, streamAssist } from "@/core/writer/api";
import type { DocType } from "@/core/writer/types";
import { cn } from "@/lib/utils";

interface ContinueWritingButtonProps {
  editor: Editor | null;
  docType: DocType;
  docTitle: string;
  markdown: string;
}

/**
 * B3-T3: AI continue-writing button (decision U1).
 *
 * Renders alongside the EditorToolbar (not inside it -- toolbar items are
 * synchronous format commands, not async streaming). Clicking opens a small
 * input for an optional instruction; confirming starts a streamAssist call
 * with op=continue. The generated text is inserted at the cursor position.
 */
function ContinueWritingButtonInner({
  editor,
  docType,
  docTitle,
  markdown,
}: ContinueWritingButtonProps) {
  const [open, setOpen] = useState(false);
  const [instruction, setInstruction] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  // Abort in-flight stream on unmount (same pattern as use-from-scratch B1-T3)
  useEffect(
    () => () => {
      abortRef.current?.abort();
    },
    [],
  );

  const handleContinue = useCallback(async () => {
    if (!editor) return;

    // Get cursor position and preceding text (up to 3000 chars for context)
    const { from } = editor.state.selection;
    const textBefore = editor.state.doc.textBetween(
      Math.max(0, from - 3000),
      from,
      "\n",
    );

    if (!textBefore.trim()) {
      toast.error("请将光标放在已有文本后方再使用续写");
      return;
    }

    setIsStreaming(true);
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const ctx = buildSelectionContext(docTitle, markdown, textBefore);
      let accumulated = "";
      for await (const chunk of streamAssist(
        {
          op: "continue",
          text: textBefore,
          context: ctx,
          doc_type: docType,
          full_doc: false,
          ...(instruction ? { instruction } : {}),
        },
        controller.signal,
      )) {
        if (controller.signal.aborted) return;
        accumulated += chunk;
      }

      if (accumulated.trim()) {
        // I7: cursor context guard — if cursor is inside a code block or
        // table cell, inserting markdown text would corrupt the structure.
        // Fall back to appending at the end of the document.
        // Uses editor.isActive() (codebase pattern from editor-toolbar.tsx).
        const inCodeBlock = editor.isActive("codeBlock");
        const inTable = editor.isActive("table");
        if (inCodeBlock || inTable) {
          editor.chain().focus("end").insertContent(`\n\n${accumulated}`).run();
          toast.info("已将续写内容追加到文档末尾");
        } else {
          editor
            .chain()
            .focus()
            .insertContentAt(
              {
                from: editor.state.selection.from,
                to: editor.state.selection.from,
              },
              `\n\n${accumulated}`,
            )
            .run();
          toast.success("续写完成");
        }
      } else {
        toast.warning("AI 未返回续写内容，请重试");
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      toast.error(`续写失败：${err instanceof Error ? err.message : "请重试"}`);
    } finally {
      setIsStreaming(false);
      setOpen(false);
      setInstruction("");
    }
  }, [editor, docType, docTitle, markdown, instruction]);

  const handleCancel = useCallback(() => {
    abortRef.current?.abort();
    setIsStreaming(false);
    setOpen(false);
    setInstruction("");
  }, []);

  if (!editor) return null;

  return (
    <div className="relative flex items-center">
      <button
        onClick={() => setOpen((v) => !v)}
        disabled={isStreaming}
        title="AI 续写"
        className={cn(
          "inline-flex h-7 items-center gap-1 rounded px-2 text-xs transition-colors",
          "text-[var(--muted-foreground)] hover:bg-[var(--muted)] hover:text-[var(--foreground)]",
          isStreaming && "opacity-50",
        )}
      >
        <Sparkles className="h-3.5 w-3.5" />
        续写
      </button>

      {open && (
        <div className="absolute top-full right-0 z-30 mt-1 w-72 rounded-lg border border-[var(--border)] bg-[var(--card)] p-2.5 shadow-xl">
          <div className="mb-1.5 flex items-center justify-between">
            <span className="text-xs font-medium text-[var(--foreground)]">
              AI 续写
            </span>
            <button
              onClick={handleCancel}
              className="rounded p-0.5 text-[var(--muted-foreground)] hover:bg-[var(--muted)]"
            >
              <X size={14} />
            </button>
          </div>
          <input
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !isStreaming) {
                e.preventDefault();
                void handleContinue();
              }
            }}
            placeholder="可选：输入续写要求..."
            className="mb-2 h-8 w-full rounded-md border border-[var(--border)] bg-[var(--background)] px-2 text-xs text-[var(--foreground)] outline-none placeholder:text-[var(--muted-foreground)]"
            disabled={isStreaming}
            autoFocus
          />
          <button
            onClick={handleContinue}
            disabled={isStreaming}
            className="h-7 w-full rounded-md bg-[var(--primary)] text-xs text-white transition-opacity disabled:opacity-50"
          >
            {isStreaming ? "续写中..." : "开始续写"}
          </button>
        </div>
      )}
    </div>
  );
}

// 2026-10-06 性能批：memo 化——markdown 变化（击键）时 props 未变的子树跳过重渲
export const ContinueWritingButton = memo(ContinueWritingButtonInner);
