/** Writer assets API client — AI image generation and upload. */

import { fetch } from "@/core/api/fetcher"; // CSRF + credentials auto-injected; do not use globalThis.fetch for writes
import { getBackendBaseURL } from "@/core/config";

export interface GeneratedImage {
  url: string;
  width: number;
  height: number;
  source: string;
}

const ASSETS_BASE = `${getBackendBaseURL()}/api/writer/assets`;

// ── Upload constants (B6-T6: moved from design-panel.tsx for reuse) ──────

/** Max upload size — mirrors the backend `_WRITER_IMAGE_MAX_BYTES` (10 MB). */
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;

/** MIME types the backend accepts (magic-bytes sniff; SVG never allowed). */
export const ACCEPTED_UPLOAD_MIME_TYPES: ReadonlySet<string> = new Set([
  "image/png",
  "image/jpeg",
  "image/gif",
  "image/webp",
]);

/** `accept` attribute for the file input. */
export const UPLOAD_ACCEPT_ATTR = "image/png,image/jpeg,image/gif,image/webp";

/** Client-side pre-validation for file uploads. */
export function isValidUploadFile(file: File): boolean {
  if (!ACCEPTED_UPLOAD_MIME_TYPES.has(file.type)) return false;
  if (file.size > MAX_UPLOAD_BYTES) return false;
  return true;
}

// ── Error types (W19: decouple video_gen failure-path) ─────────────────────

/**
 * Structured error from the AI image generation endpoint.
 *
 * The backend returns 503 with ``detail: {message, action, reason}`` when
 * video_gen is unconfigured or qihu_api_key is missing. This class carries
 * the structured fields so the UI can render a Chinese actionable message
 * + action + retry button instead of raw English HTTP text.
 */
export class GenerateImageError extends Error {
  readonly action?: string;
  readonly reason?: string;

  constructor(message: string, action?: string, reason?: string) {
    super(message);
    this.name = "GenerateImageError";
    this.action = action;
    this.reason = reason;
  }
}

/** Parsed error info suitable for rendering in the AI 生图 tab. */
export interface GenerateImageErrorInfo {
  message: string;
  action?: string;
  reason?: string;
}

/**
 * Parse any error thrown by ``generateImage`` into a renderable info object.
 *
 * Pure helper extracted so the message/action extraction is unit-testable
 * without rendering the React component. Handles:
 * - ``GenerateImageError`` (structured 503 from backend)
 * - Generic ``Error`` (network failure, etc.)
 * - Non-Error values (null, string, unknown)
 */
export function parseGenerateImageError(err: unknown): GenerateImageErrorInfo {
  if (err instanceof GenerateImageError) {
    return { message: err.message, action: err.action, reason: err.reason };
  }
  if (err instanceof Error) {
    return { message: err.message };
  }
  if (typeof err === "string" && err.length > 0) {
    return { message: err };
  }
  return { message: "生成失败，请重试" };
}

/** L2: AI text-to-image generation. */
export async function generateImage(params: {
  prompt: string;
  size?: string;
}): Promise<GeneratedImage> {
  const res = await fetch(`${ASSETS_BASE}/images/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      prompt: params.prompt,
      size: params.size ?? "1024x1024",
    }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    const detail = body?.detail;
    // Structured Chinese actionable error (W19): {message, action, reason}
    if (
      detail &&
      typeof detail === "object" &&
      typeof detail.message === "string"
    ) {
      throw new GenerateImageError(
        detail.message,
        typeof detail.action === "string" ? detail.action : undefined,
        typeof detail.reason === "string" ? detail.reason : undefined,
      );
    }
    // Plain string detail (rate limit, 502 provider failure, etc.)
    const msg =
      typeof detail === "string" && detail.length > 0
        ? detail
        : "生成失败，请重试";
    throw new GenerateImageError(msg);
  }
  return res.json();
}

// ── L3: Upload (paste / drop — issue ①) ────────────────────────────────────

export interface UploadedImage {
  url: string;
  width: number | null;
  height: number | null;
  source: string;
}

/**
 * Upload a pasted/dropped local image; returns a servable URL.
 *
 * Goes through the fetcher wrapper so the CSRF header + credentials travel.
 * Body is multipart FormData (no manual Content-Type — the browser sets the
 * boundary). The server sniffs the type from magic bytes and rejects SVG.
 */
export async function uploadImage(file: File): Promise<UploadedImage> {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch(`${ASSETS_BASE}/images/upload`, {
    method: "POST",
    body: fd,
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(detail.detail ?? "Upload failed");
  }
  return res.json();
}
