"use client";

import { type Editor, useEditorState } from "@tiptap/react";
import {
  type LucideIcon,
  Bold,
  CheckSquare,
  Code,
  CodeSquare,
  Heading1,
  Heading2,
  Heading3,
  Heading4,
  Heading5,
  Heading6,
  Image,
  Italic,
  Link,
  List,
  ListOrdered,
  Minus,
  Pi,
  Quote,
  Redo2,
  Strikethrough,
  Table,
  Undo2,
} from "lucide-react";
import { useMemo, useRef } from "react";
import { toast } from "sonner";

import { type PromptOptions } from "@/components/workspace/prompt-dialog";
import { formatFileSize } from "@/core/utils/format";
import {
  isValidUploadFile,
  UPLOAD_ACCEPT_ATTR,
  uploadImage,
} from "@/core/writer/assets-api";
import { cn } from "@/lib/utils";

// ── Types ──────────────────────────────────────────────────────────────────

/**
 * Context passed to toolbar commands (W29 — §9.6).
 *
 * `prompt` is the imperative `usePrompt()` function from `DialogProvider`,
 * injected so toolbar items can open a `PromptDialog` instead of
 * `window.prompt`. Commands that don't need a prompt simply ignore it.
 */
export interface ToolbarCommandContext {
  prompt: (opts: PromptOptions) => Promise<string | null>;
}

interface ToolbarItem {
  id: string;
  icon: LucideIcon;
  title: string;
  type?: "separator";
  isActive?: (editor: Editor) => boolean;
  command?: (editor: Editor, ctx: ToolbarCommandContext) => void;
}

export interface EditorToolbarProps {
  editor: Editor | null;
  className?: string;
  /** Imperative prompt function (from `usePrompt()`). Required for link/image/math inserts. */
  prompt: (opts: PromptOptions) => Promise<string | null>;
}

// ── Toolbar items ──────────────────────────────────────────────────────────

const TOOLBAR_ITEMS: ToolbarItem[] = [
  // Undo / Redo
  {
    id: "undo",
    icon: Undo2,
    title: "撤销",
    command: (e) => e.chain().focus().undo().run(),
  },
  {
    id: "redo",
    icon: Redo2,
    title: "重做",
    command: (e) => e.chain().focus().redo().run(),
  },
  { id: "sep-1", icon: Minus, title: "", type: "separator" },

  // Headings
  {
    id: "h1",
    icon: Heading1,
    title: "标题 1",
    isActive: (e) => e.isActive("heading", { level: 1 }),
    command: (e) => e.chain().focus().toggleHeading({ level: 1 }).run(),
  },
  {
    id: "h2",
    icon: Heading2,
    title: "标题 2",
    isActive: (e) => e.isActive("heading", { level: 2 }),
    command: (e) => e.chain().focus().toggleHeading({ level: 2 }).run(),
  },
  {
    id: "h3",
    icon: Heading3,
    title: "标题 3",
    isActive: (e) => e.isActive("heading", { level: 3 }),
    command: (e) => e.chain().focus().toggleHeading({ level: 3 }).run(),
  },
  {
    id: "h4",
    icon: Heading4,
    title: "标题 4",
    isActive: (e) => e.isActive("heading", { level: 4 }),
    command: (e) => e.chain().focus().toggleHeading({ level: 4 }).run(),
  },
  {
    id: "h5",
    icon: Heading5,
    title: "标题 5",
    isActive: (e) => e.isActive("heading", { level: 5 }),
    command: (e) => e.chain().focus().toggleHeading({ level: 5 }).run(),
  },
  {
    id: "h6",
    icon: Heading6,
    title: "标题 6",
    isActive: (e) => e.isActive("heading", { level: 6 }),
    command: (e) => e.chain().focus().toggleHeading({ level: 6 }).run(),
  },
  { id: "sep-2", icon: Minus, title: "", type: "separator" },

  // Inline formatting
  {
    id: "bold",
    icon: Bold,
    title: "粗体",
    isActive: (e) => e.isActive("bold"),
    command: (e) => e.chain().focus().toggleBold().run(),
  },
  {
    id: "italic",
    icon: Italic,
    title: "斜体",
    isActive: (e) => e.isActive("italic"),
    command: (e) => e.chain().focus().toggleItalic().run(),
  },
  {
    id: "strikethrough",
    icon: Strikethrough,
    title: "删除线",
    isActive: (e) => e.isActive("strike"),
    command: (e) => e.chain().focus().toggleStrike().run(),
  },
  {
    id: "code",
    icon: Code,
    title: "行内代码",
    isActive: (e) => e.isActive("code"),
    command: (e) => e.chain().focus().toggleCode().run(),
  },
  { id: "sep-3", icon: Minus, title: "", type: "separator" },

  // Block elements
  {
    id: "quote",
    icon: Quote,
    title: "引用",
    isActive: (e) => e.isActive("blockquote"),
    command: (e) => e.chain().focus().toggleBlockquote().run(),
  },
  {
    id: "ul",
    icon: List,
    title: "无序列表",
    isActive: (e) => e.isActive("bulletList"),
    command: (e) => e.chain().focus().toggleBulletList().run(),
  },
  {
    id: "ol",
    icon: ListOrdered,
    title: "有序列表",
    isActive: (e) => e.isActive("orderedList"),
    command: (e) => e.chain().focus().toggleOrderedList().run(),
  },
  {
    id: "task",
    icon: CheckSquare,
    title: "任务列表",
    isActive: (e) => e.isActive("taskList"),
    command: (e) => e.chain().focus().toggleTaskList().run(),
  },
  { id: "sep-4", icon: Minus, title: "", type: "separator" },

  // Insert elements
  {
    id: "hr",
    icon: Minus,
    title: "分隔线",
    command: (e) => e.chain().focus().setHorizontalRule().run(),
  },
  {
    id: "table",
    icon: Table,
    title: "表格",
    command: (e) =>
      e
        .chain()
        .focus()
        .insertTable({ rows: 3, cols: 3, withHeaderRow: true })
        .run(),
  },
  {
    id: "link",
    icon: Link,
    title: "链接 (Ctrl+点击打开)",
    isActive: (e) => e.isActive("link"),
    command: (e, ctx) => {
      const prev = e.getAttributes("link").href as string | undefined;
      void ctx
        .prompt({
          title: "插入链接",
          label: "链接地址",
          defaultValue: prev ?? "https://",
        })
        .then((url) => {
          if (url == null) return; // cancelled
          if (url) {
            e.chain().focus().setLink({ href: url }).run();
          } else {
            e.chain().focus().unsetLink().run();
          }
        });
    },
  },
  {
    id: "image",
    icon: Image,
    title: "图片",
    // B6-T6: command is intercepted in the render loop (imageInputRef.current?.click())
    // to trigger file upload instead of the old prompt-for-URL flow.
    command: () => {
      // intercepted by onClick handler — no-op here
    },
  },
  { id: "sep-5", icon: Minus, title: "", type: "separator" },

  // Code block
  {
    id: "codeblock",
    icon: CodeSquare,
    title: "代码块",
    isActive: (e) => e.isActive("codeBlock"),
    command: (e) => e.chain().focus().toggleCodeBlock().run(),
  },
  {
    id: "math",
    icon: Pi,
    title: "数学公式 (Ctrl+Shift+M)",
    isActive: (e) => e.isActive("mathInline"),
    command: (e, ctx) => {
      void ctx
        .prompt({
          title: "插入公式",
          label: "LaTeX 公式",
          defaultValue: "x^2 + y^2 = r^2",
        })
        .then((latex) => {
          if (latex) e.chain().focus().insertMath(latex).run();
        });
    },
  },
];

// ── Component ──────────────────────────────────────────────────────────────

/** B1-T4: Reactive toolbar state via useEditorState.
 * Returns a flat object of isActive booleans + canUndo/canRedo so the
 * toolbar re-renders on every transaction (format toggle, cursor move,
 * undo/redo stack change). Without this, buttons showed stale state
 * because the toolbar wasn't subscribed to editor transactions. */
interface ToolbarState {
  isBold: boolean;
  isItalic: boolean;
  isStrike: boolean;
  isCode: boolean;
  isH1: boolean;
  isH2: boolean;
  isH3: boolean;
  isH4: boolean;
  isH5: boolean;
  isH6: boolean;
  isBlockquote: boolean;
  isBulletList: boolean;
  isOrderedList: boolean;
  isTaskList: boolean;
  isCodeBlock: boolean;
  isLink: boolean;
  isMathInline: boolean;
  canUndo: boolean;
  canRedo: boolean;
}

function selectToolbarState(editor: Editor): ToolbarState {
  return {
    isBold: editor.isActive("bold"),
    isItalic: editor.isActive("italic"),
    isStrike: editor.isActive("strike"),
    isCode: editor.isActive("code"),
    isH1: editor.isActive("heading", { level: 1 }),
    isH2: editor.isActive("heading", { level: 2 }),
    isH3: editor.isActive("heading", { level: 3 }),
    isH4: editor.isActive("heading", { level: 4 }),
    isH5: editor.isActive("heading", { level: 5 }),
    isH6: editor.isActive("heading", { level: 6 }),
    isBlockquote: editor.isActive("blockquote"),
    isBulletList: editor.isActive("bulletList"),
    isOrderedList: editor.isActive("orderedList"),
    isTaskList: editor.isActive("taskList"),
    isCodeBlock: editor.isActive("codeBlock"),
    isLink: editor.isActive("link"),
    isMathInline: editor.isActive("mathInline"),
    canUndo: editor.can().undo(),
    canRedo: editor.can().redo(),
  };
}

/** Map toolbar item id -> ToolbarState boolean key for reactive highlight. */
const ACTIVE_MAP: Record<string, keyof ToolbarState> = {
  bold: "isBold",
  italic: "isItalic",
  strikethrough: "isStrike",
  code: "isCode",
  h1: "isH1",
  h2: "isH2",
  h3: "isH3",
  h4: "isH4",
  h5: "isH5",
  h6: "isH6",
  quote: "isBlockquote",
  ul: "isBulletList",
  ol: "isOrderedList",
  task: "isTaskList",
  codeblock: "isCodeBlock",
  link: "isLink",
  math: "isMathInline",
};

export function EditorToolbar({
  editor,
  className,
  prompt,
}: EditorToolbarProps) {
  const ctx: ToolbarCommandContext = useMemo(() => ({ prompt }), [prompt]);

  // B1-T4: subscribe to editor state so toolbar buttons reflect the current
  // format/cursor position. Returns null when editor is null (before ready).
  const toolbarState = useEditorState({
    editor: editor,
    selector: ({ editor: e }) => (e ? selectToolbarState(e) : null),
  });

  // B6-T6: hidden file input for image upload (replaces the old prompt-for-URL flow)
  const imageInputRef = useRef<HTMLInputElement>(null);

  const handleImageUpload = useMemo(() => {
    return async (file: File) => {
      if (!isValidUploadFile(file)) {
        if (
          file.type === "image/svg+xml" ||
          file.name.toLowerCase().endsWith(".svg")
        ) {
          toast.error("不支持 SVG 格式，请上传 PNG/JPEG/GIF/WebP 图片");
        } else if (file.size > 10 * 1024 * 1024) {
          toast.error(`图片大小 ${formatFileSize(file.size)} 超过 10MB 限制`);
        } else {
          toast.error("不支持的图片格式，仅支持 PNG/JPEG/GIF/WebP");
        }
        return;
      }
      try {
        const result = await uploadImage(file);
        editor
          ?.chain()
          .focus()
          .setImage({ src: result.url, alt: file.name })
          .run();
      } catch {
        toast.error("图片上传失败，请重试");
      }
    };
  }, [editor]);

  return (
    <div
      className={cn(
        "border-border/50 flex shrink-0 items-center gap-0.5 overflow-x-auto border-b px-3 py-1.5",
        className,
      )}
    >
      {/* B6-T6: hidden file input for image upload */}
      <input
        ref={imageInputRef}
        type="file"
        accept={UPLOAD_ACCEPT_ATTR}
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) void handleImageUpload(file);
          e.target.value = ""; // reset so same file can be selected again
        }}
      />
      {TOOLBAR_ITEMS.map((item) => {
        if (item.type === "separator") {
          return (
            <div
              key={item.id}
              className="mx-1 h-4 w-px shrink-0 bg-[var(--border)]"
            />
          );
        }
        const Icon = item.icon;
        // B1-T4: use reactive state from useEditorState instead of calling
        // isActive on each render (which only fires on re-render, not on
        // editor transactions). Fall back to item.isActive for items not
        // in ACTIVE_MAP (none currently, but defensive).
        const activeKey = ACTIVE_MAP[item.id];
        const active =
          toolbarState && activeKey
            ? toolbarState[activeKey]
            : editor && item.isActive?.(editor);
        // B1-T4: disable undo/redo when the stack is empty.
        // Uses || not ??: the first expression is `false` (not nullish) when
        // item.id !== "undo", so ?? short-circuits and redo is never checked.
        const isDisabled = Boolean(
          // eslint-disable-next-line @typescript-eslint/prefer-nullish-coalescing
          (item.id === "undo" && toolbarState && !toolbarState.canUndo) ||
          (item.id === "redo" && toolbarState && !toolbarState.canRedo),
        );
        return (
          <button
            key={item.id}
            title={item.title}
            onClick={() => {
              if (!editor || isDisabled) return;
              // B6-T6: image button triggers file upload instead of prompt-for-URL
              if (item.id === "image") {
                imageInputRef.current?.click();
                return;
              }
              item.command?.(editor, ctx);
            }}
            disabled={!!isDisabled}
            className={cn(
              "inline-flex h-7 w-7 shrink-0 items-center justify-center rounded transition-colors",
              active
                ? "bg-[var(--primary)]/15 text-[var(--primary)]"
                : "text-[var(--muted-foreground)] hover:bg-[var(--muted)] hover:text-[var(--foreground)]",
              isDisabled && "opacity-40",
            )}
          >
            <Icon className="h-3.5 w-3.5" />
          </button>
        );
      })}
    </div>
  );
}
