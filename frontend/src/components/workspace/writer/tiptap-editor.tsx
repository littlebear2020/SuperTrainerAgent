"use client";

import { CodeBlock } from "@tiptap/extension-code-block";
import { Placeholder } from "@tiptap/extension-placeholder";
import { Table } from "@tiptap/extension-table";
import { TableCell } from "@tiptap/extension-table-cell";
import { TableHeader } from "@tiptap/extension-table-header";
import { TableRow } from "@tiptap/extension-table-row";
import { TaskItem } from "@tiptap/extension-task-item";
import { TaskList } from "@tiptap/extension-task-list";
import { EditorContent, type Editor, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { useEffect, useRef } from "react";
import { toast } from "sonner";
import { Markdown } from "tiptap-markdown";

import "@/components/workspace/writer/tiptap-styles.css";
import { uploadImage } from "@/core/writer/assets-api";
import { sanitizeMarkdownHtml } from "@/core/writer/sanitize-markdown";

import { CustomImage } from "./tiptap-image";
import { MathInline } from "./tiptap-math";
import { createImagePasteDropHandlers } from "./tiptap-paste-images";

interface TiptapEditorProps {
  content: string;
  onChange: (md: string) => void;
  onEditorReady?: (editor: Editor) => void;
  placeholder?: string;
}

export function TiptapEditor({
  content,
  onChange,
  onEditorReady,
  placeholder,
}: TiptapEditorProps) {
  // B1-T5: Refactored content sync to use a single pending-external ref.
  // Multiple rapid external updates (version restore + draft recovery arriving
  // close together) coalesce: only the LATEST value is applied via microtask.
  // Local edits are detected via string === comparison (lastSyncedContentRef)
  // — the content prop round-trip from onChange→parent→re-render is skipped
  // because lastSyncedContentRef matches.
  //
  // I5 fix: removed the syncSeqRef/lastAppliedSeqRef mechanism — it was
  // ineffective (seq was incremented before the comparison, so the guard
  // never triggered). The string comparison + pendingExternalRef coalescing
  // are the real dedup mechanisms.
  const pendingExternalRef = useRef<string | null>(null);
  const microtaskScheduledRef = useRef(false);
  const lastSyncedContentRef = useRef(content);

  const editor = useEditor({
    immediatelyRender: false,
    extensions: [
      StarterKit.configure({
        codeBlock: false,
        heading: { levels: [1, 2, 3, 4, 5, 6] },
        link: { openOnClick: false },
      }),
      Table.configure({ resizable: true }),
      TableRow,
      TableCell,
      TableHeader,
      TaskList,
      TaskItem.configure({ nested: true }),
      CodeBlock,
      CustomImage,
      Placeholder.configure({ placeholder: placeholder ?? "在此编写..." }),
      MathInline,
      Markdown.configure({
        html: true,
        transformPastedText: true,
        transformCopiedText: true,
      }),
    ],
    // SEC-M5: initial mount goes through the same DOMPurify sanitize as the
    // external-update path below (html:true is needed for img width/height).
    content: sanitizeMarkdownHtml(content),
    onUpdate: ({ editor: e }) => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const md = (e.storage as any).markdown.getMarkdown() as string;
      lastSyncedContentRef.current = md;
      onChange(md);
    },
    onCreate: ({ editor: e }) => {
      lastSyncedContentRef.current = content;
      onEditorReady?.(e);
    },
    editorProps: {
      attributes: {
        class: "tiptap-content outline-none",
      },
      // Paste a local image / screenshot or drag a file in → upload + insert.
      // Non-image content falls through to TipTap's default handling.
      ...createImagePasteDropHandlers(uploadImage, (file) =>
        toast.error(`图片「${file.name}」上传失败，请重试`),
      ),
    },
  });

  // B1-T5: Sync external content changes into the editor.
  // useEditor only reads `content` at initialization; subsequent prop
  // changes (e.g. server data loading, version restore) need explicit sync.
  //
  // Local edits (onUpdate) are detected via string === comparison:
  // onUpdate sets lastSyncedContentRef, and the round-trip content prop
  // matches it → skip. External updates don't match → apply via microtask.
  // Multiple rapid external updates coalesce (only the latest is applied).
  useEffect(() => {
    if (!editor) return;

    // Skip if content hasn't actually changed (catches round-trip from onUpdate)
    if (content === lastSyncedContentRef.current) return;
    if (!content && content !== "") return;

    // Store the latest external content and schedule a single microtask
    // that consumes whatever is newest (multiple rapid updates coalesce).
    pendingExternalRef.current = content;
    if (microtaskScheduledRef.current) return;
    microtaskScheduledRef.current = true;

    queueMicrotask(() => {
      microtaskScheduledRef.current = false;
      const pending = pendingExternalRef.current;
      if (pending === null || !editor) return;
      pendingExternalRef.current = null;

      // B1-T5: preserve cursor position across external setContent.
      // setContent resets cursor to doc start by default; clamp saved
      // position to the new doc size to avoid out-of-range errors.
      // B6-T8: sanitize HTML content with DOMPurify before setting it
      // (html:true is needed for img width/height, but allows XSS).
      const sanitized = sanitizeMarkdownHtml(pending);
      const savedSelection = editor.state.selection;
      const savedFrom = savedSelection.from;

      editor.commands.setContent(sanitized);
      lastSyncedContentRef.current = pending;

      // Restore cursor (clamped to new doc size)
      const newDocSize = editor.state.doc.content.size;
      const clampedPos = Math.min(savedFrom, newDocSize);
      editor.commands.setTextSelection(clampedPos);
    });
  }, [editor, content]);

  if (!editor) return null;

  return (
    <div className="tiptap-wrapper h-full overflow-y-auto">
      <EditorContent editor={editor} />
    </div>
  );
}
