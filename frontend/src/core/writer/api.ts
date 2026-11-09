/**
 * Writer API client — document CRUD, AI assist SSE, PPT export.
 */

import { fetch } from "@/core/api/fetcher";
import { readFastApiErrorDetail } from "@/core/api/read-fastapi-error";
import { getBackendBaseURL } from "@/core/config";

import {
  extractMessageText,
  isNonAiMessage,
  parseSseEvent,
  sseFrames,
} from "./sse";
import type {
  AgentEditEvent,
  AgentEditMode,
  AssistRequest,
  DocShare,
  DocVersionBrief,
  DocVersionDetail,
  DocType,
  PptExportParams,
  SharedWriterDoc,
  TaskBrief,
  TaskDetail,
  WriterDoc,
  WriterDocCreateParams,
  WriterDocUpdateParams,
} from "./types";

// ── Helpers ──────────────────────────────────────────────────────────────

/** Parse backend WriterDoc: content_json stays as string (Markdown). */
function parseDoc(raw: Record<string, unknown>): WriterDoc {
  const content = raw.content_json;
  return {
    ...raw,
    content_json:
      typeof content === "string"
        ? content
        : content != null
          ? JSON.stringify(content)
          : "",
  } as WriterDoc;
}

function parseDocs(raws: Record<string, unknown>[]): WriterDoc[] {
  return raws.map(parseDoc);
}

const BASE = `${getBackendBaseURL()}/api/writer/docs`;
const ASSIST_BASE = `${getBackendBaseURL()}/api/ai/assist`;
const TASKS_BASE = `${getBackendBaseURL()}/api/content/tasks`;

// ── Document CRUD ────────────────────────────────────────────────────────

/** B5-T1: list query parameters for paginated document listing. */
export interface ListWriterDocsParams {
  limit?: number;
  offset?: number;
  search?: string;
  doc_type?: string;
  sort_by?: "updated_at" | "created_at" | "title";
  sort_dir?: "asc" | "desc";
}

/** B5-T1: paginated list response (replaces raw WriterDoc[]). */
export interface WriterDocListResponse {
  items: WriterDoc[];
  has_more: boolean;
}

/** List all writer documents for the current user. */
export async function listWriterDocs(
  params?: ListWriterDocsParams,
): Promise<WriterDocListResponse> {
  const searchParams = new URLSearchParams();
  if (params?.limit != null) searchParams.set("limit", String(params.limit));
  if (params?.offset != null) searchParams.set("offset", String(params.offset));
  if (params?.search) searchParams.set("search", params.search);
  if (params?.doc_type) searchParams.set("doc_type", params.doc_type);
  if (params?.sort_by) searchParams.set("sort_by", params.sort_by);
  if (params?.sort_dir) searchParams.set("sort_dir", params.sort_dir);
  const qs = searchParams.toString();
  const res = await fetch(qs ? `${BASE}?${qs}` : BASE);
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Failed to list documents",
    );
    throw new Error(detail);
  }
  const data = await res.json();
  // B5-T1: handle both new {items, has_more} and old array format (compatibility)
  if (Array.isArray(data)) {
    return { items: parseDocs(data), has_more: false };
  }
  return {
    items: parseDocs(data.items ?? []),
    has_more: data.has_more ?? false,
  };
}

/** Get a single writer document. */
export async function getWriterDoc(docId: string): Promise<WriterDoc> {
  const res = await fetch(`${BASE}/${encodeURIComponent(docId)}`);
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(res, "Failed to get document");
    throw new Error(detail);
  }
  return parseDoc(await res.json());
}

/** Create a new writer document. */
export async function createWriterDoc(
  params: WriterDocCreateParams,
): Promise<WriterDoc> {
  const res = await fetch(BASE, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Failed to create document",
    );
    throw new Error(detail);
  }
  return parseDoc(await res.json());
}

/** Thrown when a writer-doc PUT 409s on a stale base_version (B2-T2). */
export class WriterDocConflictError extends Error {
  readonly status = 409;
}

/** Update a writer document. */
export async function updateWriterDoc(
  docId: string,
  params: WriterDocUpdateParams,
): Promise<WriterDoc> {
  const res = await fetch(`${BASE}/${encodeURIComponent(docId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Failed to update document",
    );
    if (res.status === 409) {
      throw new WriterDocConflictError(detail);
    }
    throw new Error(detail);
  }
  return res.json();
}

/** Delete a writer document. */
export async function deleteWriterDoc(docId: string): Promise<void> {
  const res = await fetch(`${BASE}/${encodeURIComponent(docId)}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Failed to delete document",
    );
    throw new Error(detail);
  }
}

// ── AI Assist (SSE streaming) ────────────────────────────────────────────

/**
 * Stream AI assist response. Yields text chunks via async generator.
 */
export async function* streamAssist(
  req: AssistRequest,
  signal?: AbortSignal,
): AsyncGenerator<string, void, undefined> {
  const res = await fetch(ASSIST_BASE, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
    // P2-12: connect the caller's AbortController so cancel/timeout can
    // interrupt the in-flight fetch + stream reads (not just between chunks).
    ...(signal ? { signal } : {}),
  });
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(res, "Failed to get AI assist");
    throw new Error(detail);
  }
  if (!res.body) return;

  // B1-T7: split on \n\n (SSE frame boundary) instead of \n — the old \n
  // split could break JSON payloads containing newlines. 帧拆分收敛至
  // core/writer/sse.ts（与 streamAgentEdit / parseScratchSSE 共用）。
  for await (const lines of sseFrames(res.body)) {
    // Extract data: lines from the frame (a frame may have multiple lines)
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed.startsWith("data: ")) continue;
      const data = trimmed.slice(6);
      if (data === "[DONE]") return;

      let parsed: { text?: string; type?: string; content?: string };
      try {
        parsed = JSON.parse(data);
      } catch {
        // Skip malformed JSON
        continue;
      }
      // C2 闭环:后端超时/中途异常发的错误帧(type:"error")——抛给调用方
      // 的既有 catch 显示 content(超时文案/AI 服务异常:...),不再静默吞掉
      // (200 头已发出,断连只会给用户一个无意义的网络错误)。空串/纯空白
      // content 回落中性文案(?? 只挡 null/undefined,挡不住 "")
      if (parsed.type === "error") {
        const content = parsed.content ?? "";
        throw new Error(content.trim() ? content : "AI 服务异常,请重试");
      }
      if (parsed.text) {
        yield parsed.text;
      }
    }
  }
}

// ── Agent Edit (co-writer via Agent chain) ─────────────────────────────────

const MODE_LABELS: Record<AgentEditMode, string> = {
  polish: "全文润色",
  shorten: "全文精简",
  expand: "全文扩写",
};

/** Selection-level operation prompts — differentiated instructions for each quick op. */
const SELECTION_OP_PROMPTS: Record<string, string> = {
  polish:
    "优化这段文字的表达流畅性和专业度。保持原有结构和核心内容不变，只改善遣词造句和语句衔接。",
  rewrite:
    "用完全不同的表达方式重写这段文字。保持原意不变，但换用不同的句式结构、词汇和修辞手法，让表达焕然一新。",
  expand:
    "在保持原文核心意思的基础上，补充更多细节、例证、背景说明或过渡句。可以适当展开论述，使内容更加丰富饱满。",
  summarize:
    "将这段文字提炼为 2-3 个核心要点的概要。使用简洁的要点列表形式，只保留最关键的信息。",
};

/**
 * Build selection context with surrounding text and document outline.
 */
export function buildSelectionContext(
  docTitle: string | undefined,
  fullMarkdown: string,
  selectedText: string,
): string {
  const parts: string[] = [];

  if (docTitle) {
    parts.push(`文档标题：${docTitle}`);
  }

  const selStart = fullMarkdown.indexOf(selectedText);
  if (selStart >= 0) {
    const beforeStart = Math.max(0, selStart - 300);
    const afterEnd = Math.min(
      fullMarkdown.length,
      selStart + selectedText.length + 300,
    );

    const before = fullMarkdown.slice(beforeStart, selStart).trim();
    const after = fullMarkdown
      .slice(selStart + selectedText.length, afterEnd)
      .trim();

    if (before) {
      parts.push(`选区前文：\n${before}`);
    }
    if (after) {
      parts.push(`选区后文：\n${after}`);
    }
  }

  const headings = fullMarkdown
    .split("\n")
    .filter((line) => /^#{1,6}\s/.test(line))
    .slice(0, 15)
    .join("\n");
  if (headings) {
    parts.push(`文档大纲：\n${headings}`);
  }

  return parts.join("\n\n");
}

/** Names that indicate a web-search tool call (config web_search + MCP metaso /
 * 360, plus common Tavily/Jina backends). Fix 2.3: lets the UI surface
 * 「正在联网搜索」 instead of a silent spinner while the agent searches. */
const SEARCH_TOOL_RE = /search|metaso|so_360|tavily|jina|bocha/i;

/**
 * Detect a search tool call in a streamed AI message chunk. Returns the tool
 * name when the message initiates a search call, else null. Reads
 * `tool_calls`, streaming `tool_call_chunks`, and `additional_kwargs.tool_calls`.
 */
export function detectSearchToolCall(msg: unknown): string | null {
  if (!msg || typeof msg !== "object") return null;
  const m = msg as Record<string, unknown>;
  const ak = m.additional_kwargs as { tool_calls?: unknown[] } | undefined;
  const calls =
    (m.tool_calls as Array<{ name?: string }> | undefined) ??
    (m.tool_call_chunks as Array<{ name?: string }> | undefined) ??
    (ak?.tool_calls as Array<{ name?: string }> | undefined);
  if (!Array.isArray(calls)) return null;
  for (const c of calls) {
    const name = String((c as { name?: string })?.name ?? "");
    if (name && SEARCH_TOOL_RE.test(name)) return name;
  }
  return null;
}

/**
 * Stream full-draft edit via Agent chain (co-writer agent).
 * Uses stateless /api/runs/stream which auto-creates ephemeral thread.
 */
export async function* streamAgentEdit(opts: {
  instruction: string;
  markdown: string;
  mode: AgentEditMode;
  contentType?: DocType;
  dataSources?: ("knowledge" | "web")[];
  selectionOp?: string;
  context?: string;
  /** P1-9: wire the caller's AbortController into the underlying fetch so
   * cancelling the modal actually cancels the backend stream (previously the
   * UI stopped but the run kept burning tokens server-side). */
  signal?: AbortSignal;
}): AsyncGenerator<AgentEditEvent, void, undefined> {
  let prompt: string;

  if (opts.selectionOp && SELECTION_OP_PROMPTS[opts.selectionOp]) {
    // Selection quick op — use differentiated prompt template
    const opInstruction = SELECTION_OP_PROMPTS[opts.selectionOp];
    const contextBlock = opts.context ? `\n\n${opts.context}` : "";
    prompt = `${opInstruction}${contextBlock}\n\n需要处理的文本片段：\n\n${opts.markdown}`;
  } else {
    // Full-draft edit or custom instruction
    const modeLabel = MODE_LABELS[opts.mode];
    prompt = opts.instruction
      ? `${opts.instruction}\n\n${opts.markdown}`
      : `请对以下培训文档执行「${modeLabel}」：\n\n${opts.markdown}`;
  }

  // Append data source instructions if specified
  if (opts.dataSources?.length) {
    const sourceHints: string[] = [];
    if (opts.dataSources.includes("knowledge")) {
      sourceHints.push("请参考知识库中的相关资料来丰富内容");
    }
    if (opts.dataSources.includes("web")) {
      sourceHints.push("请参考网络搜索结果来补充最新信息");
    }
    if (sourceHints.length) {
      prompt = `${sourceHints.join("；")}。\n\n${prompt}`;
    }
  }

  // Inject content type identifier for SOUL.md type-specific behavior
  if (opts.contentType) {
    prompt = `[类型: ${opts.contentType}]\n\n${prompt}`;
  }

  yield { type: "status", content: `正在${MODE_LABELS[opts.mode]}...` };

  // Use stateless /api/runs/stream — auto-creates ephemeral thread, no pre-existing thread required
  const res = await fetch(`${getBackendBaseURL()}/api/runs/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    signal: opts.signal ?? null,
    body: JSON.stringify({
      assistant_id: "co-writer",
      input: {
        messages: [{ role: "user", content: prompt }],
        data_sources: opts.dataSources ?? [],
      },
      stream_mode: ["messages-tuple", "values"],
      on_disconnect: "cancel",
    }),
  });

  if (!res.ok) {
    const detail = await readFastApiErrorDetail(res, "Agent edit failed");
    throw new Error(detail);
  }
  if (!res.body) return;

  // Track message content by message ID for assembling streaming chunks
  const messageBuffers = new Map<string, string>();

  for await (const lines of sseFrames(res.body)) {
    const frame = parseSseEvent(lines);
    if (!frame) continue;
    const eventType = frame.event;
    const eventData = frame.data;

    // Handle event: end
    if (eventType === "end") {
      // Return accumulated text as done
      const allText = Array.from(messageBuffers.values()).join("");
      yield { type: "done", content: allText };
      return;
    }

    // Handle event: messages — streaming AI text chunks
    // Format: [serialized_message_chunk, metadata_dict]
    // e.g. [{type:"AIMessageChunk", content:"...", id:"msg-xxx"}, {run_id:"..."}]
    if (eventType === "messages") {
      try {
        const parsed = JSON.parse(eventData);
        if (Array.isArray(parsed) && parsed.length >= 1) {
          const msg = parsed[0];
          if (msg && typeof msg === "object") {
            // 非 AI 消息黑名单收敛至 core/writer/sse.ts（issue ④ 注释见彼处）
            if (isNonAiMessage(msg as Record<string, unknown>)) continue;

            // Fix 2.3: surface web-search tool calls as a status event so the
            // user can see the agent is searching (otherwise it's a silent
            // spinner — 「感觉不到联网」).
            const searchTool = detectSearchToolCall(msg);
            if (searchTool) {
              yield { type: "status", content: "🌐 正在联网搜索…" };
            }

            const msgId = String(msg.id ?? "");
            const text = extractMessageText(msg);

            if (text) {
              // For streaming deltas, append to buffer per message ID
              const prev = messageBuffers.get(msgId) ?? "";
              messageBuffers.set(msgId, prev + text);

              // Only yield the new delta
              yield { type: "text", content: text };
            }
          }
        }
      } catch {
        // Skip malformed JSON
      }
      continue;
    }

    // Handle event: values — state updates (may contain trace info)
    if (eventType === "values") {
      try {
        const parsed = JSON.parse(eventData);
        // Extract trace/thinking from middleware state updates
        const keys = Object.keys(parsed);
        for (const key of keys) {
          const val = parsed[key];
          if (typeof val === "object" && val !== null && "thinking" in val) {
            yield {
              type: "trace",
              thinking: String(val.thinking ?? ""),
              step: key,
            };
          }
        }
      } catch {
        // Skip malformed JSON
      }
      continue;
    }
  }

  // Stream ended without explicit 'end' event
  const allText = Array.from(messageBuffers.values()).join("");
  yield { type: "done", content: allText };
}

// ── PPT Export ───────────────────────────────────────────────────────────

/** Submit a PPT generation task. */
export async function submitPptExport(
  params: PptExportParams,
): Promise<TaskBrief> {
  const res = await fetch(TASKS_BASE, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      task_type: "ppt_gen",
      payload: {
        doc_id: params.doc_id,
        template: params.template,
        title: params.title,
      },
    }),
  });
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Failed to submit PPT export",
    );
    throw new Error(detail);
  }
  return res.json();
}

/** Get task status (for polling). */
export async function getContentTask(taskId: string): Promise<TaskDetail> {
  const res = await fetch(`${TASKS_BASE}/${encodeURIComponent(taskId)}`);
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(res, "Failed to get task");
    throw new Error(detail);
  }
  return res.json();
}

/** Get PPT download URL for a completed task. */
export function getPptDownloadUrl(taskId: string): string {
  return `${TASKS_BASE}/${encodeURIComponent(taskId)}/download`;
}

// ── PDF Export (B4-T3) ────────────────────────────────────────────────────

/** Submit a PDF generation task. */
export async function submitPdfExport(params: {
  doc_id: string;
  title?: string;
}): Promise<TaskBrief> {
  const res = await fetch(TASKS_BASE, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      task_type: "pdf_gen",
      payload: {
        doc_id: params.doc_id,
        title: params.title,
      },
    }),
  });
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Failed to submit PDF export",
    );
    throw new Error(detail);
  }
  return res.json();
}

/** Get PDF download URL for a completed task (same endpoint as PPT). */
export function getPdfDownloadUrl(taskId: string): string {
  return `${TASKS_BASE}/${encodeURIComponent(taskId)}/download`;
}

// ── Document Sharing ──────────────────────────────────────────────────────

const SHARE_BASE = `${getBackendBaseURL()}/api/writer/docs`;

/** Create a share link for a document. */
export async function createDocShare(
  docId: string,
  opts?: { expires_days?: number },
): Promise<DocShare> {
  const res = await fetch(`${SHARE_BASE}/${encodeURIComponent(docId)}/share`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(opts ?? {}),
  });
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(res, "Failed to create share");
    throw new Error(detail);
  }
  return res.json();
}

/** Get current share status for a document. */
export async function getDocShare(docId: string): Promise<DocShare | null> {
  const res = await fetch(`${SHARE_BASE}/${encodeURIComponent(docId)}/share`);
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(res, "Failed to get share");
    throw new Error(detail);
  }
  return res.json();
}

/** Revoke sharing for a document. */
export async function revokeDocShare(docId: string): Promise<void> {
  const res = await fetch(`${SHARE_BASE}/${encodeURIComponent(docId)}/share`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(res, "Failed to revoke share");
    throw new Error(detail);
  }
}

// ── Share load result classification ────────────────────────────────────

export type ShareLoadResult =
  | { state: "none" }
  | { state: "loaded"; data: DocShare }
  | { state: "failed"; error: string };

/**
 * Classify a getDocShare result (or thrown error) into a tri-state:
 * - "none": no share exists (null) → show create-form
 * - "loaded": share exists → show share panel
 * - "failed": load error → show error + retry (NOT create-form)
 *
 * Accepts the resolved value (DocShare | null) or a thrown value (Error | unknown).
 */
export function classifyShareLoadResult(
  result: DocShare | null | Error | unknown,
): ShareLoadResult {
  if (result instanceof Error) {
    return { state: "failed", error: result.message };
  }
  if (typeof result === "string" && result.length > 0) {
    return { state: "failed", error: result };
  }
  if (result === null || result === undefined) {
    return { state: "none" };
  }
  return { state: "loaded", data: result as DocShare };
}

// ── Version History ──────────────────────────────────────────────────────

/** List document versions (no content). */
export async function listDocVersions(
  docId: string,
): Promise<DocVersionBrief[]> {
  const res = await fetch(`${BASE}/${encodeURIComponent(docId)}/versions`);
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(res, "Failed to list versions");
    throw new Error(detail);
  }
  return res.json();
}

/** Get a specific version with content. */
export async function getDocVersion(
  docId: string,
  versionId: string,
): Promise<DocVersionDetail> {
  const res = await fetch(
    `${BASE}/${encodeURIComponent(docId)}/versions/${encodeURIComponent(versionId)}`,
  );
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(res, "Failed to get version");
    throw new Error(detail);
  }
  return res.json();
}

/** Restore document to a specific version. */
export async function restoreDocVersion(
  docId: string,
  versionId: string,
): Promise<WriterDoc> {
  const res = await fetch(
    `${BASE}/${encodeURIComponent(docId)}/versions/${encodeURIComponent(versionId)}/restore`,
    {
      method: "POST",
    },
  );
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Failed to restore version",
    );
    throw new Error(detail);
  }
  return res.json();
}
