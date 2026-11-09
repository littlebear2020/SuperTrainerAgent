"use client";

import {
  ArrowRight,
  Check,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  Globe,
  Loader2,
  X,
} from "lucide-react";
import { useCallback, useRef, useState } from "react";

import { streamAgentEdit, streamAssist } from "@/core/writer/api";
import {
  type Citation,
  parseCitations,
  sanitizeCitationUrl,
} from "@/core/writer/citations";
import { DIFF_MAX_CELL_PRODUCT } from "@/core/writer/compute-diff";
import type {
  AgentEditMode,
  AssistRequest,
  DocType,
} from "@/core/writer/types";

import { DiffView } from "./diff-view";

// ── Types ──────────────────────────────────────────────────────────────────

export interface FullDraftModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  markdown: string;
  contentType?: DocType;
  onApply: (newMarkdown: string) => void;
}

// ── Action labels ──────────────────────────────────────────────────────────

export type EditAction = "polish" | "shorten" | "expand";

const ACTION_LABELS: Record<
  EditAction,
  { label: string; mode: AgentEditMode }
> = {
  polish: { label: "润色", mode: "polish" },
  shorten: { label: "精简", mode: "shorten" },
  expand: { label: "扩写", mode: "expand" },
};

// ── Light/heavy diversion helpers (Phase 1) ────────────────────────────────

/**
 * Decide whether the full-draft edit uses the light (direct LLM) or heavy
 * (co-writer agent) channel. Phase 1 rule: any data_source checked → heavy
 * (web = search/knowledge agent). `action` is reserved for future per-action
 * routing (e.g. certain ops might always go heavy).
 */
export function pickFullDraftChannel(
  action: EditAction,
  dataSources: Set<"knowledge" | "web">,
): "light" | "heavy" {
  return dataSources.size > 0 ? "heavy" : "light";
}

/**
 * Build an AssistRequest for the light full-draft path.
 *
 * Op mapping (full-draft action → AssistService):
 * - polish → { op: "polish", ... } (direct op branch)
 * - expand → { op: "expand", ... } (direct op branch)
 * - shorten → { op: "shorten", ... } (direct op branch)
 *
 * All three full-draft actions are op-based (consistent). The light path
 * forwards a custom user `instruction` when provided (P1-8; backend
 * _build_prompt's instruction branch handles it). `full_doc:true` lifts the
 * backend's 4000-char input limit + 2000-token output cap so long docs
 * don't 422.
 */
export function buildAssistRequestForFullDraft(
  action: EditAction,
  markdown: string,
  contentType?: DocType,
  instruction?: string,
): AssistRequest {
  const req: AssistRequest = {
    op: action,
    text: markdown,
    doc_type: contentType,
    // full-draft sends the entire document as `text`; full_doc lifts the
    // backend's 4000-char input limit + 2000-token output cap so long docs
    // don't 422 on the light path (assist_service.py:34-36,113).
    full_doc: true,
  };
  // P1-8: 透传用户自定义 instruction（后端 _build_prompt 的 instruction 分支已支持）。
  if (instruction) {
    req.instruction = instruction;
  }
  return req;
}

// ── W10: preview phase + long-doc size guard ───────────────────────────────

/**
 * Modal phase machine (mirrors the selection-popover's preview→apply pattern).
 *
 * - "idle"      — action/instruction config shown; "开始生成" starts a stream.
 * - "streaming" — stream in flight; live status + Agent trace shown.
 * - "preview"   — stream done; result + diff shown; the wholesale apply is
 *   gated behind an explicit "应用" confirm (NOT auto on stream end).
 */
export type FullDraftPhase = "idle" | "streaming" | "preview";

/**
 * Max LCS cell product (origTokens × revisedTokens) for which a full-doc diff
 * is computed. P1-10: the old guard checked only original length, so a 6000-char
 * doc expanded 2× built a 72M-cell matrix (main-thread freeze + hundreds of MB).
 * CJK ≈ 1 token/char, so char products approximate cell counts.
 *
 * Fix 1.3: raised 4M → 25M. The 4M gate hid the diff for any doc over ~2000
 * chars (a 3000×3000 polish = 9M), so typical full-doc edits showed no diff —
 * users couldn't see what changed. 25M covers docs up to ~5000 chars (a one-off
 * ~0.5-1s DP), still well under the 72M freeze zone.
 */
// Alias kept for existing imports/tests; the canonical constant lives in
// core/writer/compute-diff.ts (P1-10 shared gate).
export const FULL_DRAFT_DIFF_MAX_CELL_PRODUCT = DIFF_MAX_CELL_PRODUCT;

/**
 * Whether the full-draft preview should compute/show a diff (original doc vs
 * streamed result). Pure so the size guard is unit-testable without driving
 * the modal.
 *
 * - origLen × revisedLen ≤ maxProduct → true (diff shown; user reviews changes
 *   before the wholesale setContent apply).
 * - over → false (diff skipped; result-only + a note — avoids the O(n·m)
 *   blowup).
 *
 * Unlike W8's op-based `shouldShowDiff` (which hides the diff for polish), the
 * full-draft actions polish/shorten/expand ALL rewrite the whole doc and so
 * ALL warrant a diff — the gate here is size-based, not op-based.
 */
export function shouldShowFullDraftDiff(
  origLen: number,
  revisedLen: number,
  maxProduct: number = FULL_DRAFT_DIFF_MAX_CELL_PRODUCT,
): boolean {
  return origLen * revisedLen <= maxProduct;
}

/**
 * Strip a whole-output markdown code fence (Fix 1.4). Weak/light-channel models
 * occasionally wrap the entire result in ```markdown ... ``` → TipTap would
 * render it as a code block instead of parsed content. Only unwraps a fence
 * that spans the whole text (anchored ^…$); mid-text inline code is left alone.
 */
export function stripCodeFences(text: string): string {
  const m = /^\s*```[a-zA-Z]*\s*\n([\s\S]*?)\n```\s*$/.exec(text);
  return m?.[1] ?? text;
}

/**
 * Pure seam making the preview-gate testable without @testing-library/react.
 *
 * The wholesale `setContent` apply (handleFullDraftApply → setContentViaEditor
 * in writer-page.tsx) must only fire after the user confirms in the preview
 * state — NOT automatically when the stream ends. This helper returns:
 * - `null` while streaming/idle (no auto-apply), and when the result is empty;
 * - a no-arg confirm thunk in the preview phase that calls `onApply(result)`.
 *
 * Mirrors the selection-popover's `pickApplyCallbacks` seam (W7).
 */
export function pickFullDraftApplyCallback(
  phase: FullDraftPhase,
  result: string,
  onApply: (newMarkdown: string) => void,
): (() => void) | null {
  if (phase !== "preview" || !result) return null;
  return () => onApply(result);
}

// ── Component ──────────────────────────────────────────────────────────────

export function FullDraftModal({
  open,
  onOpenChange,
  markdown,
  contentType,
  onApply,
}: FullDraftModalProps) {
  const [action, setAction] = useState<EditAction>("polish");
  const [instruction, setInstruction] = useState("");
  const [phase, setPhase] = useState<FullDraftPhase>("idle");
  const [result, setResult] = useState("");
  const [citations, setCitations] = useState<Citation[] | null>(null);
  const [dataSources, setDataSources] = useState<Set<"knowledge" | "web">>(
    new Set(),
  );
  const [statusText, setStatusText] = useState("");
  const [traces, setTraces] = useState<
    Array<{ step: string; thinking: string }>
  >([]);
  const [traceExpanded, setTraceExpanded] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  // Stream the full-draft edit (light or heavy channel), then — instead of
  // auto-applying — hold the accumulated result in `result` and switch to the
  // "preview" phase so the user reviews the diff/result before confirming.
  const handleGenerate = useCallback(async () => {
    if (!markdown) return;
    setPhase("streaming");
    setStatusText("正在启动 Agent...");
    setResult("");
    setCitations(null);
    setTraces([]);
    abortRef.current = new AbortController();
    const ac = abortRef.current; // P2-12: capture so we can check aborted after the stream

    try {
      let accumulated = "";

      if (pickFullDraftChannel(action, dataSources) === "light") {
        // Light path: streamAssist (/api/ai/assist, fast, clean, direct LLM).
        // Yields raw string chunks — accumulate into the same buffer as heavy.
        const req = buildAssistRequestForFullDraft(
          action,
          markdown,
          contentType,
          instruction,
        );
        for await (const chunk of streamAssist(req, ac.signal)) {
          accumulated += chunk;
          setStatusText(`正在生成... (${accumulated.length} 字)`);
        }
      } else {
        // Heavy path: streamAgentEdit (/api/runs/stream, co-writer agent).
        // Unchanged from original — web/knowledge data source requires agent.
        // P1-9: pass the abort signal so closing the modal cancels the run.
        const stream = streamAgentEdit({
          instruction: instruction || `请${ACTION_LABELS[action].label}全文`,
          markdown,
          mode: ACTION_LABELS[action].mode,
          contentType,
          dataSources: Array.from(dataSources),
          signal: ac.signal,
        });

        for await (const event of stream) {
          if (event.type === "status") {
            setStatusText(event.content);
          } else if (event.type === "text") {
            accumulated += event.content;
            setStatusText(`正在生成... (${accumulated.length} 字)`);
          } else if (event.type === "trace") {
            setTraces((prev) => [
              ...prev,
              { step: event.step, thinking: event.thinking },
            ]);
          } else if (event.type === "done") {
            accumulated = event.content || accumulated;
            break;
          }
        }
      }

      // P2-12: aborted mid-stream (modal closed / cancelled) → don't write stale
      // results into state the user has already moved away from.
      if (ac.signal.aborted) return;

      if (accumulated) {
        // Fix 1.4: 模型偶尔用 ```markdown 包裹整篇输出 → TipTap 渲染成代码块。
        // 先剥围栏再解析引用标记（围栏可能把标记包进去）。
        accumulated = stripCodeFences(accumulated);
        // W22: parse the trailing [[CITATIONS:<json>]] marker (if any) from
        // the co-writer's response. The heavy/web path may produce this marker
        // when web-search sources were used. The body (marker stripped) is
        // stored as the result; the citations are rendered as a separate list
        // below the result. The light path has no marker → null → no list.
        const { citations: parsedCitations, body } =
          parseCitations(accumulated);
        setResult(body);
        setCitations(parsedCitations);
        setPhase("preview");
      } else {
        setPhase("idle");
        setStatusText("未生成内容，请重试");
      }
    } catch (err) {
      // P2-12: user-initiated cancel (abort) is not an error — bail silently.
      if (err instanceof DOMException && err.name === "AbortError") return;
      console.error("Full draft edit error:", err);
      setStatusText("编辑失败，请重试");
      setPhase("idle");
    } finally {
      abortRef.current = null;
    }
  }, [markdown, action, instruction, dataSources, contentType]);

  // Confirm the preview → fire the (unchanged) wholesale setContent apply.
  // Routed through the pure seam so the gate is testable + consistent.
  const handleConfirm = useCallback(() => {
    const apply = pickFullDraftApplyCallback(phase, result, onApply);
    if (!apply) return;
    apply();
    onOpenChange(false);
    setInstruction("");
    setResult("");
    setCitations(null);
    setPhase("idle");
    setStatusText("");
    setTraces([]);
  }, [phase, result, onApply, onOpenChange]);

  // Cancel / close: abort any in-flight stream, drop the result, reset.
  const handleClose = useCallback(() => {
    abortRef.current?.abort();
    onOpenChange(false);
    setPhase("idle");
    setResult("");
    setCitations(null);
    setStatusText("");
    setTraces([]);
  }, [onOpenChange]);

  if (!open) return null;

  // P1-10: gate on the N×M cell product (orig × revised), not just original
  // length — a long doc expanded 2× would otherwise freeze the main thread.
  const showDiff = shouldShowFullDraftDiff(markdown.length, result.length);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) handleClose();
      }}
    >
      <div className="w-full max-w-2xl rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-[var(--border)] px-4 py-3">
          <h2 className="text-sm font-semibold text-[var(--foreground)]">
            全文 AI 编辑
          </h2>
          <button
            onClick={handleClose}
            className="text-xs text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
          >
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="space-y-3 px-4 py-4">
          {/* Action buttons + data sources + instruction (hidden in preview) */}
          {phase !== "preview" && (
            <>
              {/* Action buttons */}
              <div className="flex gap-1.5">
                {(Object.keys(ACTION_LABELS) as EditAction[]).map((a) => (
                  <button
                    key={a}
                    onClick={() => setAction(a)}
                    disabled={phase === "streaming"}
                    className={`rounded-md px-3 py-1.5 text-xs transition-colors ${
                      action === a
                        ? "bg-[var(--primary)] text-white"
                        : "border border-[var(--border)] text-[var(--foreground)] hover:bg-[var(--muted)]"
                    }`}
                  >
                    {ACTION_LABELS[a].label}
                  </button>
                ))}
              </div>

              {/* Data sources */}
              <div className="flex items-center gap-2">
                <span className="text-[11px] text-[var(--muted-foreground)]">
                  数据源:
                </span>
                <button
                  onClick={() => {
                    setDataSources((prev) => {
                      const next = new Set(prev);
                      if (next.has("web")) next.delete("web");
                      else next.add("web");
                      return next;
                    });
                  }}
                  disabled={phase === "streaming"}
                  className={`inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] transition-colors ${
                    dataSources.has("web")
                      ? "bg-[var(--primary)]/15 text-[var(--primary)]"
                      : "border border-[var(--border)] text-[var(--muted-foreground)] hover:bg-[var(--muted)]"
                  }`}
                >
                  <Globe size={11} />
                  网络搜索
                </button>
              </div>

              {/* Instruction input */}
              <textarea
                value={instruction}
                onChange={(e) => setInstruction(e.target.value)}
                rows={4}
                disabled={phase === "streaming"}
                className="w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm text-[var(--foreground)] outline-none focus:border-[var(--primary)] disabled:opacity-60"
                placeholder="描述你希望如何编辑全文..."
              />
            </>
          )}

          {/* Status + Trace (visible during streaming) */}
          {phase === "streaming" && (
            <div className="space-y-2">
              <div className="flex items-center gap-2 text-xs text-[var(--muted-foreground)]">
                <Loader2 size={12} className="animate-spin" />
                {statusText}
              </div>
              {traces.length > 0 && (
                <div className="rounded-md border border-[var(--border)] bg-[var(--muted)]/30">
                  <button
                    className="flex w-full items-center gap-1 px-2 py-1.5 text-[11px] text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
                    onClick={() => setTraceExpanded(!traceExpanded)}
                  >
                    {traceExpanded ? (
                      <ChevronDown size={12} />
                    ) : (
                      <ChevronRight size={12} />
                    )}
                    Agent 思考过程 ({traces.length})
                  </button>
                  {traceExpanded && (
                    <div className="max-h-32 space-y-1 overflow-y-auto px-2 pb-2">
                      {traces.map((t, i) => (
                        <div
                          key={i}
                          className="text-[10px] text-[var(--muted-foreground)]"
                        >
                          <span className="font-medium text-[var(--foreground)]">
                            {t.step}
                          </span>
                          {t.thinking && `: ${t.thinking.slice(0, 100)}`}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* P1-11/P2-15: idle phase with an error status must also surface it
              (the streaming block only renders while phase === "streaming"). */}
          {phase === "idle" && statusText && (
            <p className="text-xs text-red-500">{statusText}</p>
          )}

          {/* Preview (after streaming) — diff or result-only, gated by size */}
          {phase === "preview" && (
            <div className="space-y-2">
              <div className="rounded-lg border border-[var(--border)] bg-[var(--muted)]/30 p-2">
                {showDiff ? (
                  // Reuse W8's DiffView (computeDiff under the hood): original
                  // = the whole doc `markdown`; revised = the streamed result.
                  <DiffView original={markdown} revised={result} />
                ) : (
                  // Long-doc size guard: skip the O(n·m) diff; show the result
                  // only + a note. Apply/cancel still work.
                  <div>
                    <p className="mb-1 text-[11px] text-[var(--muted-foreground)]">
                      文档较长，已隐藏逐字 diff，请直接预览全文结果
                    </p>
                    <div className="max-h-[420px] overflow-y-auto text-[12px] leading-relaxed whitespace-pre-wrap text-[var(--foreground)]">
                      {result}
                    </div>
                  </div>
                )}
              </div>

              {/* W22: citation list — rendered when the co-writer appended a
                  [[CITATIONS:<json>]] marker (heavy/web path only). Each
                  citation shows title + org/domain; the url is a clickable
                  link. No list when citations is null (light path or no
                  web sources used). */}
              {citations && citations.length > 0 && (
                <div className="rounded-lg border border-[var(--border)] bg-[var(--muted)]/20 p-2">
                  <p className="mb-1.5 text-[11px] font-medium text-[var(--muted-foreground)]">
                    来源（{citations.length}）
                  </p>
                  <ul className="space-y-1">
                    {citations.map((c, i) => (
                      <li
                        key={i}
                        className="flex items-start gap-1.5 text-[11px] leading-relaxed"
                      >
                        <ExternalLink
                          size={10}
                          className="mt-0.5 shrink-0 text-[var(--muted-foreground)]"
                        />
                        <span className="min-w-0 flex-1">
                          {sanitizeCitationUrl(c.url) ? (
                            <a
                              href={sanitizeCitationUrl(c.url)!}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-[var(--primary)] hover:underline"
                            >
                              {c.title}
                            </a>
                          ) : (
                            <span className="text-[var(--muted-foreground)]">
                              {c.title}
                            </span>
                          )}
                          <span className="text-[var(--muted-foreground)]">
                            {" "}
                            — {c.org}
                          </span>
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}

          {phase !== "preview" && (
            <p className="text-[11px] text-[var(--muted-foreground)]">
              AI 将通过 Agent 链路编辑全文，完成后可按 Ctrl+Z 撤销。
            </p>
          )}
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-2 border-t border-[var(--border)] px-4 py-3">
          {phase === "preview" ? (
            <>
              <button
                onClick={handleClose}
                className="inline-flex items-center gap-1.5 rounded-md border border-[var(--border)] px-4 py-1.5 text-xs font-medium text-[var(--foreground)] transition-colors hover:bg-[var(--muted)]"
              >
                <X size={13} />
                取消
              </button>
              <button
                onClick={handleConfirm}
                className="inline-flex items-center gap-1.5 rounded-md bg-[var(--primary)] px-4 py-1.5 text-xs font-medium text-white transition-opacity hover:opacity-90"
              >
                <Check size={13} />
                应用
              </button>
            </>
          ) : (
            <button
              onClick={handleGenerate}
              disabled={phase === "streaming"}
              className="inline-flex items-center gap-1.5 rounded-md bg-[var(--primary)] px-4 py-1.5 text-xs font-medium text-white transition-opacity disabled:opacity-50"
            >
              {phase === "streaming" ? (
                <Loader2 size={13} className="animate-spin" />
              ) : (
                <ArrowRight size={13} />
              )}
              {phase === "streaming" ? "生成中..." : "开始生成"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
