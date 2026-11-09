import { expect, test, vi, beforeEach } from "vitest";

// Mock AudioContext — use a regular function so vitest can call it with `new`
const mockDecodeAudioData = vi.fn();
const mockClose = vi.fn();

class MockAudioContext {
  decodeAudioData = mockDecodeAudioData;
  close = mockClose;
}

vi.stubGlobal("AudioContext", MockAudioContext);

import { localPrescreen } from "@/core/voice-twin/prescreen";

/** Create a minimal File-like object for testing. */
function makeFile(name: string, size: number): File {
  const file = new File([new ArrayBuffer(size)], name);
  // Override size since File constructor may not respect empty ArrayBuffer size
  Object.defineProperty(file, "size", { value: size });
  return file;
}

beforeEach(() => {
  mockDecodeAudioData.mockReset();
  mockClose.mockReset();
  mockDecodeAudioData.mockResolvedValue({ duration: 60 });
  mockClose.mockResolvedValue(undefined);
});

// ── valid file ─────────────────────────────────────────────────────────────

test("passes for valid wav file", async () => {
  const file = makeFile("voice.wav", 1024 * 1024); // 1MB
  const result = await localPrescreen(file);

  expect(result.valid).toBe(true);
  expect(result.errors).toEqual([]);
});

// ── unsupported format ─────────────────────────────────────────────────────

test("rejects unsupported format", async () => {
  const file = makeFile("notes.txt", 1024);
  const result = await localPrescreen(file);

  expect(result.valid).toBe(false);
  expect(result.errors).toContain("Unsupported format: .txt");
});

// ── oversized file ─────────────────────────────────────────────────────────

test("rejects oversized file", async () => {
  const file = makeFile("big.wav", 11 * 1024 * 1024); // 11MB
  const result = await localPrescreen(file);

  expect(result.valid).toBe(false);
  expect(result.errors.some((e) => e.includes("File too large"))).toBe(true);
});

// ── short audio ────────────────────────────────────────────────────────────

test("rejects short audio", async () => {
  mockDecodeAudioData.mockResolvedValue({ duration: 10 });
  const file = makeFile("short.wav", 1024);
  const result = await localPrescreen(file);

  expect(result.valid).toBe(false);
  expect(result.errors.some((e) => e.includes("too short"))).toBe(true);
});

// ── decode failure ─────────────────────────────────────────────────────────

test("warns on decode failure", async () => {
  mockDecodeAudioData.mockRejectedValue(new Error("decode failed"));
  const file = makeFile("corrupt.wav", 1024);
  const result = await localPrescreen(file);

  expect(result.warnings).toContain(
    "Could not decode audio for duration check",
  );
});

// ── result fields ──────────────────────────────────────────────────────────

test("returns duration and format", async () => {
  const file = makeFile("recording.mp3", 2048);
  const result = await localPrescreen(file);

  expect(result.duration).toBe(60);
  expect(result.format).toBe("mp3");
  expect(result.fileSize).toBe(2048);
});
