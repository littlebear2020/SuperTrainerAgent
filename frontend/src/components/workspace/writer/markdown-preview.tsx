/* eslint-disable @typescript-eslint/no-explicit-any -- markdown AST tokens use loose typing; tighten per-renderer later */
"use client";

import React, { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import rehypeRaw from "rehype-raw";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

import { streamdownSanitizeSchema } from "@/core/streamdown";
import { cn } from "@/lib/utils";

// ── Types ──────────────────────────────────────────────────────────────────

export interface MarkdownPreviewProps {
  /** Markdown source text */
  content: string;
  className?: string;
}

// ── Helpers ────────────────────────────────────────────────────────────────

function extractText(children: React.ReactNode): string {
  return React.Children.toArray(children)
    .map((child) => {
      if (typeof child === "string" || typeof child === "number")
        return String(child);
      if (React.isValidElement<{ children?: React.ReactNode }>(child))
        return extractText(child.props.children);
      return "";
    })
    .join("");
}

function headingId(children: React.ReactNode): string | undefined {
  const text = extractText(children)
    .toLowerCase()
    .replace(/[^\w\s-]/g, "")
    .replace(/\s+/g, "-");
  return text || undefined;
}

// ── Shiki code highlighter ──────────────────────────────────────────────────

const SHIKI_LANGS = [
  "javascript",
  "typescript",
  "python",
  "shell",
  "bash",
  "markdown",
  "json",
  "yaml",
  "css",
  "html",
];

function resolveLang(className?: string): string {
  if (!className) return "text";
  const m = /language-(\w+)/.exec(className);
  if (!m) return "text";
  const lang = m[1]!.toLowerCase();
  // Normalize aliases
  if (lang === "js") return "javascript";
  if (lang === "ts") return "typescript";
  if (lang === "sh" || lang === "zsh") return "shell";
  if (lang === "yml") return "yaml";
  return SHIKI_LANGS.includes(lang) ? lang : "text";
}

// 2026-10-06 性能批：模块级 highlighter 单例（getOrCreate）——修前每个代码块
// 各自 createHighlighter（整库语法装配）+ 用完 dispose，滚动长文时反复重建。
// 单例随应用存续，失败时清空缓存允许下次重试。
let highlighterPromise: Promise<unknown> | null = null;
function getSharedHighlighter() {
  if (!highlighterPromise) {
    highlighterPromise = import("shiki").then((shiki) =>
      shiki.createHighlighter({ themes: ["github-dark"], langs: SHIKI_LANGS }),
    );
    highlighterPromise.catch(() => {
      highlighterPromise = null;
    });
  }
  return highlighterPromise as Promise<{
    codeToHtml: (code: string, opts: { lang: string; theme: string }) => string;
  }>;
}

function ShikiCode({
  code,
  lang,
  className: wrapClass,
}: {
  code: string;
  lang: string;
  className?: string;
}) {
  const [html, setHtml] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const highlighter = await getSharedHighlighter();
        if (cancelled) return;
        const resolved = SHIKI_LANGS.includes(lang) ? lang : "text";
        setHtml(
          highlighter.codeToHtml(code, {
            lang: resolved,
            theme: "github-dark",
          }),
        );
      } catch {
        // Fallback: plain text
        setHtml(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [code, lang]);

  if (html) {
    return (
      <div
        className={cn(
          "md-code-block overflow-hidden rounded-xl border border-[var(--border)]",
          wrapClass,
        )}
        dangerouslySetInnerHTML={{ __html: html }}
      />
    );
  }

  // Fallback: plain text rendering
  return (
    <div
      className={cn(
        "md-code-block overflow-hidden rounded-xl border border-[var(--border)] bg-[#1f2937]",
        wrapClass,
      )}
    >
      <pre className="overflow-x-auto p-4 text-sm leading-relaxed text-[#e5e7eb]">
        <code>{code}</code>
      </pre>
    </div>
  );
}

// ── Sanitize schema (P0-2) ────────────────────────────────────────────────

// Raw HTML is allowed (docs may carry legacy annotation spans — D-3 removed
// the feature, so they now render as plain spans with their data attrs kept),
// but anything the sanitizer doesn't whitelist is stripped. The schema itself
// lives in core/streamdown/plugins.ts (SEC-H1) so all raw-HTML render paths
// share one whitelist.

// ── Component ──────────────────────────────────────────────────────────────

export function MarkdownPreview({ content, className }: MarkdownPreviewProps) {
  const remarkPlugins = useMemo(
    () => [remarkGfm, [remarkMath, { singleDollarTextMath: true }]] as any,
    [],
  );

  const rehypePlugins = useMemo(
    () =>
      [
        rehypeRaw,
        [rehypeSanitize, streamdownSanitizeSchema],
        [rehypeKatex, { output: "html" }],
      ] as any,
    [],
  );

  const textColor = "text-[var(--foreground)]";
  const gap = "my-4";
  const cellPad = "px-3 py-2";

  const components = useMemo(() => {
    const headingBase = "scroll-mt-20 font-sans tracking-tight";

    return {
      h1: ({ children, node: _node, ...props }: any) => (
        <h1
          id={headingId(children)}
          className={cn(
            headingBase,
            "text-2xl font-bold",
            textColor,
            "mt-8 mb-4",
          )}
          {...props}
        >
          {children}
        </h1>
      ),
      h2: ({ children, node: _node, ...props }: any) => (
        <h2
          id={headingId(children)}
          className={cn(
            headingBase,
            "text-xl font-semibold",
            textColor,
            "mt-7 mb-3",
          )}
          {...props}
        >
          {children}
        </h2>
      ),
      h3: ({ children, node: _node, ...props }: any) => (
        <h3
          id={headingId(children)}
          className={cn(
            headingBase,
            "text-lg font-semibold",
            textColor,
            "mt-6 mb-2.5",
          )}
          {...props}
        >
          {children}
        </h3>
      ),
      h4: ({ children, node: _node, ...props }: any) => (
        <h4
          id={headingId(children)}
          className={cn(
            headingBase,
            "text-base font-semibold",
            textColor,
            "mt-5 mb-2",
          )}
          {...props}
        >
          {children}
        </h4>
      ),
      h5: ({ children, node: _node, ...props }: any) => (
        <h5
          id={headingId(children)}
          className={cn(
            headingBase,
            "text-sm font-semibold",
            textColor,
            "mt-4 mb-2",
          )}
          {...props}
        >
          {children}
        </h5>
      ),
      h6: ({ children, node: _node, ...props }: any) => (
        <h6
          id={headingId(children)}
          className={cn(
            headingBase,
            "text-sm font-semibold tracking-wide text-[var(--muted-foreground)] uppercase",
            "mt-4 mb-2",
          )}
          {...props}
        >
          {children}
        </h6>
      ),
      p: ({ children, node: _node, ...props }: any) => (
        <p className={cn("mb-3 last:mb-0", textColor)} {...props}>
          {children}
        </p>
      ),
      ul: ({ children, node: _node, ...props }: any) => (
        <ul
          className={cn("my-2 ml-6 list-disc space-y-1", textColor)}
          {...props}
        >
          {children}
        </ul>
      ),
      ol: ({ children, node: _node, ...props }: any) => (
        <ol
          className={cn("my-2 ml-6 list-decimal space-y-1", textColor)}
          {...props}
        >
          {children}
        </ol>
      ),
      li: ({ children, node: _node, ...props }: any) => (
        <li className="leading-relaxed" {...props}>
          {children}
        </li>
      ),
      blockquote: ({ children, node: _node, ...props }: any) => (
        <blockquote
          className={cn(
            gap,
            "border-l-[3px] border-[var(--muted-foreground)] pl-4 text-[var(--muted-foreground)] italic [&>p]:mb-1",
          )}
          {...props}
        >
          {children}
        </blockquote>
      ),
      pre: ({ children, ...props }: any) => <>{children}</>,
      code: ({
        children,
        className: codeClassName,
        node: _node,
        ...props
      }: any) => {
        const raw = String(children).replace(/\n$/, "");
        // Fenced code block — use shiki for highlighting
        if (raw.includes("\n") || codeClassName) {
          const lang = resolveLang(codeClassName);
          return <ShikiCode code={raw} lang={lang} className={cn(gap)} />;
        }
        // Inline code
        return (
          <code
            className="rounded bg-[var(--muted)] px-1.5 py-0.5 font-mono text-[0.875em] text-[var(--foreground)]"
            {...props}
          >
            {children}
          </code>
        );
      },
      a: ({ href, children, node: _node, ...props }: any) => (
        <a
          href={href}
          target={href?.startsWith("http") ? "_blank" : undefined}
          rel={href?.startsWith("http") ? "noopener noreferrer" : undefined}
          className="text-[var(--primary)] underline underline-offset-2 transition-colors hover:text-[var(--primary)]/70"
          {...props}
        >
          {children}
        </a>
      ),
      img: ({ src, alt, node: _node, ...props }: any) => (
        <img
          src={src}
          alt={alt ?? ""}
          loading="lazy"
          className={cn(
            "inline-block max-w-full rounded-lg border border-[var(--border)]",
            gap,
          )}
          {...props}
        />
      ),
      table: ({ children, node: _node, ...props }: any) => (
        <div
          className={cn(
            "overflow-x-auto rounded-lg border border-[var(--border)] shadow-sm",
            gap,
          )}
        >
          <table
            className="min-w-full divide-y divide-[var(--border)] text-sm"
            {...props}
          >
            {children}
          </table>
        </div>
      ),
      thead: ({ node: _node, ...props }: any) => (
        <thead className="bg-[var(--muted)]" {...props} />
      ),
      th: ({ node: _node, ...props }: any) => (
        <th
          className={cn(
            "border-b border-[var(--border)] text-left font-semibold text-[var(--foreground)]",
            cellPad,
          )}
          {...props}
        />
      ),
      tbody: ({ node: _node, ...props }: any) => (
        <tbody
          className="divide-y divide-[var(--border)] bg-[var(--card)]"
          {...props}
        />
      ),
      td: ({ node: _node, ...props }: any) => (
        <td
          className={cn(
            "border-b border-[var(--border)] text-[var(--muted-foreground)]",
            cellPad,
          )}
          {...props}
        />
      ),
      tr: ({ node: _node, ...props }: any) => (
        <tr
          className="transition-colors hover:bg-[var(--muted)]/60"
          {...props}
        />
      ),
      hr: ({ node: _node, ...props }: any) => (
        <hr
          className={cn("h-px border-none bg-[var(--border)]", gap)}
          {...props}
        />
      ),
      input: ({ type, checked, node: _node, ...props }: any) =>
        type === "checkbox" ? (
          <input
            type="checkbox"
            checked={checked}
            readOnly
            className="mr-2 h-4 w-4 rounded border-[var(--border)] align-middle accent-[var(--primary)]"
            {...props}
          />
        ) : null,
      span: ({ children, node: _node, ...props }: any) => (
        <span {...props}>{children}</span>
      ),
    };
  }, [cellPad, gap, textColor]);

  return (
    <div className={cn("md-renderer max-w-none", "prose font-sans", className)}>
      <ReactMarkdown
        remarkPlugins={remarkPlugins}
        rehypePlugins={rehypePlugins}
        components={components}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
