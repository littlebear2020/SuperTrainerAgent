export interface LocalPrescreenResult {
  valid: boolean;
  errors: string[];
  warnings: string[];
  duration: number;
  fileSize: number;
  format: string;
}

const MAX_SIZE = 10 * 1024 * 1024; // 10MB
const MIN_DURATION = 30; // seconds
const SUPPORTED_FORMATS = ["wav", "mp3", "m4a", "webm", "ogg", "aac"];

export async function localPrescreen(
  file: File,
): Promise<LocalPrescreenResult> {
  const errors: string[] = [];
  const warnings: string[] = [];
  const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
  const fileSize = file.size;
  let duration = 0;

  // Format check
  if (!SUPPORTED_FORMATS.includes(ext)) {
    errors.push(`Unsupported format: .${ext}`);
  }

  // Size check
  if (fileSize > MAX_SIZE) {
    errors.push(
      `File too large: ${(fileSize / 1024 / 1024).toFixed(1)}MB (max 10MB)`,
    );
  }

  // Duration check via Web Audio API
  try {
    const arrayBuffer = await file.arrayBuffer();
    const audioCtx = new AudioContext();
    const audioBuffer = await audioCtx.decodeAudioData(arrayBuffer);
    duration = audioBuffer.duration;
    await audioCtx.close();

    if (duration < MIN_DURATION) {
      errors.push(
        `Audio too short: ${Math.round(duration)}s (min ${MIN_DURATION}s)`,
      );
    }
  } catch {
    warnings.push("Could not decode audio for duration check");
  }

  return {
    valid: errors.length === 0,
    errors,
    warnings,
    duration,
    fileSize,
    format: ext,
  };
}
