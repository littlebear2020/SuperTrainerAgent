"use client";

import type { Editor } from "@tiptap/react";
import {
  ArrowDownToLine,
  Check,
  Loader2,
  Replace,
  Send,
  X,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { buildSelectionContext, streamAssist } from "@/core/writer/api";
import { shouldShowDiff } from "@/core/writer/compute-diff";
import type { AssistOp, AssistRequest, DocType } from "@/core/writer/types";

import { DiffView } from "./diff-view";

// ── Types ──────────────────────────────────────────────────────────────────

export interface SelectionPopoverProps {
  editor: Editor | null;
  markdown: string;
  docTitle?: string;
  contentType?: DocType;
  onReplaceText: (replacement: string) => void;
  /** Insert text below the current selection (original survives). Used by 概要. */
  onInsertBelow: (text: string) => void;
}

// ── Quick ops (unified — sole op selector) ────────────────────────────────

const QUICK_OPS: { op: AssistOp; label: string }[] = [
  { op: "polish", label: "润色" },
  { op: "rewrite", label: "改写" },
  { op: "expand", label: "扩写" },
  { op: "summarize", label: "概要" },
  { op: "grammar", label: "纠错" },
  { op: "tone", label: "语气" },
  { op: "shorten", label: "精简" },
];

/**
 * Build an AssistRequest for the light selection-edit path.
 *
 * Op mapping (selection quick op → AssistService):
 * - polish → { op: "polish", ... }
 * - rewrite → { op: "rewrite", ... }  (NOT polish — old MODE_OPTIONS
 *   改写→polish contradiction is gone)
 * - expand → { op: "expand", ... }
 * - summarize → { op: "summarize", ... }
 *
 * All four selection ops are op-based (consistent). `full_doc:true` lifts
 * the backend's 4000-char input limit + 2000-token output cap so large
 * selections don't 422 (consistent with W2/W2b). When a custom instruction
 * is present (instruction input, no quick op), it's forwarded via the
 * `instruction` field and `op` defaults to "rewrite".
 */
export function buildSelectionAssistRequest(
  op: AssistOp,
  selectedText: string,
  contentType?: DocType,
  context?: string,
  instruction?: string,
): AssistRequest {
  const req: AssistRequest = {
    op,
    text: selectedText,
    context,
    doc_type: contentType,
    // Selections can be large; full_doc lifts the backend's 4000-char input
    // limit + 2000-token output cap (assist_service.py:34-36,114).
    full_doc: true,
  };
  if (instruction) {
    req.instruction = instruction;
  }
  return req;
}

// ── Apply mode (W7): 概要 inserts below; 润色/改写/扩写 replace ────────────

/** How a selection op's result is applied to the editor. */
export type SelectionApplyMode = "insert-below" | "replace";

/**
 * Map a selection op to its apply mode (spec §4.2).
 *
 * - summarize → "insert-below" (non-destructive: the summary is inserted
 *   below the selection; the original prose survives). This is the default.
 * - polish/rewrite/expand → "replace" (the existing selection-replace
 *   semantics — unchanged by W7).
 */
export function selectionApplyMode(op: AssistOp): SelectionApplyMode {
  return op === "summarize" ? "insert-below" : "replace";
}

/**
 * Pick which apply callback to invoke for a given apply mode.
 *
 * Pure seam so handleApply's mode-pick is unit-testable without
 * @testing-library/react. Returns a no-arg thunk that calls the chosen
 * callback with `streamedText`, or `null` when there is no text to apply.
 *
 * - "insert-below" → onInsertBelow(streamedText)
 * - "replace" → onReplaceText(streamedText)
 */
export function pickApplyCallbacks(
  mode: SelectionApplyMode,
  streamedText: string,
  onInsertBelow: (text: string) => void,
  onReplaceText: (text: string) => void,
): (() => void) | null {
  if (!streamedText) return null;
  if (mode === "insert-below") {
    return () => onInsertBelow(streamedText);
  }
  return () => onReplaceText(streamedText);
}

// ── Follow-up iteration (W9): light channel + appliedOp + hard cap ────────

/** Maximum follow-up iterations per selection round (spec §4.5). */
export const MAX_ITERATIONS = 3;

/**
 * Whether another follow-up iteration is allowed. Pure so the hard cap is
 * unit-testable without driving the popover (W9 §4.5). The guard sits at the
 * top of ``applyFollowUp`` so the cap is unbypassable programmatically, not
 * just UI-hidden.
 */
export function canIterate(
  iterationCount: number,
  max: number = MAX_ITERATIONS,
): boolean {
  return iterationCount < max;
}

/**
 * Build an AssistRequest for a follow-up refine on the light channel (W9).
 *
 * The follow-up refines ``streamedText`` (the current result) per the user's
 * ``instruction``, carrying the round's ``appliedOp`` (NOT hardcoded "polish")
 * so a 改写/扩写/概要 round refines in its own op flavor. On the backend,
 * ``_build_prompt``'s instruction branch honors ``op`` (when ``mode`` is
 * absent) to flavor the refine. ``full_doc:true`` lifts the backend's
 * input/output caps (consistent with W6 ``applyEdit``).
 */
export function buildFollowUpAssistRequest(
  appliedOp: AssistOp,
  instruction: string,
  streamedText: string,
  contentType?: DocType,
  context?: string,
): AssistRequest {
  return {
    op: appliedOp,
    text: streamedText,
    instruction,
    context,
    doc_type: contentType,
    full_doc: true,
  };
}

// ── Component ──────────────────────────────────────────────────────────────

type PopoverState = "hidden" | "input" | "streaming" | "preview";

export function SelectionPopover({
  editor,
  markdown,
  docTitle,
  contentType,
  onReplaceText,
  onInsertBelow,
}: SelectionPopoverProps) {
  const [state, setState] = useState<PopoverState>("hidden");
  const [pos, setPos] = useState({ top: 0, left: 0 });
  const [selectedText, setSelectedText] = useState("");
  const [instruction, setInstruction] = useState("");
  const [streamedText, setStreamedText] = useState("");
  // Which op produced the current streamedText (drives apply mode in W7).
  const [appliedOp, setAppliedOp] = useState<AssistOp | null>(null);
  const [statusMsg, setStatusMsg] = useState("");
  const [iterationCount, setIterationCount] = useState(0);
  const [followUpInstruction, setFollowUpInstruction] = useState("");
  // B3-T2: tone submenu (U2) -- inline expansion for tone style selection
  const [toneMenuOpen, setToneMenuOpen] = useState(false);

  // MAX_ITERATIONS is a module-level export (W9) — used by canIterate + the
  // UI hide/note below. The hard guard lives at the top of applyFollowUp.

  const popoverRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const isDraggingRef = useRef(false);
  // Saved selection range so we can restore it when popover interactions blur the editor
  const savedSelectionRef = useRef<{ from: number; to: number } | null>(null);

  // Detect TipTap selection
  const checkSelection = useCallback(() => {
    if (!editor) return;

    const { from, to } = editor.state.selection;
    const hasSelection = from !== to;

    if (hasSelection) {
      if (state === "preview") return;

      const text = editor.state.doc.textBetween(from, to, "\n");
      if (!text.trim()) return;

      setSelectedText(text);
      savedSelectionRef.current = { from, to };

      try {
        const coords = editor.view.coordsAtPos(to);
        setPos({
          top: coords.bottom + 8,
          left: Math.min(coords.left, window.innerWidth - 380),
        });
      } catch {
        setPos({
          top: window.innerHeight / 2,
          left: Math.max(20, window.innerWidth / 2 - 180),
        });
      }

      setState("input");
      setInstruction("");
      setStreamedText("");
      setStatusMsg("");
    } else if (state === "input") {
      setState("hidden");
    }
  }, [editor, state]);

  // Listen for selection changes via TipTap transaction
  useEffect(() => {
    if (!editor) return;

    const handler = () => {
      setTimeout(checkSelection, 0);
    };

    editor.on("selectionUpdate", handler);
    return () => {
      editor.off("selectionUpdate", handler);
    };
  }, [editor, checkSelection]);

  // Close on click outside
  useEffect(() => {
    if (state === "hidden") return;
    const handleClick = (e: MouseEvent) => {
      if (
        popoverRef.current &&
        !popoverRef.current.contains(e.target as Node)
      ) {
        setState("hidden");
      }
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [state]);

  // Apply AI edit via light AssistService channel (W6 — was heavy streamAgentEdit)
  const applyEdit = useCallback(
    async (customOp?: AssistOp, toneStyle?: string) => {
      if (!selectedText) return;
      const inst = instruction;
      if (!inst && !customOp) return;

      setState("streaming");
      setStatusMsg("正在分析文本...");
      setStreamedText("");
      setIterationCount(0);
      setFollowUpInstruction("");

      const abort = new AbortController();
      abortRef.current = abort;

      // Build context + light-path request.
      // B3-T2: Quick op -> op-based (polish/rewrite/expand/summarize/grammar/tone/shorten).
      // Custom instruction -> op defaults to "rewrite", instruction forwarded.
      const ctx = buildSelectionContext(docTitle, markdown, selectedText);
      const op: AssistOp = customOp ?? "rewrite";
      setAppliedOp(op);
      const req = buildSelectionAssistRequest(
        op,
        selectedText,
        contentType,
        ctx,
        inst || undefined,
      );
      // B3-T2: pass tone style for the tone submenu (U2)
      if (toneStyle) {
        req.tone = toneStyle;
      }

      try {
        // Light path: streamAssist yields raw string chunks (not typed events).
        // Accumulate into the same streamedText buffer → preview state.
        let accumulated = "";
        for await (const chunk of streamAssist(req, abort.signal)) {
          if (abort.signal.aborted) return;
          accumulated += chunk;
          setStreamedText(accumulated);
          setStatusMsg(`正在生成... (${accumulated.length} 字)`);
        }

        if (accumulated) {
          setStreamedText(accumulated);
        }
        setState("preview");
        setStatusMsg("");
      } catch (err) {
        console.error("Selection edit error:", err);
        // B3-T5: surface the backend error detail (e.g. 429 "请求过于频繁")
        const msg =
          err instanceof Error && err.message
            ? err.message
            : "编辑失败，请重试";
        setStatusMsg(msg);
        setState("input");
      }
    },
    [selectedText, instruction, markdown, docTitle, contentType],
  );

  // Apply the streamed result. Mode-aware (W7): 概要 -> insert-below
  // (original survives), 润色/改写/扩写 -> replace. Shared core restores
  // the saved selection, picks the callback via the pure seam, then resets.
  //
  // B1-T6: before applying, validate the saved selection range still
  // matches the original selectedText. If the document changed (e.g. user
  // typed in another window while the popover was streaming), the saved
  // range now points to different content -> replacing would corrupt the
  // document. Show a toast and bail to idle.
  const runApply = useCallback(
    (mode: SelectionApplyMode) => {
      if (!streamedText) return;
      const e = editor;
      if (e && savedSelectionRef.current) {
        const { from, to } = savedSelectionRef.current;
        // B1-T6: validate the selection range still contains the original text
        const currentText = e.state.doc.textBetween(from, to, "\n");
        if (currentText !== selectedText) {
          toast.error("选区内容已变化，请重新选择");
          setState("input");
          setStreamedText("");
          savedSelectionRef.current = null;
          setAppliedOp(null);
          return;
        }
        e.chain().focus().setTextSelection({ from, to }).run();
      }
      const apply = pickApplyCallbacks(
        mode,
        streamedText,
        onInsertBelow,
        onReplaceText,
      );
      apply?.();
      setState("hidden");
      setStreamedText("");
      savedSelectionRef.current = null;
      setAppliedOp(null);
    },
    [streamedText, onInsertBelow, onReplaceText, editor, selectedText],
  );

  // Default apply — mode derived from the op that ran.
  const handleApply = useCallback(
    () => runApply(selectionApplyMode(appliedOp ?? "rewrite")),
    [runApply, appliedOp],
  );

  // "替换原文" — forces replace even for 概要 (whose default is insert-below).
  const handleReplaceOriginal = useCallback(
    () => runApply("replace"),
    [runApply],
  );

  // Iterative follow-up edit (W9: migrated to light streamAssist)
  const applyFollowUp = useCallback(
    async (instruction: string) => {
      if (!streamedText || !instruction) return;
      // W9 §4.5: hard guard — no further iterations past the cap (unbypassable,
      // not just UI-hidden). The follow-up input is already hidden at the
      // limit; this makes the cap enforceable programmatically too.
      if (!canIterate(iterationCount, MAX_ITERATIONS)) return;

      setState("streaming");
      setStatusMsg("正在优化...");
      setFollowUpInstruction("");

      const abort = new AbortController();
      abortRef.current = abort;

      try {
        const ctx = buildSelectionContext(docTitle, markdown, selectedText);

        // W9: light channel — carry the round's op (appliedOp), NOT a
        // hardcoded "polish" (§4.4 bug). streamAssist yields raw string
        // chunks (like applyEdit); accumulate into streamedText in place →
        // stay in preview. The diff (W8) re-renders against selectedText.
        // P0-2: instruction 只传用户的优化要求。streamedText 已作为 text 传入，
        // 后端 _build_prompt 的 instruction 分支会把它拼进「待编辑文本」。
        // 旧实现把全文嵌进 instruction → >952 字必 422 + 文本重复两遍。
        const req = buildFollowUpAssistRequest(
          appliedOp ?? "rewrite",
          instruction,
          streamedText,
          contentType,
          ctx,
        );

        let accumulated = "";
        for await (const chunk of streamAssist(req, abort.signal)) {
          if (abort.signal.aborted) return;
          accumulated += chunk;
          setStreamedText(accumulated);
          setStatusMsg(`正在优化... (${accumulated.length} 字)`);
        }

        if (accumulated) {
          setStreamedText(accumulated);
        }
        setIterationCount((prev) => prev + 1);
        setState("preview");
        setStatusMsg("");
      } catch (err) {
        console.error("Follow-up edit error:", err);
        setStatusMsg("优化失败，请重试");
        setState("preview");
      }
    },
    [
      streamedText,
      iterationCount,
      docTitle,
      markdown,
      selectedText,
      contentType,
      appliedOp,
    ],
  );

  // Cancel
  const handleCancel = useCallback(() => {
    abortRef.current?.abort();
    setState("hidden");
    setStreamedText("");
    setAppliedOp(null);
  }, []);

  if (state === "hidden") return null;

  const handleDragStart = (e: React.PointerEvent) => {
    e.preventDefault();
    isDraggingRef.current = true;
    const startX = e.clientX;
    const startY = e.clientY;
    const startLeft = pos.left;
    const startTop = pos.top;

    const onMove = (ev: PointerEvent) => {
      if (!isDraggingRef.current) return;
      const dx = ev.clientX - startX;
      const dy = ev.clientY - startY;
      const newLeft = Math.max(
        0,
        Math.min(window.innerWidth - 380, startLeft + dx),
      );
      const newTop = Math.max(
        0,
        Math.min(window.innerHeight - 100, startTop + dy),
      );
      setPos({ top: newTop, left: newLeft });
    };
    const onUp = () => {
      isDraggingRef.current = false;
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  return (
    <div
      ref={popoverRef}
      className="fixed z-50 w-[360px] rounded-2xl border border-[var(--border)] bg-[var(--card)] p-2.5 shadow-2xl"
      style={{ top: pos.top, left: pos.left }}
      onMouseDown={(e) => {
        // Prevent the editor from losing selection when clicking inside popover,
        // but allow interactive elements (input, button, textarea) to receive focus
        const tag = (e.target as HTMLElement).tagName;
        if (tag !== "INPUT" && tag !== "BUTTON" && tag !== "TEXTAREA") {
          e.preventDefault();
        }
      }}
    >
      {/* Drag handle */}
      <div
        className="mb-2 flex cursor-grab justify-center active:cursor-grabbing"
        onPointerDown={handleDragStart}
        aria-hidden="true"
      >
        <div className="h-1 w-10 rounded-full bg-[var(--border)]/80" />
      </div>

      {/* Selection preview badge */}
      {selectedText && (
        <div className="mb-2 flex items-center gap-2 rounded-lg bg-[var(--muted)]/40 px-2.5 py-1.5">
          <div className="min-w-0 flex-1 truncate text-[11px] text-[var(--muted-foreground)]">
            已选中：
            <span className="text-[var(--foreground)]">
              {selectedText.slice(0, 60)}
              {selectedText.length > 60 ? "..." : ""}
            </span>
          </div>
          <span className="shrink-0 text-[10px] text-[var(--muted-foreground)]">
            {selectedText.length} 字
          </span>
        </div>
      )}

      {/* Instruction input */}
      {state !== "preview" && (
        <div className="relative">
          <input
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            onMouseDown={(e) => {
              e.preventDefault();
              e.currentTarget.focus();
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void applyEdit();
              }
            }}
            className="h-10 w-full rounded-xl bg-transparent pr-10 pl-3 text-[13px] text-[var(--foreground)] outline-none placeholder:text-[var(--muted-foreground)]"
            placeholder="告诉 AI 如何处理选中文本..."
            disabled={state === "streaming"}
          />
          <button
            onClick={() => void applyEdit()}
            disabled={state === "streaming" || !instruction}
            className="absolute top-1.5 right-1.5 inline-flex h-7 w-7 items-center justify-center rounded-full bg-[var(--primary)] text-white transition-opacity disabled:opacity-45"
          >
            {state === "streaming" ? (
              <Loader2 size={13} className="animate-spin" />
            ) : (
              <Send size={13} />
            )}
          </button>
        </div>
      )}

      {/* Quick ops */}
      {state === "input" && (
        <div className="mt-2 flex flex-wrap items-center gap-1">
          {QUICK_OPS.map((q) =>
            q.op === "tone" ? (
              <div key={q.op} className="relative">
                <button
                  onClick={() => setToneMenuOpen((v) => !v)}
                  className="flex-1 rounded-lg border border-[var(--border)] px-1.5 py-1.5 text-[11px] text-[var(--foreground)] transition-colors hover:bg-[var(--muted)]"
                >
                  {q.label}
                </button>
                {toneMenuOpen && (
                  <div className="absolute top-full left-0 z-10 mt-1 flex flex-col gap-0.5 rounded-lg border border-[var(--border)] bg-[var(--card)] p-1 shadow-lg">
                    {["正式", "活泼", "专业", "亲切"].map((style) => (
                      <button
                        key={style}
                        onClick={() => {
                          setToneMenuOpen(false);
                          void applyEdit("tone", style);
                        }}
                        className="rounded px-2 py-1 text-[11px] whitespace-nowrap text-[var(--foreground)] transition-colors hover:bg-[var(--muted)]"
                      >
                        {style}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            ) : (
              <button
                key={q.op}
                onClick={() => void applyEdit(q.op)}
                className="flex-1 rounded-lg border border-[var(--border)] px-1.5 py-1.5 text-[11px] text-[var(--foreground)] transition-colors hover:bg-[var(--muted)]"
              >
                {q.label}
              </button>
            ),
          )}
        </div>
      )}

      {/* Streaming status / preview */}
      {(state === "streaming" || state === "preview") && (
        <div className="mt-2 rounded-xl border border-[var(--border)]/70 bg-[var(--muted)]/18 px-3 py-2">
          {statusMsg && (
            <div className="mb-1 flex items-center gap-1.5 text-[12px] text-[var(--muted-foreground)]">
              {state === "streaming" && (
                <Loader2 size={11} className="animate-spin" />
              )}
              {statusMsg}
            </div>
          )}

          {streamedText &&
          state === "preview" &&
          appliedOp &&
          shouldShowDiff(appliedOp) ? (
            // 改写/扩写: show a diff (original selection vs streamed result)
            // so the user reviews changes before applying (spec §4.3).
            <DiffView original={selectedText} revised={streamedText} />
          ) : (
            <div className="max-h-[200px] overflow-y-auto text-[12px] leading-relaxed text-[var(--foreground)]">
              {streamedText}
            </div>
          )}
          {state === "preview" && (
            <>
              {/* Iteration info */}
              {iterationCount > 0 && (
                <div className="mt-1 text-[10px] text-[var(--muted-foreground)]">
                  已迭代 {iterationCount} 次
                  {iterationCount >= MAX_ITERATIONS
                    ? "（已达上限）"
                    : `（最多 ${MAX_ITERATIONS} 次）`}
                </div>
              )}
              {/* Follow-up iteration input */}
              {iterationCount < MAX_ITERATIONS && (
                <div className="mt-2 flex items-center gap-2">
                  <input
                    value={followUpInstruction}
                    onChange={(e) => setFollowUpInstruction(e.target.value)}
                    onMouseDown={(e) => {
                      e.preventDefault();
                      e.currentTarget.focus();
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && followUpInstruction.trim()) {
                        e.preventDefault();
                        void applyFollowUp(followUpInstruction.trim());
                      }
                    }}
                    className="h-8 flex-1 rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 text-[12px] text-[var(--foreground)] outline-none placeholder:text-[var(--muted-foreground)]"
                    placeholder="追加指令，继续优化（如：再简洁一点）..."
                  />
                  <button
                    onClick={() =>
                      void applyFollowUp(followUpInstruction.trim())
                    }
                    disabled={!followUpInstruction.trim()}
                    className="inline-flex h-8 shrink-0 items-center rounded-lg bg-[var(--primary)] px-3 text-[11px] text-white transition-opacity disabled:opacity-45"
                  >
                    优化
                  </button>
                </div>
              )}
              <div className="mt-2 flex justify-end gap-2">
                <button
                  onClick={handleCancel}
                  className="inline-flex items-center gap-1 rounded-md px-3 py-1.5 text-[12px] text-[var(--foreground)] transition-colors hover:bg-[var(--muted)]"
                >
                  <X size={12} />
                  取消
                </button>
                {selectionApplyMode(appliedOp ?? "rewrite") ===
                "insert-below" ? (
                  <>
                    {/* 概要: secondary 替换原文 (forces replace) */}
                    <button
                      onClick={handleReplaceOriginal}
                      className="inline-flex items-center gap-1 rounded-md border border-[var(--border)] px-3 py-1.5 text-[12px] text-[var(--foreground)] transition-colors hover:bg-[var(--muted)]"
                    >
                      <Replace size={12} />
                      替换原文
                    </button>
                    {/* 概要: primary 插入到下方 (default — non-destructive) */}
                    <button
                      onClick={handleApply}
                      className="inline-flex items-center gap-1 rounded-md bg-[var(--primary)] px-3 py-1.5 text-[12px] text-white transition-opacity hover:opacity-90"
                    >
                      <ArrowDownToLine size={12} />
                      插入到下方
                    </button>
                  </>
                ) : (
                  /* 润色/改写/扩写: replace (unchanged) */
                  <button
                    onClick={handleApply}
                    className="inline-flex items-center gap-1 rounded-md bg-[var(--primary)] px-3 py-1.5 text-[12px] text-white transition-opacity hover:opacity-90"
                  >
                    <Check size={12} />
                    应用
                  </button>
                )}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
