/**
 * TypeScript interfaces mirroring backend voice twin schemas.
 * @see backend/app/gateway/voice_twin/schemas.py
 */

/** Voice twin training status. */
export type VoiceTwinStatus = "uploading" | "training" | "ready" | "failed";

/** Audio prescreen pass/fail status. */
export type PrescreenResult = "pass" | "fail";

/** Synthesis output audio format. */
export type SynthOutputFormat = "mp3" | "wav" | "ogg";

// ---------------------------------------------------------------------------
// Prescreen
// ---------------------------------------------------------------------------

/** Response from audio pre-screening. */
export interface PrescreenResponse {
  temp_file_id: string;
  duration: number;
  format: string;
  needs_transcode: boolean;
  status: PrescreenResult;
  warnings: string[];
  error?: string;
}

// ---------------------------------------------------------------------------
// Voice Twin record
// ---------------------------------------------------------------------------

/** Full voice twin record (mirrors VoiceTwinResponse). */
export interface VoiceTwin {
  id: string;
  user_id: string;
  voice_name: string;
  speaker_id: string;
  model_type: number;
  language: number;
  status: VoiceTwinStatus;
  error_code?: number;
  error_message?: string;
  source_audio_format?: string;
  source_audio_duration?: number;
  created_at?: string; // ISO-8601 datetime
  updated_at?: string; // ISO-8601 datetime
  /** Backend list_voices fills this from disk (demos/{id}.wav); absent on older payloads. */
  demo_available?: boolean;
}

// ---------------------------------------------------------------------------
// Voice Twin training status
// ---------------------------------------------------------------------------

/** Training status response (mirrors VoiceTwinStatusResponse). */
export interface VoiceTwinStatusResponse {
  id: string;
  speaker_id: string;
  status: VoiceTwinStatus;
  error_code?: number;
  error_message?: string;
  demo_available: boolean;
}

// ---------------------------------------------------------------------------
// Model type info
// ---------------------------------------------------------------------------

/** Information about an available voice clone model type. */
export interface ModelTypeInfo {
  model_type: number;
  name: string;
  description: string;
  languages: number[];
  resource_id: string;
}

// ---------------------------------------------------------------------------
// Request parameter types
// ---------------------------------------------------------------------------

/** Parameters for uploading / creating a voice twin. */
export interface UploadVoiceTwinParams {
  temp_file_id: string;
  voice_name: string;
  model_type?: number; // 1-5, default 5
  language?: number; // 0-7, default 0
  speaker_id?: string; // Pre-paid console-created ID (S_xxx, ICL_xxx, etc.)
  extra_params?: Record<string, unknown> | null; // Optional provider-specific params
}

/** Parameters for synthesizing speech with a cloned voice. */
export interface SynthesizeParams {
  text: string; // 1-5000 chars
  output_format?: SynthOutputFormat; // default "mp3"
  speed_ratio?: number; // 0.5-2.0, default 1.0
}

// ---------------------------------------------------------------------------
// Preset voices（共享音色目录，对齐后端 CatalogVoice）
// ---------------------------------------------------------------------------

/** 目录音色的提供方。 */
export type VoiceProvider = "edge-tts" | "qihu" | "doubao";

/**
 * 共享音色目录中的一条预设音色（镜像后端 CatalogVoice）。
 * 注意：id 是带前缀的完整 voice_id（"edge:..." / "preset:..." / "doubao:..."），
 * 可直接传给 preview / synthesize，无需再拼前缀。
 */
export interface PresetVoice {
  id: string;
  name: string;
  gender: "male" | "female";
  locale: string; // "zh-CN" / "en-US" / ...
  provider: VoiceProvider;
  description: string;
  scenario: string[]; // 适用场景标签
  tone: string[]; // 音色特质标签
  preview_url?: string | null;
}

// ── 筛选枚举常量（纯中文，与后端目录标签口径一致；后端不强校验，保持扩展性）──

/** provider → 中文展示标签。 */
export const VOICE_PROVIDER_LABELS: Record<VoiceProvider, string> = {
  "edge-tts": "Edge TTS（免费）",
  qihu: "360 声音（高质量）",
  doubao: "豆包 seed-tts-2.0",
};
