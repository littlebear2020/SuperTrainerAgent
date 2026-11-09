/**
 * Hook for "Create from Scratch" conversation flow.
 *
 * Uses a persistent thread with co-writer agent. The agent uses
 * ask_clarification to guide the user through topic → outline → full draft.
 *
 * Thread lifecycle:
 * 1. On open: create a new thread (or reuse if already created for this doc)
 * 2. User sends topic → agent responds with ask_clarification (interrupt)
 * 3. User answers → resume → agent asks more or generates outline
 * 4. User confirms outline → resume → agent generates full Markdown
 * 5. User clicks "Fill Editor" → content injected into TipTap
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { getAPIClient } from "@/core/api";
import { fetch } from "@/core/api/fetcher"; // CSRF + credentials auto-injected; do not use globalThis.fetch for writes
import { getBackendBaseURL } from "@/core/config";
import { detectSearchToolCall } from "@/core/writer/api";
import { parseCitations } from "@/core/writer/citations";
import {
  isFullDocHeuristic,
  parseStageSignal,
  type ScratchStage,
} from "@/core/writer/from-scratch-stage";
import { createHeartbeat } from "@/core/writer/heartbeat";

import {
  extractMessageText,
  isNonAiMessage,
  parseSseEvent,
  sseFrames,
} from "./sse";

// ── Types ────────────────────────────────────────────────────────────────

export interface ScratchMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  /** True when this is an ask_clarification interrupt (waiting for user input). */
  isClarification?: boolean;
  /** Optional choices for the user (from ask_clarification options). */
  options?: string[];
  /** True when buildResult detected a `[[STAGE:OUTLINE]]` marker (an outline to
   * confirm, not a full draft). Threaded from buildResult so the panel reads it
   * directly instead of re-parsing marker-stripped content (which always yields
   * stage:null — the panel's parseStageSignal stage-detection was dead code). */
  isOutline?: boolean;
  /** True when buildResult classified this as a full draft (signal or §11
   * fallback). Threaded from buildResult; gates the 📄 hint reliably for
   * signal-driven drafts that the legacy heuristic would miss. */
  isFullDoc?: boolean;
  /** True when buildResult flagged this as a likely-truncated draft (stream
   * ended WITHOUT a `[[STAGE:...]]` marker AND content is substantial — §5.1.2).
   * The panel shows a ⚠️ truncation indicator + retry/continue affordance
   * instead of silently treating the partial content as a normal turn. */
  isTruncated?: boolean;
}

/** Result from parsing a co-writer SSE stream. */
export interface ScratchSSEResult {
  isClarification: boolean;
  text: string;
  options?: string[];
  isFullDoc: boolean;
  /** True when the assistant emitted a `[[STAGE:OUTLINE]]` marker (an outline
   * to confirm, not a full draft to fill). Set only via the stage signal. */
  isOutline?: boolean;
  /** True when the stream ended WITHOUT a stage marker AND the accumulated text
   * is substantial → likely token-truncated mid-generation (§5.1.2). The panel
   * shows a ⚠️ indicator + retry/continue affordance (not a silent stall). */
  isTruncated?: boolean;
}

interface UseFromScratchOptions {
  docType: string;
  docTitle?: string;
  onFullDraftReady: (markdown: string) => void;
}

/**
 * POST a co-writer run to the thread's stream endpoint.
 *
 * Extracted from `sendMessage` so the CSRF/credentials contract is unit-testable
 * without a DOM. Resolves `fetch` to the fetcher wrapper (`import { fetch } from
 * "@/core/api/fetcher"`) so `X-CSRF-Token` is minted from the `csrf_token`
 * cookie — a bare `globalThis.fetch` here was the root cause of the 403
 * "CSRF token missing" on the 从零创作 streaming POST.
 */
export async function postThreadRunStream(
  tid: string,
  payload: unknown,
  signal?: AbortSignal,
): Promise<Response> {
  return fetch(`${getBackendBaseURL()}/api/threads/${tid}/runs/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    signal,
    body: JSON.stringify(payload),
  });
}

// ── Hook ─────────────────────────────────────────────────────────────────

export function useFromScratch({
  docType,
  docTitle,
  onFullDraftReady,
}: UseFromScratchOptions) {
  const [messages, setMessages] = useState<ScratchMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [isSearching, setIsSearching] = useState(false);
  const [threadId, setThreadId] = useState<string | null>(null);

  // Refs for synchronous access inside async callbacks (prevents closure traps)
  const threadIdRef = useRef<string | null>(null);
  const streamingRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);

  // B1-T3: docType/docTitle refs so sendMessage reads current values
  // without depending on them (avoids stale closure from [] deps).
  // Pattern matches the existing threadIdRef usage.
  const docTypeRef = useRef(docType);
  docTypeRef.current = docType;
  const docTitleRef = useRef(docTitle);
  docTitleRef.current = docTitle;

  // B1-T3: abort in-flight stream on unmount (prevents setState on
  // unmounted component + backend continues burning tokens).
  useEffect(
    () => () => {
      abortRef.current?.abort();
    },
    [],
  );

  // ── SSE parser (delegates to exported parseScratchSSE) ─────────────

  const parseSSEStream = useCallback(
    async (body: ReadableStream<Uint8Array>, onChunkReset?: () => void) => {
      // Progressive rendering (§5.2B): create the streaming assistant message
      // LAZILY on the first text chunk, then update its content per-chunk as
      // the draft streams in — the user watches the text appear incrementally,
      // not a spinner-then-bulk at stream-end. The placeholder is created
      // lazily (not upfront) so clarification turns (no streamed text) don't
      // show an empty/stuck bubble while waiting for the tool call.
      const assistantId = `a-${Date.now()}`;
      let placeholderShown = false;

      try {
        const result = await parseScratchSSE(
          body,
          (accumulated) => {
            // onChunk: progressive text rendering only (placeholder create/update).
            // 文本到达 = 搜索阶段结束（setIsSearching(false) 幂等，React 无变更时 bail-out）。
            setIsSearching(false);
            if (!placeholderShown) {
              placeholderShown = true;
              setMessages((prev) => [
                ...prev,
                { id: assistantId, role: "assistant", content: accumulated },
              ]);
            } else {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId ? { ...m, content: accumulated } : m,
                ),
              );
            }
          },
          // P1-5: reset the idle-heartbeat on EVERY valid SSE frame (tool calls,
          // values, end), not just AI-text chunks. A >30s silent search+reasoning
          // window must not abort the run.
          onChunkReset,
          // Fix 2.3: agent 调用搜索工具时置位，UI 显示「正在联网搜索」。
          () => setIsSearching(true),
        );

        if (!result) {
          // No meaningful content → remove the (possibly empty) placeholder.
          if (placeholderShown) {
            setMessages((prev) => prev.filter((m) => m.id !== assistantId));
          }
          return;
        }

        if (result.isClarification) {
          // Clarification question arrived as a tool call (no streamed text) →
          // the placeholder may not exist yet. Create or update it atomically.
          const clarificationMsg: ScratchMessage = {
            id: assistantId,
            role: "assistant",
            content: result.text || "请回答以上问题",
            isClarification: true,
            options: result.options,
          };
          setMessages((prev) =>
            placeholderShown
              ? prev.map((m) => (m.id === assistantId ? clarificationMsg : m))
              : [...prev, clarificationMsg],
          );
        } else {
          // Draft / outline / truncated draft → finalize the streamed text +
          // thread buildResult's signal-derived flags (isOutline / isFullDoc) and
          // the truncation flag (isTruncated) so the panel reads them directly.
          const draftMsg: ScratchMessage = {
            id: assistantId,
            role: "assistant",
            content: result.text,
            isOutline: result.isOutline,
            isFullDoc: result.isFullDoc,
            isTruncated: result.isTruncated,
          };
          setMessages((prev) =>
            placeholderShown
              ? prev.map((m) => (m.id === assistantId ? draftMsg : m))
              : [...prev, draftMsg],
          );
          if (result.isFullDoc) {
            onFullDraftReady(result.text);
          }
        }
      } catch (err) {
        // Stream error/abort mid-stream (Finding #1): if the placeholder has
        // partial content, flag it isTruncated so the recovery UI (⚠️ +
        // "继续生成") shows — NOT an orphaned partial draft with no recovery
        // affordance. Then re-throw so sendMessage's catch handles the error
        // message (separate error bubble for non-abort errors; silent for
        // AbortError per the existing timeout-abort contract).
        if (placeholderShown) {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId ? finalizePlaceholderOnError(m) : m,
            ),
          );
        }
        throw err;
      }
    },
    [onFullDraftReady],
  );

  // ── Core: send message to co-writer via thread runs/stream ────────

  const sendMessage = useCallback(
    async (text: string, isFirst: boolean) => {
      const client = getAPIClient();

      // Use ref for synchronous thread ID read/write (prevents closure trap)
      let tid = threadIdRef.current;
      if (!tid) {
        const thread = await client.threads.create({
          metadata: { agent_name: "co-writer", source: "writer-from-scratch" },
        });
        tid = thread.thread_id;
        threadIdRef.current = tid;
        setThreadId(tid);
      }

      // Wrap user input in tags to reduce prompt injection risk.
      // For force-direct-generate types (general/podcast/script) on the first
      // send, a "直接生成，无需提问" hint is appended so the SOUL generates
      // directly without asking. §5.2C.
      // B1-T3: read docType/docTitle from refs (avoids stale closure
      // when startSession/reply/skipClarification capture old values)
      const prompt = buildScratchPrompt(
        text,
        isFirst,
        docTypeRef.current,
        docTitleRef.current,
      );

      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      // Idle-heartbeat (§5.2D): replaces the old fixed 120s wall-clock hard
      // timeout that cut off legitimately long generations mid-flight. The
      // heartbeat resets on each received chunk; it only fires (→ abort) after
      // 30s of NO chunks. Long active generations are never cut off.
      const heartbeat = createHeartbeat(30_000, () => controller.abort());
      heartbeat.reset(); // start the idle timer (covers slow first-chunk too)

      try {
        const res = await postThreadRunStream(
          tid,
          {
            assistant_id: "co-writer",
            input: {
              messages: [{ role: "user", content: prompt }],
            },
            stream_mode: ["messages-tuple", "values"],
            on_disconnect: "cancel",
          },
          controller.signal,
        );

        if (!res.ok) {
          throw new Error(`请求失败: ${res.status}`);
        }
        if (!res.body) return;

        // Parse SSE stream — pass heartbeat.reset so it's called per chunk
        // (each SSE data frame / onChunk invocation resets the idle timer).
        await parseSSEStream(res.body, heartbeat.reset);
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setMessages((prev) => [
          ...prev,
          {
            id: `e-${Date.now()}`,
            role: "assistant",
            content: `⚠️ 发生错误：${err instanceof Error ? err.message : String(err)}`,
          },
        ]);
      } finally {
        heartbeat.clear();
      }
    },
    [parseSSEStream], // B1-T3: docType/docTitle 走 refs 免 deps
  );

  // ── Start session ──────────────────────────────────────────────────

  const startSession = useCallback(
    async (topic: string) => {
      if (streamingRef.current) return;
      streamingRef.current = true;
      setIsStreaming(true);
      setMessages([]);

      const userMsg: ScratchMessage = {
        id: `u-${Date.now()}`,
        role: "user",
        content: topic,
      };
      setMessages([userMsg]);

      try {
        await sendMessage(topic, true);
      } finally {
        streamingRef.current = false;
        setIsStreaming(false);
      }
    },
    [sendMessage],
  );

  // ── Send message (reply to clarification or new input) ────────────

  const reply = useCallback(
    async (text: string) => {
      if (streamingRef.current) return;
      streamingRef.current = true;
      setIsStreaming(true);

      const userMsg: ScratchMessage = {
        id: `u-${Date.now()}`,
        role: "user",
        content: text,
      };
      setMessages((prev) => [...prev, userMsg]);

      try {
        await sendMessage(text, false);
      } finally {
        streamingRef.current = false;
        setIsStreaming(false);
      }
    },
    [sendMessage],
  );

  // ── Skip clarification ("直接生成") ─────────────────────────────────
  //
  // When the user clicks "直接生成" on a clarification, send a skip instruction
  // via reply() DIRECTLY (not through the panel's handleReply, which has a
  // pre-existing bug: handleReply ignores its argument and reads replyInput
  // state instead — so the skip text would never reach the backend). By calling
  // reply() directly, the skip instruction reaches sendMessage → postThreadRunStream
  // → backend. §5.2C.
  const skipClarification = useCallback(async () => {
    await reply(SKIP_CLARIFICATION_INSTRUCTION);
  }, [reply]);

  // ── Reset ──────────────────────────────────────────────────────────

  const reset = useCallback(() => {
    abortRef.current?.abort();
    streamingRef.current = false;
    setMessages([]);
    threadIdRef.current = null;
    setThreadId(null);
    setIsStreaming(false);
    setIsSearching(false);
  }, []);

  return {
    messages,
    isStreaming,
    isSearching,
    threadId,
    startSession,
    reply,
    skipClarification,
    reset,
  };
}

// ── "直接生成" skippable questions (§5.2C + §5.1.3) ──────────────────────────

/**
 * Doc types that default to direct generation (no forced ask_clarification).
 *
 * `general`/`podcast`/`script` → true: the initial send includes a
 * "直接生成，无需提问" hint so the SOUL/model generates directly without asking.
 * `article`/`case`/`course` → false: the model may ask at most 1 key question
 * (skippable — the user can always click "直接生成" to skip).
 *
 * Pure so it is unit-testable without React.
 */
export function shouldForceDirectGenerate(docType: string): boolean {
  return docType === "general" || docType === "podcast" || docType === "script";
}

/**
 * Hint appended to the initial prompt for force-direct-generate types so the
 * SOUL/model knows to generate directly without asking questions. §5.2C.
 */
export const DIRECT_GENERATE_HINT = "直接生成，无需提问";

/**
 * Skip instruction sent when the user clicks "直接生成" on a clarification.
 * The SOUL honors this directive and proceeds to generate without re-asking.
 * §5.2C.
 */
export const SKIP_CLARIFICATION_INSTRUCTION =
  "请直接基于已有信息生成，无需更多提问";

/**
 * Build the prompt sent to the co-writer for a from-scratch message.
 *
 * Extracted from `sendMessage` so the prompt-build (including the
 * "直接生成" hint for force-types on initial send) is unit-testable without
 * React/DOM. §5.2C.
 *
 * - `isFirst=true`: wraps the topic in `[从零创作] [类型: ...]` tags; for
 *   force-direct-generate types (general/podcast/script), appends the
 *   `DIRECT_GENERATE_HINT` so the SOUL generates without asking.
 * - `isFirst=false` (reply/skip): wraps the text in `<user_input>` tags only
 *   (no hint — the skip instruction goes through cleanly).
 *
 * @param text     The user's input (topic for first send; reply/skip text otherwise).
 * @param isFirst  True for the initial topic send; false for replies/skips.
 * @param docType  The document type (controls the direct-generate hint).
 * @param docTitle Optional document title reference (first send only).
 */
export function buildScratchPrompt(
  text: string,
  isFirst: boolean,
  docType: string,
  docTitle?: string,
): string {
  if (isFirst) {
    const hint = shouldForceDirectGenerate(docType)
      ? `\n${DIRECT_GENERATE_HINT}`
      : "";
    return `[从零创作] [类型: ${docType}]${hint}\n主题：<user_input>${text}</user_input>${docTitle ? `\n标题参考：${docTitle}` : ""}`;
  }
  return `<user_input>${text}</user_input>`;
}

// ── Pure helpers (progressive rendering + truncation detection) ───────────

/**
 * Pure per-chunk text accumulation: append a new text chunk to the previously
 * accumulated text. Extracted so the progressive-rendering path (§5.2B — the
 * draft streams in per-chunk, not spinner-then-bulk) is unit-testable without
 * React. Used inside `parseScratchSSE` for per-message-id buffer appends.
 */
export function accumulateChunk(prev: string, chunk: string): string {
  return prev + chunk;
}

/**
 * Minimum accumulated length (in chars) for a no-marker stream to be considered
 * a likely-truncated draft. Below this, content is treated as a short reply /
 * clarification and no truncation indicator is shown. Matches the
 * `isFullDocHeuristic` length floor so the two heuristics agree on "substantial".
 */
export const TRUNCATION_THRESHOLD = 200;

/**
 * Detect whether a stream that ended WITHOUT a `[[STAGE:...]]` marker likely
 * truncated mid-generation (§5.1.2).
 *
 * - `stage !== null` → a `[[STAGE:DRAFT]]`/`[[STAGE:OUTLINE]]` marker was
 *   emitted at the start → the draft/outline was delivered intentionally → NOT
 *   truncated (even if the content is short).
 * - `stage === null && textLen > threshold` → substantial content with no
 *   marker → likely token-truncated mid-generation → flag as truncated.
 * - `stage === null && textLen <= threshold` → short clarification/ack reply →
 *   NOT truncated (no false indicator on ask_clarification turns).
 *
 * Pure so it is unit-testable without React.
 */
export function detectTruncation(
  stage: ScratchStage | null,
  textLen: number,
  threshold: number = TRUNCATION_THRESHOLD,
): boolean {
  if (stage !== null) return false;
  return textLen > threshold;
}

/**
 * Flag a streaming placeholder message as truncated when the stream errors or
 * aborts mid-stream (Finding #1). Without this, the placeholder remains with
 * partial content and NO `isTruncated` flag → the user sees partial content
 * with no "继续生成" recovery affordance.
 *
 * Used by `parseSSEStream`'s catch block to ensure the recovery UI shows on
 * stream error/abort. Pure so it is unit-testable without React.
 */
export function finalizePlaceholderOnError(
  msg: ScratchMessage,
): ScratchMessage {
  return { ...msg, isTruncated: true };
}

// ── Exported SSE parser (testable without React) ──────────────────────────

/**
 * Parse a co-writer SSE stream and return the final result.
 *
 * Handles two exit paths:
 * 1. `event: end` received → returns immediately
 * 2. Stream closes (done=true) without end → fallback finalize
 *
 * Returns null if no meaningful content was received.
 *
 * @param onChunk Optional progressive-rendering callback (§5.2B): invoked with
 *   the accumulated assistant text each time a new text chunk arrives, so the
 *   consumer can update `message.content` per-chunk (the user watches the draft
 *   being written, not a spinner-then-bulk). NOT called for clarification tool
 *   calls (those arrive atomically, not as streamed text).
 */
export async function parseScratchSSE(
  body: ReadableStream<Uint8Array>,
  onChunk?: (accumulatedText: string) => void,
  onFrame?: () => void,
  onSearchTool?: (toolName: string) => void,
): Promise<ScratchSSEResult | null> {
  const msgBuffers = new Map<string, string>();
  let sawClarification = false;
  let clarificationOptions: string[] | undefined;
  let assistantText = "";
  let receivedEnd = false;

  const buildResult = (): ScratchSSEResult | null => {
    // W22: strip any trailing [[CITATIONS:<json>]] marker so the raw token
    // doesn't leak into user-visible text. The co-writer appends this marker
    // to ANY search-using response (not just full-draft-edit); parseCitations
    // returns the body with the marker removed (or unchanged if no marker).
    const { body: cleanText } = parseCitations(assistantText);

    if (sawClarification) {
      return {
        isClarification: true,
        text: cleanText,
        options: clarificationOptions,
        isFullDoc: false,
        // Clarifications are intentional questions, never truncated.
        isTruncated: false,
      };
    }
    if (cleanText) {
      // Structured stage signal: when the co-writer emits a leading
      // `[[STAGE:OUTLINE]]` / `[[STAGE:DRAFT]]` marker, trust it entirely — no
      // string-guessing. §11 fallback: when absent, use the legacy heuristic so
      // pre-marker SOULs/models still work.
      const { stage, text } = parseStageSignal(cleanText);
      if (stage === "outline") {
        return {
          isClarification: false,
          text,
          isFullDoc: false,
          isOutline: true,
          // Signal present → the outline was delivered intentionally → never
          // truncated, even if short.
          isTruncated: false,
        };
      }
      if (stage === "draft") {
        return {
          isClarification: false,
          text,
          isFullDoc: true,
          isOutline: false,
          isTruncated: false,
        };
      }
      // No marker → §11 fallback (marker-stripped text is a no-op here).
      // Also flag truncation: no signal + substantial content likely means the
      // generation was token-truncated mid-stream before the model could emit
      // a marker (§5.1.2). Short content is a normal reply, not truncation.
      return {
        isClarification: false,
        text,
        isFullDoc: isFullDocHeuristic(text),
        isOutline: false,
        isTruncated: detectTruncation(stage, text.length),
      };
    }
    return null;
  };

  for await (const lines of sseFrames(body)) {
    const parsedFrame = parseSseEvent(lines);
    if (!parsedFrame) continue;
    const eventType = parsedFrame.event;
    const eventData = parsedFrame.data;

    // P1-5: reset the idle-heartbeat on EVERY valid frame (tool calls,
    // values, end) — not just AI-text chunks. Article/case types search
    // during generation; a >30s silent search+reasoning window must not
    // abort the run. The old code only reset inside onChunk (text only).
    onFrame?.();

    if (eventType === "end") {
      receivedEnd = true;
      return buildResult();
    }

    if (eventType === "messages") {
      // Narrow try/catch to ONLY the JSON parse so consumer errors (e.g.
      // from onChunk's setMessages callback) are NOT mis-classified as
      // malformed JSON and silently swallowed (Finding #2). The
      // JSON-parse error handling (skip malformed frames) remains.
      let parsed: unknown;
      try {
        parsed = JSON.parse(eventData);
      } catch {
        // Skip malformed JSON
        continue;
      }

      if (Array.isArray(parsed) && parsed[0]) {
        const msg = parsed[0];
        // 非 AI 消息黑名单收敛至 core/writer/sse.ts（issue ④ 注释见彼处）
        if (isNonAiMessage(msg as Record<string, unknown>)) continue;

        const msgId = String(msg.id ?? "");

        // Fix 2.3: surface web-search tool calls (metaso/360/web_search) so
        // 从零创作 shows 「正在联网搜索」 instead of a silent spinner.
        const searchTool = detectSearchToolCall(msg);
        if (searchTool) onSearchTool?.(searchTool);

        // Check for tool calls (ask_clarification)
        if (msg.tool_calls) {
          for (const tc of msg.tool_calls) {
            if (tc.name === "ask_clarification") {
              sawClarification = true;
              const args =
                typeof tc.args === "string" ? JSON.parse(tc.args) : tc.args;
              if (args?.question) {
                assistantText = args.question;
              }
              clarificationOptions = args?.options;
            }
          }
        }

        const text = extractMessageText(msg);
        if (text) {
          const prev = msgBuffers.get(msgId) ?? "";
          const next = accumulateChunk(prev, text);
          msgBuffers.set(msgId, next);
          if (!sawClarification) {
            assistantText = Array.from(msgBuffers.values()).join("");
            // Progressive rendering (§5.2B): notify the consumer with the
            // accumulated text so it can update message.content per-chunk
            // (the user watches the draft being written, not a
            // spinner-then-bulk). NOT called for clarification turns
            // (those arrive atomically via tool_calls, not streamed text).
            onChunk?.(assistantText);
          }
        }
      }
    }
  }

  // Fallback: stream ended (done=true) without "end" event (network truncation)
  if (!receivedEnd) {
    return buildResult();
  }
  return null;
}
