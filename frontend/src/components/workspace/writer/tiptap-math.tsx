"use client";

import { mergeAttributes, Node } from "@tiptap/core";
import type { Node as ProseNode } from "@tiptap/pm/model";
import {
  NodeViewWrapper,
  ReactNodeViewRenderer,
  type NodeViewWrapperProps,
} from "@tiptap/react";
import katex from "katex";
import { useCallback, useRef, useState } from "react";

// ── React NodeView for inline math ─────────────────────────────────────────

function MathComponent({ node, updateAttributes }: NodeViewWrapperProps) {
  const latex = (node.attrs.latex as string) || "";
  const [editing, setEditing] = useState(!latex);
  const inputRef = useRef<HTMLInputElement>(null);

  const renderKatex = useCallback(() => {
    try {
      return katex.renderToString(latex, {
        throwOnError: false,
        displayMode: false,
      });
    } catch {
      // SEC-M5: this string lands in dangerouslySetInnerHTML — escape the raw
      // latex so a parse failure can't inject HTML into the document.
      const escaped = latex
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;");
      return `<span class="text-rose-500">${escaped || "空公式"}</span>`;
    }
  }, [latex]);

  const commit = useCallback(
    (value: string) => {
      updateAttributes({ latex: value });
      if (value) setEditing(false);
    },
    [updateAttributes],
  );

  return (
    <NodeViewWrapper as="span" className="inline">
      {editing ? (
        <span
          className="inline-flex items-center rounded border border-[var(--primary)]/40 bg-[var(--muted)]/50 px-1"
          suppressContentEditableWarning
        >
          <input
            ref={inputRef}
            defaultValue={latex}
            autoFocus
            className="w-28 bg-transparent px-1 text-xs outline-none"
            placeholder="LaTeX 公式"
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                commit(e.currentTarget.value);
              } else if (e.key === "Escape") {
                e.preventDefault();
                if (!latex) {
                  updateAttributes({ latex: "" });
                }
                setEditing(false);
              }
            }}
            onBlur={(e) => commit(e.target.value)}
          />
        </span>
      ) : (
        <span
          role="button"
          className="mx-0.5 cursor-pointer rounded px-0.5 align-middle hover:bg-[var(--muted)]/50"
          title={latex}
          onClick={() => setEditing(true)}
          suppressContentEditableWarning
          dangerouslySetInnerHTML={{ __html: renderKatex() }}
        />
      )}
    </NodeViewWrapper>
  );
}

// ── markdown-it inline rule for $...$ math ──────────────────────────────────

function mathInlineRule(
  state: {
    pos: number;
    posMax: number;
    src: string;
    push: (
      type: string,
      tag: string,
      nesting: number,
    ) => { content: string; markup: string };
  },
  silent: boolean,
): boolean {
  const start = state.pos;
  const max = state.posMax;

  if (state.src.charCodeAt(start) !== 0x24 /* $ */) return false;

  let pos = start + 1;
  let found = false;

  while (pos < max) {
    if (state.src.charCodeAt(pos) === 0x24 /* $ */) {
      found = true;
      break;
    }
    if (state.src.charCodeAt(pos) === 0x5c /* \ */) {
      pos += 1;
    }
    pos += 1;
  }

  if (!found) return false;
  if (pos === start + 1) return false;

  const latex = state.src.slice(start + 1, pos);

  if (!silent) {
    const token = state.push("math_inline", "math", 0);
    token.content = latex;
    token.markup = "$";
  }

  state.pos = pos + 1;
  return true;
}

// ── TipTap Node extension with tiptap-markdown integration ─────────────────

declare module "@tiptap/core" {
  interface Commands<ReturnType> {
    mathInline: {
      insertMath: (latex?: string) => ReturnType;
    };
  }
}

export const MathInline = Node.create({
  name: "mathInline",
  group: "inline",
  inline: true,
  atom: true,

  addAttributes() {
    return {
      latex: {
        default: "",
        parseHTML: (el) => el.getAttribute("data-latex") ?? "",
        renderHTML: (attrs) => ({
          "data-latex": attrs.latex,
        }),
      },
    };
  },

  parseHTML() {
    return [{ tag: 'span[data-type="math"]' }];
  },

  renderHTML({ HTMLAttributes }) {
    return ["span", mergeAttributes(HTMLAttributes, { "data-type": "math" })];
  },

  addNodeView() {
    return ReactNodeViewRenderer(MathComponent);
  },

  addCommands() {
    return {
      insertMath:
        (latex?: string) =>
        ({ commands }) => {
          return commands.insertContent({
            type: this.name,
            attrs: { latex: latex ?? "" },
          });
        },
    };
  },

  addKeyboardShortcuts() {
    return {
      "Mod-Shift-m": () => this.editor.commands.insertMath(""),
    };
  },

  addStorage() {
    return {
      markdown: {
        serialize(state: { write: (s: string) => void }, node: ProseNode) {
          state.write(`$${node.attrs.latex}$`);
        },
        parse: {
          // Register the markdown-it inline rule via setup hook
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          setup(md: any) {
            md.inline.ruler.after("escape", "math_inline", mathInlineRule);
            md.renderer.rules.math_inline = (
              tokens: Array<{ content: string }>,
              idx: number,
            ) => {
              const latex = tokens[idx]?.content ?? "";
              return `<span data-type="math" data-latex="${md.utils.escapeHtml(latex)}"></span>`;
            };
          },
        },
      },
    };
  },
});
