"use client";

import {
  createContext,
  type ComponentProps,
  isValidElement,
  type ReactNode,
  useContext,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  preprocessStreamdownMarkdown,
  rehypeStreamingListItems,
  streamdownPluginsWithoutRawHtml,
  streamdownSmoothStreamingAnimation,
} from "@/core/streamdown";
import type { ClipboardSafeStreamdownProps } from "@/core/streamdown";
import {
  SafeMessageResponse,
  type StreamdownComponentOverrides,
  toStreamdownComponents,
} from "@/core/streamdown/components";
import { cn } from "@/lib/utils";

import { CitationLink } from "../citations/citation-link";

// （PR-2 3.5：自上游 DeerFlow markdown-content.tsx 同源移植；本地保留
// CitationLink（citation: 链接是本地特有面）。流式渲染策略：useDeferredValue
// 打底 + 50ms 节奏 / 300ms 总窗的平滑揭示（prefers-reduced-motion 旁路）；
// 流式期代码块走纯文本态不高亮（StreamingPre/StreamingCode），完结后交还
// @streamdown/code 的 shiki 高亮。）
export type MarkdownContentProps = {
  content: string;
  isLoading: boolean;
  rehypePlugins?: ClipboardSafeStreamdownProps["rehypePlugins"];
  className?: string;
  remarkPlugins?: ClipboardSafeStreamdownProps["remarkPlugins"];
  components?: StreamdownComponentOverrides;
};

type StreamingCodeProps = ComponentProps<"code"> & {
  node?: unknown;
  children?: ReactNode;
};

const SMOOTH_REVEAL_MIN_DELTA = 80;
const SMOOTH_REVEAL_CADENCE_MS = 50;
const SMOOTH_REVEAL_MIN_CHARS_PER_COMMIT = 64;
const SMOOTH_REVEAL_DURATION_MS = 300;

const StreamingCodeBlockContext = createContext(false);

function useSmoothStreamingContent(content: string, isLoading: boolean) {
  const initialContent =
    isLoading && content.length >= SMOOTH_REVEAL_MIN_DELTA ? "" : content;
  const [displayContent, setDisplayContent] = useState(initialContent);
  const displayContentRef = useRef(initialContent);
  const targetContentRef = useRef(content);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const revealStartedAtRef = useRef<number | null>(null);

  useEffect(() => {
    targetContentRef.current = content;

    const current = displayContentRef.current;
    const prefersReducedMotion =
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const shouldSmoothReveal =
      content !== current &&
      content.startsWith(current) &&
      !prefersReducedMotion &&
      isLoading;

    if (!shouldSmoothReveal) {
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
      revealStartedAtRef.current = null;
      if (current !== content) {
        displayContentRef.current = content;
        setDisplayContent(content);
      }
      return;
    }

    const tick = () => {
      timerRef.current = null;
      const target = targetContentRef.current;
      const latest = displayContentRef.current;
      if (!target.startsWith(latest) || latest.length >= target.length) {
        revealStartedAtRef.current = null;
        return;
      }

      const startedAt = revealStartedAtRef.current ?? performance.now();
      revealStartedAtRef.current = startedAt;
      const remainingDuration = Math.max(
        SMOOTH_REVEAL_CADENCE_MS,
        SMOOTH_REVEAL_DURATION_MS - (performance.now() - startedAt),
      );
      const remainingCommits = Math.max(
        1,
        Math.ceil(remainingDuration / SMOOTH_REVEAL_CADENCE_MS),
      );
      const nextLength = Math.min(
        target.length,
        latest.length +
          Math.max(
            SMOOTH_REVEAL_MIN_CHARS_PER_COMMIT,
            Math.ceil((target.length - latest.length) / remainingCommits),
          ),
      );
      const next = target.slice(0, nextLength);
      displayContentRef.current = next;
      setDisplayContent(next);

      if (next.length < target.length) {
        scheduleTick();
      } else {
        revealStartedAtRef.current = null;
      }
    };

    const scheduleTick = () => {
      if (timerRef.current !== null) return;
      revealStartedAtRef.current ??= performance.now();
      timerRef.current = setTimeout(tick, SMOOTH_REVEAL_CADENCE_MS);
    };

    scheduleTick();
  }, [content, isLoading]);

  useEffect(() => {
    return () => {
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, []);

  return {
    content: displayContent,
    isRevealing: displayContent !== content,
  };
}

function StreamingPre({ children }: ComponentProps<"pre">) {
  const childClassName = isValidElement<{ className?: string }>(children)
    ? children.props.className
    : undefined;
  const language =
    /(?:^|\s)language-([^\s]+)/.exec(childClassName ?? "")?.[1] ?? "";

  return (
    <div
      className="my-4 w-full overflow-hidden rounded-xl border"
      data-language={language}
      data-streaming-code-block="true"
    >
      {language && (
        <div className="bg-muted/80 text-muted-foreground p-3 text-xs">
          <span className="ml-1 font-mono lowercase">{language}</span>
        </div>
      )}
      <pre className="bg-muted/40 overflow-x-auto border-t p-4 font-mono text-xs">
        <StreamingCodeBlockContext.Provider value={true}>
          {children}
        </StreamingCodeBlockContext.Provider>
      </pre>
    </div>
  );
}

function StreamingCode({
  children,
  className,
  node: _node,
  ...props
}: StreamingCodeProps) {
  const isBlock = useContext(StreamingCodeBlockContext);

  if (!isBlock) {
    return (
      <code
        {...props}
        className={cn(
          "bg-muted rounded px-1.5 py-0.5 font-mono text-sm",
          className,
        )}
        data-streaming-inline-code="true"
      >
        {children}
      </code>
    );
  }

  return (
    <code {...props} className={className}>
      {children}
    </code>
  );
}

function isExternalUrl(href: string | undefined): boolean {
  return !!href && /^https?:\/\//.test(href);
}

/** Renders markdown content. */
export function MarkdownContent({
  content,
  isLoading,
  rehypePlugins,
  className,
  remarkPlugins = streamdownPluginsWithoutRawHtml.remarkPlugins,
  components: componentsFromProps,
}: MarkdownContentProps) {
  const deferredContent = useDeferredValue(content);
  const targetContent = isLoading ? deferredContent : content;
  const { content: displayContent, isRevealing } = useSmoothStreamingContent(
    targetContent,
    isLoading,
  );
  const isStreamingRender = isLoading || isRevealing;
  const normalizedContent = useMemo(
    () => preprocessStreamdownMarkdown(displayContent),
    [displayContent],
  );
  const effectiveRehypePlugins = useMemo(() => {
    const base = streamdownPluginsWithoutRawHtml.rehypePlugins ?? [];
    const extra = rehypePlugins ?? [];
    const streaming = isStreamingRender ? [rehypeStreamingListItems] : [];
    return [
      ...base,
      ...extra,
      ...streaming,
    ] as ClipboardSafeStreamdownProps["rehypePlugins"];
  }, [isStreamingRender, rehypePlugins]);
  const components = useMemo(() => {
    // 本地 CitationLink 面：`citation:` 文本链接渲染为引用链接；外链强制
    // _blank + noopener（上游用 markdown-link 组件承担，本地同语义内联）。
    const baseComponents: StreamdownComponentOverrides = {
      a: (props) => {
        if (typeof props.children === "string") {
          const match = /^citation:(.+)$/.exec(props.children);
          if (match) {
            const [, text] = match;
            return <CitationLink {...props}>{text}</CitationLink>;
          }
        }
        const { className: aClassName, target, rel, ...rest } = props;
        const external = isExternalUrl(props.href);
        return (
          <a
            {...rest}
            className={cn(
              "text-primary decoration-primary/30 hover:decoration-primary/60 underline underline-offset-2 transition-colors",
              aClassName,
            )}
            target={target ?? (external ? "_blank" : undefined)}
            rel={rel ?? (external ? "noopener noreferrer" : undefined)}
          />
        );
      },
      ...componentsFromProps,
    };
    if (!isStreamingRender) {
      return baseComponents;
    }
    return {
      ...baseComponents,
      code: componentsFromProps?.code ?? StreamingCode,
      pre: componentsFromProps?.pre ?? StreamingPre,
    };
  }, [componentsFromProps, isStreamingRender]);

  if (!displayContent) return null;

  return (
    <SafeMessageResponse
      className={className}
      remarkPlugins={remarkPlugins}
      rehypePlugins={effectiveRehypePlugins}
      components={toStreamdownComponents(components)}
      parseIncompleteMarkdown={isLoading}
      animated={streamdownSmoothStreamingAnimation}
      isAnimating={isLoading}
    >
      {normalizedContent}
    </SafeMessageResponse>
  );
}
