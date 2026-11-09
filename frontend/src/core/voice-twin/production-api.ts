/**
 * Voice production API client.
 */

import { fetch } from "@/core/api/fetcher";
import { readFastApiErrorDetail } from "@/core/api/read-fastapi-error";
import { getBackendBaseURL } from "@/core/config";

import type {
  DocExtractResult,
  ProductionTask,
  PptToVideoParams,
  ScriptToAudioParams,
} from "./production-types";

// ── Helpers ──────────────────────────────────────────────────────────────

const BASE = `${getBackendBaseURL()}/api/voice-twins/production`;

// ── API functions ────────────────────────────────────────────────────────

/**
 * Extract text from a document file (preview only, no synthesis).
 */
export async function extractDocText(file: File): Promise<DocExtractResult> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${BASE}/doc-extract-text`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(res, "Failed to extract text");
    throw new Error(detail);
  }
  return res.json();
}

/**
 * Submit a script-to-audio production task.
 */
export async function submitScriptToAudio(
  params: ScriptToAudioParams,
): Promise<ProductionTask> {
  const res = await fetch(`${BASE}/script-to-audio`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(res, "Failed to submit task");
    throw new Error(detail);
  }
  return res.json();
}

/**
 * Submit a PPT-to-video production task.
 */
export async function submitPptToVideo(
  file: File,
  params: PptToVideoParams,
): Promise<ProductionTask> {
  const form = new FormData();
  form.append("file", file);
  form.append("voice_id", params.voice_id);
  if (params.preset) {
    form.append("preset", params.preset);
  }
  if (params.subtitle) {
    form.append("subtitle", params.subtitle);
  }
  form.append("accept_layout_diff", params.accept_layout_diff ?? "false");

  const res = await fetch(`${BASE}/ppt-to-video`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Failed to submit PPT task",
    );
    throw new Error(detail);
  }
  return res.json();
}

/**
 * Resume a font-gate-failed PPT-to-video task with accept_layout_diff=true.
 * Creates a new task that bypasses the font availability check.
 */
export async function resumePptToVideo(
  taskId: string,
): Promise<ProductionTask> {
  const res = await fetch(
    `${BASE}/ppt-to-video/${encodeURIComponent(taskId)}/resume`,
    { method: "POST" },
  );
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(res, "Failed to resume task");
    throw new Error(detail);
  }
  return res.json();
}

/**
 * List all production tasks for the current user.
 */
export async function listProductionTasks(): Promise<ProductionTask[]> {
  const res = await fetch(`${BASE}`);
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(res, "Failed to list tasks");
    throw new Error(detail);
  }
  return res.json();
}

/**
 * Get a production task's status.
 */
export async function getProductionTask(
  taskId: string,
): Promise<ProductionTask> {
  const res = await fetch(`${BASE}/${encodeURIComponent(taskId)}`);
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(res, "Failed to get task");
    throw new Error(detail);
  }
  return res.json();
}

/**
 * Get the download URL for a production task's output.
 */
export function getProductionDownloadURL(taskId: string): string {
  return `${BASE}/${encodeURIComponent(taskId)}/download`;
}

/**
 * Get the stream URL for a production task's output (for inline preview).
 */
export function getProductionStreamURL(taskId: string): string {
  return `${BASE}/${encodeURIComponent(taskId)}/stream`;
}

/**
 * Delete a production task.
 */
export async function deleteProductionTask(taskId: string): Promise<void> {
  const res = await fetch(`${BASE}/${encodeURIComponent(taskId)}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(res, "Failed to delete task");
    throw new Error(detail);
  }
}
