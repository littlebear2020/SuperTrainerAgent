/**
 * Writer module types.
 */

/** Writer document (synced with backend WriterDoc). */
export interface WriterDoc {
  id: string;
  user_id: string;
  title: string;
  /** Markdown text (stored as content_json TEXT in backend). */
  content_json: string;
  doc_type: DocType;
  /** B2-T2: optimistic lock version for CAS saves */
  version: number;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
}

/**
 * Public shared-doc view (backend SharedDocResponse) — the owner user_id is
 * deliberately NOT exposed to anonymous viewers (P2-29). Distinct from
 * WriterDoc so share-page code can't accidentally consume a field the
 * backend never sends (batch-B review follow-up).
 */
export type SharedWriterDoc = Omit<WriterDoc, "user_id">;

/** Document type — controls AI tool strategy, sidebar panels, and export pipelines. */
export type DocType =
  | "article"
  | "case"
  | "script"
  | "podcast"
  | "course"
  | "general";

/** Create/update document params. */
export interface WriterDocCreateParams {
  title: string;
  content_json?: string;
  doc_type?: DocType;
}

export interface WriterDocUpdateParams {
  title?: string;
  content_json?: string;
  doc_type?: DocType;
  /** Force a pre-apply version snapshot (bypasses 30s auto-throttle) for
   * destructive content replacements - guarantees an undo point (§9.3). */
  force_snapshot?: boolean;
  /** B2-T2: CAS optimistic lock via integer version. When present,
   * the PUT 409s if the stored version doesn't match. */
  base_version?: number;
}

/** AI assist operation types. */
export type AssistOp =
  | "rewrite"
  | "polish"
  | "translate"
  | "expand"
  | "summarize"
  | "grammar"
  | "tone"
  | "shorten"
  | "continue";

/** AI assist request. */
export interface AssistRequest {
  op: AssistOp;
  text: string;
  context?: string;
  target_lang?: string;
  tone?: string;
  instruction?: string;
  full_doc?: boolean;
  /** Document type — controls style hint injection (W1 backend, W2 forwarding). */
  doc_type?: DocType;
}

/** PPT export request. */
export interface PptExportParams {
  doc_id: string;
  template: "default" | "dark" | "minimal" | "training";
  title?: string;
}

/** Task status from unified_tasks. */
export interface TaskBrief {
  id: string;
  user_id: string;
  domain: string;
  task_type: string;
  status: string;
  progress: number;
  error_message: string | null;
  retry_count: number;
  created_at: string | null;
  updated_at: string | null;
  /** 批次 C (2026-08-13)：每任务花费（后端 task_cost 计算，null=无法计价）。 */
  cost: number | null;
  currency: string | null;
}

/** Task detail with payload/result. */
export interface TaskDetail extends TaskBrief {
  payload: Record<string, unknown>;
  result: Record<string, unknown>;
}

/** Agent edit event from co-writer Agent chain. */
export type AgentEditEvent =
  | { type: "status"; content: string }
  | { type: "trace"; thinking: string; step: string }
  | { type: "text"; content: string }
  | { type: "done"; content: string };

/** Agent edit mode. */
export type AgentEditMode = "polish" | "shorten" | "expand";

/** Document share info. */
export interface DocShare {
  id: string;
  doc_id: string;
  owner_id: string;
  share_token: string;
  expires_at: string | null;
  created_at: string | null;
}

/** Version brief (no content). */
export interface DocVersionBrief {
  id: string;
  doc_id: string;
  version_number: number;
  title: string;
  snapshot_reason:
    | "auto"
    | "manual"
    | "restore"
    | "destructive"
    | "rename"
    | "retype";
  created_at: string | null;
}

/** Version detail with content. */
export interface DocVersionDetail extends DocVersionBrief {
  content_json: string;
}
