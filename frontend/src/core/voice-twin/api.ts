/**
 * Voice Twin API client.
 *
 * Uses the CSRF-aware fetch wrapper + getBackendBaseURL,
 * following the same pattern as budget/api.ts and channels/api.ts.
 */

import { fetch } from "@/core/api/fetcher";
import { readFastApiErrorDetail } from "@/core/api/read-fastapi-error";
import { getBackendBaseURL } from "@/core/config";

import type {
  ModelTypeInfo,
  PrescreenResponse,
  PresetVoice,
  SynthesizeParams,
  UploadVoiceTwinParams,
  VoiceTwin,
  VoiceTwinStatusResponse,
} from "./types";

// ── Helpers ──────────────────────────────────────────────────────────────

const BASE = `${getBackendBaseURL()}/api/voice-twins`;

// ── API functions ────────────────────────────────────────────────────────

/**
 * List available voice clone model types.
 */
export async function listModels(): Promise<ModelTypeInfo[]> {
  const res = await fetch(`${BASE}/models`);
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(res, "Failed to list models");
    throw new Error(detail);
  }
  return res.json();
}

/**
 * Pre-screen an audio file for suitability before uploading.
 */
export async function prescreenAudio(file: File): Promise<PrescreenResponse> {
  const form = new FormData();
  form.append("file", file);

  const res = await fetch(`${BASE}/prescreen`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(res, "Audio pre-screen failed");
    throw new Error(detail);
  }
  return res.json();
}

/**
 * Upload / create a new voice twin from a pre-screened audio file.
 * Parameters are sent as a JSON request body (validated server-side by
 * VoiceTwinUploadRequest) — not the URL query string — so voice_name and
 * model_type/language ranges are not leaked into access logs.
 */
export async function uploadVoiceTwin(
  params: UploadVoiceTwinParams,
): Promise<VoiceTwin> {
  const res = await fetch(`${BASE}/upload`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Voice twin upload failed",
    );
    throw new Error(detail);
  }
  return res.json();
}

/**
 * List available 360 TTS preset voices.
 */
export async function listPresetVoices(): Promise<PresetVoice[]> {
  const res = await fetch(`${BASE}/preset-voices`);
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Failed to list preset voices",
    );
    throw new Error(detail);
  }
  return res.json();
}

/**
 * List all voice twins for the current user.
 */
export async function listVoiceTwins(): Promise<VoiceTwin[]> {
  const res = await fetch(`${BASE}`);
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Failed to list voice twins",
    );
    throw new Error(detail);
  }
  return res.json();
}

/**
 * Get the training status of a specific voice twin.
 */
export async function getVoiceTwinStatus(
  voiceId: string,
): Promise<VoiceTwinStatusResponse> {
  const res = await fetch(`${BASE}/${encodeURIComponent(voiceId)}/status`);
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Failed to get voice twin status",
    );
    throw new Error(detail);
  }
  return res.json();
}

/**
 * Get the URL for a voice twin's demo audio.
 * Returns the URL string directly (no fetch) — suitable for <audio> src.
 */
export function getDemoAudioURL(voiceId: string): string {
  return `${BASE}/${encodeURIComponent(voiceId)}/demo`;
}

/**
 * Build the voice preview URL (GET). Returns the URL string directly —
 * pass it to `new Audio()` or an `<audio>` `src`. Optional `text` previews
 * that exact content (truncated to 100 chars); omitted → server default sample.
 */
export function getVoicePreviewURL(voiceId: string, text?: string): string {
  const base = `${BASE}/production/voices/${encodeURIComponent(voiceId)}/preview`;
  if (text?.trim()) {
    return `${base}?text=${encodeURIComponent(text.slice(0, 100))}`;
  }
  return base;
}

/**
 * Synthesize speech using a trained voice twin.
 * Returns a blob URL created via URL.createObjectURL.
 */
export async function synthesizeAudio(
  voiceId: string,
  params: SynthesizeParams,
): Promise<string> {
  const res = await fetch(`${BASE}/${encodeURIComponent(voiceId)}/synthesize`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(res, "Synthesis failed");
    throw new Error(detail);
  }
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

/**
 * Delete a voice twin by ID.
 */
export async function deleteVoiceTwin(voiceId: string): Promise<void> {
  const res = await fetch(`${BASE}/${encodeURIComponent(voiceId)}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const detail = await readFastApiErrorDetail(
      res,
      "Failed to delete voice twin",
    );
    throw new Error(detail);
  }
}
