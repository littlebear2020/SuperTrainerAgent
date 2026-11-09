"use client";

import {
  AlertTriangle,
  FileText,
  Loader2,
  RefreshCw,
  Send,
  Sparkles,
  Zap,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import {
  isFullDocHeuristic,
  parseStageSignal,
} from "@/core/writer/from-scratch-stage";
import type { DocType } from "@/core/writer/types";
import { useFromScratch } from "@/core/writer/use-from-scratch";
import type { ScratchMessage } from "@/core/writer/use-from-scratch";
import { cn } from "@/lib/utils";
// ── Types ────────────────────────────────────────────────────────────────

interface FromScratchPanelProps {
  docType: DocType;
  docTitle?: string;
  onFillEditor: (markdown: string) => void;
  open: boolean;
  onClose: () => void;
}

/**
 * P2-15: resolve the reply payload for a clarify turn. Prefers the clicked
 * option's text; falls back to the input box. Returns null when neither has
 * non-whitespace content (so handleReply can no-op). Extracted so the
 * clarify-option buttons (which pass their option text as the arg) actually
 * work even when the input box is empty — the old handleReply ignored its
 * argument and read replyInput only.
 */
export function resolveReplyPayload(
  text?: string,
  replyInput = "",
): string | null {
  const payload = (text ?? replyInput).trim();
  return payload || null;
}

// ── Component ────────────────────────────────────────────────────────────

export function FromScratchPanel({
  docType,
  docTitle,
  onFillEditor,
  open,
  onClose,
}: FromScratchPanelProps) {
  const [topicInput, setTopicInput] = useState("");
  const [replyInput, setReplyInput] = useState("");
  const [fullDraft, setFullDraft] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const {
    messages,
    isStreaming,
    isSearching,
    startSession,
    reply,
    skipClarification,
    reset,
  } = useFromScratch({
    docType,
    docTitle,
    onFullDraftReady: (markdown) => setFullDraft(markdown),
  });

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, isStreaming]);

  // Reset draft when panel reopens after reset
  useEffect(() => {
    if (open && messages.length === 0) {
      setFullDraft(null);
    }
  }, [open, messages.length]);

  const handleStart = () => {
    if (!topicInput.trim()) return;
    setFullDraft(null);
    void startSession(topicInput.trim());
  };

  // P2-15: accept the clicked option's text (clarify buttons pass it as the
  // arg); fall back to the input box. The old impl ignored the arg and read
  // replyInput, so clicking an option with an empty input did nothing.
  const handleReply = (text?: string) => {
    const payload = resolveReplyPayload(text, replyInput);
    if (!payload) return;
    void reply(payload);
    setReplyInput("");
  };

  const handleReset = () => {
    reset();
    setTopicInput("");
    setReplyInput("");
    setFullDraft(null);
  };

  const handleFillEditor = () => {
    if (fullDraft) {
      onFillEditor(fullDraft);
    }
  };

  if (!open) return null;

  const isConversing = messages.length > 0;

  return (
    <aside className="absolute top-0 right-0 z-30 flex h-full w-96 flex-col border-l border-[var(--border)] bg-[var(--background)] shadow-xl">
      {/* Header */}
      <div className="flex shrink-0 items-center justify-between border-b border-[var(--border)] px-3 py-2">
        <div className="flex items-center gap-1.5 text-sm font-medium">
          <Sparkles size={14} className="text-[var(--primary)]" />
          从零创作
        </div>
        <div className="flex items-center gap-1">
          {isConversing && (
            <button
              type="button"
              onClick={handleReset}
              title="重新开始"
              className="rounded p-1 text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)] hover:text-[var(--foreground)]"
            >
              <RefreshCw size={13} />
            </button>
          )}
          <button
            type="button"
            onClick={onClose}
            title="关闭"
            className="rounded p-1 text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)] hover:text-[var(--foreground)]"
          >
            <X size={14} />
          </button>
        </div>
      </div>

      {/* Topic input (first screen) */}
      {!isConversing && (
        <div className="flex min-h-0 flex-1 flex-col gap-3 p-4">
          <div className="text-xs text-[var(--muted-foreground)]">
            输入你要创作的主题，AI 将引导你完成大纲和全文。
          </div>
          <textarea
            value={topicInput}
            onChange={(e) => setTopicInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleStart();
            }}
            placeholder="例如：微服务架构最佳实践..."
            rows={4}
            autoFocus
            className="w-full resize-none rounded-md border border-[var(--border)] bg-[var(--secondary)]/50 px-3 py-2 text-sm text-[var(--foreground)] focus:outline-none"
          />
          <button
            type="button"
            onClick={handleStart}
            disabled={!topicInput.trim() || isStreaming}
            className={cn(
              "flex items-center justify-center gap-1.5 rounded-md px-3 py-2 text-sm font-medium transition-colors",
              topicInput.trim() && !isStreaming
                ? "bg-[var(--primary)] text-[var(--primary-foreground)] hover:opacity-90"
                : "cursor-not-allowed bg-[var(--muted)] text-[var(--muted-foreground)]",
            )}
          >
            {isStreaming ? (
              <>
                <Loader2 size={14} className="animate-spin" />
                创作中...
              </>
            ) : (
              <>
                <Sparkles size={14} />
                开始创作
              </>
            )}
          </button>
        </div>
      )}

      {/* Conversation view */}
      {isConversing && (
        <>
          {/* Messages */}
          <div
            ref={scrollRef}
            className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3"
          >
            {messages.map((msg) => (
              <MessageBubble
                key={msg.id}
                message={msg}
                onOptionClick={handleReply}
                onContinue={!isStreaming ? () => void reply("继续") : undefined}
                onSkip={
                  !isStreaming ? () => void skipClarification() : undefined
                }
              />
            ))}

            {/* Streaming indicator */}
            {isStreaming && (
              <div className="flex items-center gap-1.5 px-1 text-xs text-[var(--muted-foreground)]">
                <Loader2 size={11} className="animate-spin" />
                {isSearching ? "🌐 正在联网搜索…" : "AI 正在思考..."}
              </div>
            )}

            {/* Fill editor button */}
            {fullDraft && (
              <button
                type="button"
                onClick={handleFillEditor}
                className="flex w-full items-center justify-center gap-1.5 rounded-md border border-[var(--primary)]/30 bg-[var(--primary)]/10 px-3 py-2 text-sm font-medium text-[var(--primary)] transition-colors hover:bg-[var(--primary)]/15"
              >
                <FileText size={14} />
                填入编辑器
              </button>
            )}
          </div>

          {/* Input bar */}
          <div className="shrink-0 border-t border-[var(--border)] p-2">
            <div className="flex gap-1">
              <input
                type="text"
                value={replyInput}
                onChange={(e) => setReplyInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleReply()}
                placeholder="输入回复..."
                disabled={isStreaming}
                className="flex-1 rounded-md border border-[var(--border)] bg-[var(--secondary)]/50 px-2.5 py-1.5 text-xs text-[var(--foreground)] focus:outline-none disabled:opacity-50"
              />
              <button
                type="button"
                onClick={() => handleReply()}
                disabled={isStreaming || !replyInput.trim()}
                className={cn(
                  "flex items-center justify-center rounded-md px-2.5 py-1.5 text-xs transition-colors",
                  replyInput.trim() && !isStreaming
                    ? "bg-[var(--primary)] text-[var(--primary-foreground)] hover:opacity-90"
                    : "cursor-not-allowed bg-[var(--muted)] text-[var(--muted-foreground)]",
                )}
              >
                <Send size={12} />
              </button>
            </div>
          </div>
        </>
      )}
    </aside>
  );
}

// ── Message Bubble ────────────────────────────────────────────────────────

function MessageBubble({
  message,
  onOptionClick,
  onContinue,
  onSkip,
}: {
  message: ScratchMessage;
  onOptionClick: (text: string) => void;
  /** Continue-generation affordance for truncated drafts (§5.1.2). When provided
   * (stream idle), a "继续生成" button is rendered inside the truncation
   * indicator; when undefined (streaming), the button is omitted. */
  onContinue?: () => void;
  /** Skip-clarification affordance ("直接生成", §5.2C). When provided (stream
   * idle), a "直接生成" button is rendered on ask_clarification messages so the
   * user can always skip the question and have the model generate directly.
   * Calls reply(SKIP_CLARIFICATION_INSTRUCTION) directly (independent of the
   * input-box reply path). */
  onSkip?: () => void;
}) {
  const isUser = message.role === "user";

  // Structured stage signal (shared with buildResult): strip the `[[STAGE:...]]`
  // marker from the displayed text so the user never sees it (defense-in-depth —
  // buildResult already strips it before setting content).
  //
  // Stage DETECTION now reads the threaded `message.isOutline` / `isFullDoc`
  // flags from buildResult directly. Re-parsing `message.content` for the stage
  // was dead code: buildResult sets `content = result.text` which is ALREADY
  // marker-stripped, so `parseStageSignal(message.content).stage` was always
  // null — the 📋 outline hint never fired, and the 📄 draft hint fell back to
  // the unreliable `isFullDocHeuristic` for signal-driven drafts. The fallback
  // heuristic is kept ONLY for messages that lack a threaded flag (e.g. error
  // messages), preserving the old defense-in-depth for those edge cases.
  const { text: displayText } = parseStageSignal(message.content);
  const isOutline =
    !isUser && !message.isClarification && message.isOutline === true;
  const isFullDoc =
    !isUser &&
    !message.isClarification &&
    (message.isFullDoc === true ||
      (message.isFullDoc === undefined && isFullDocHeuristic(displayText)));
  // Truncation (§5.1.2): stream ended without a stage marker and content is
  // substantial → likely token-truncated mid-generation. Show a ⚠️ indicator
  // (replaces the 📄 hint so the two never contradict each other).
  const isTruncated =
    !isUser && !message.isClarification && message.isTruncated === true;

  if (isUser) {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-lg rounded-br-sm bg-[var(--primary)] px-3 py-1.5 text-sm text-[var(--primary-foreground)]">
          {message.content}
        </div>
      </div>
    );
  }

  return (
    <div className="flex justify-start">
      <div
        className={cn(
          "max-w-[85%] rounded-lg rounded-bl-sm px-3 py-1.5 text-sm",
          message.isClarification
            ? "border border-[var(--primary)]/30 bg-[var(--primary)]/5"
            : "bg-[var(--secondary)]",
        )}
      >
        {/* Clarification icon */}
        {message.isClarification && (
          <div className="mb-1 flex items-center gap-1 text-xs font-medium text-[var(--primary)]">
            <Sparkles size={11} />
            AI 提问
          </div>
        )}

        {/* Content — render as text (not dangerouslySetInnerHTML); marker stripped */}
        <div className="break-words whitespace-pre-wrap text-[var(--foreground)]">
          {displayText}
        </div>

        {/* Clickable options for clarification */}
        {message.isClarification &&
          message.options &&
          message.options.length > 0 && (
            <div className="mt-2 flex flex-col gap-1">
              {message.options.map((opt, i) => (
                <button
                  key={i}
                  type="button"
                  onClick={() => onOptionClick(opt)}
                  className="rounded-md border border-[var(--border)] px-2.5 py-1 text-left text-xs text-[var(--muted-foreground)] transition-colors hover:border-[var(--primary)]/30 hover:text-[var(--foreground)]"
                >
                  {opt}
                </button>
              ))}
            </div>
          )}

        {/* "直接生成" skip button on clarifications (§5.2C + §5.1.3).
            Always available when the stream is idle — lets the user skip the
            question and have the model generate directly. Calls
            reply(SKIP_CLARIFICATION_INSTRUCTION) directly, independent of the
            input-box reply path. */}
        {message.isClarification && onSkip && (
          <div className="mt-2">
            <button
              type="button"
              onClick={onSkip}
              className="flex items-center gap-1 rounded-md border border-[var(--primary)]/30 bg-[var(--primary)]/5 px-2.5 py-1 text-xs font-medium text-[var(--primary)] transition-colors hover:bg-[var(--primary)]/10"
            >
              <Zap size={11} />
              直接生成
            </button>
          </div>
        )}

        {/* Outline hint */}
        {isOutline && (
          <div className="mt-1 text-xs text-[var(--muted-foreground)]">
            {"📋 这是大纲，确认后 AI 将生成全文"}
          </div>
        )}

        {/* Truncation indicator (§5.1.2): stream ended without a stage marker and
            content is substantial → likely token-truncated mid-generation. NOT
            silent: shows a perceptible ⚠️ warning + a "继续生成" affordance.
            Replaces the 📄 hint so the two never contradict each other (a
            truncated draft is not a "complete" draft, even if the legacy
            heuristic classified it as isFullDoc). */}
        {isTruncated && (
          <div className="mt-2 rounded-md border border-[var(--destructive)]/30 bg-[var(--destructive)]/5 px-2.5 py-1.5 text-xs">
            <div className="flex items-center gap-1 font-medium text-[var(--destructive)]">
              <AlertTriangle size={12} className="shrink-0" />
              生成似乎中断（可能因长度限制被截断）
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-1.5">
              {onContinue && (
                <button
                  type="button"
                  onClick={onContinue}
                  className="rounded border border-[var(--border)] px-2 py-0.5 text-[var(--muted-foreground)] transition-colors hover:border-[var(--primary)]/30 hover:text-[var(--foreground)]"
                >
                  继续生成
                </button>
              )}
              <span className="text-[var(--muted-foreground)]">或重新开始</span>
            </div>
          </div>
        )}

        {/* Markdown hint for full docs (suppressed when truncated — the ⚠️
            indicator above replaces it so the user isn't told the doc is
            "complete" while also being warned it's truncated). */}
        {isFullDoc && !isTruncated && (
          <div className="mt-1 text-xs text-[var(--muted-foreground)]">
            {"📄 已生成完整文档，点击下方「填入编辑器」"}
          </div>
        )}
      </div>
    </div>
  );
}
