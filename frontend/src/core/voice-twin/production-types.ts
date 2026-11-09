/**
 * TypeScript interfaces for voice production task API.
 * @see backend/app/gateway/voice_twin/production_schemas.py
 */

/** Production task status. */
export type ProductionTaskStatus =
  | "pending"
  | "extracting"
  | "converting"
  | "synthesizing"
  | "composing"
  | "ready"
  | "failed";

/** Production task type. */
export type ProductionTaskType = "script_to_audio" | "ppt_to_video";

/** Production task record. */
export interface ProductionTask {
  id: string;
  task_type: ProductionTaskType;
  status: ProductionTaskStatus;
  progress: number;
  voice_name: string;
  title?: string | null;
  slide_count?: number | null;
  file_size?: number | null;
  error_message?: string | null;
  created_at?: string;
  updated_at?: string;
  /** 批次 C (2026-08-13)：每任务花费（后端 task_cost 计算，null=无法计价）。 */
  cost: number | null;
  currency: string | null;
}

// ---------------------------------------------------------------------------
// Requests
// ---------------------------------------------------------------------------

/** Result of document text extraction (preview only). */
export interface DocExtractResult {
  text: string;
  char_count: number;
  filename: string;
  truncated: boolean;
}

/** Parameters for script-to-audio task. */
export interface ScriptToAudioParams {
  voice_id: string;
  text: string;
  speed_ratio?: number;
  output_format?: "mp3" | "wav" | "ogg";
  title?: string;
}

/** Parameters for PPT-to-video task (form data). */
export interface PptToVideoParams {
  voice_id: string;
  preset?: string;
  subtitle?: string;
  accept_layout_diff?: string;
}
