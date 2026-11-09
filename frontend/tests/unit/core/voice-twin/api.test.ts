/**
 * Voice Twin API layer tests.
 *
 * The hooks in `@/core/voice-twin/hooks` are thin React Query wrappers over
 * these api functions; this repo deliberately has no @testing-library/react
 * (no renderHook), so the hook-facing contracts are pinned here at the api
 * layer. Full integration coverage goes in e2e.
 *
 * (2026-10-05 review: merged the former hooks.test.ts into this file — it
 * duplicated these endpoint assertions. Its two unique cases live on here:
 * synthesizeAudio's request shape, and the no-detail error fallback.)
 */

import { beforeEach, describe, expect, test, vi } from "vitest";

import { fetch } from "@/core/api/fetcher";
import {
  deleteVoiceTwin,
  getDemoAudioURL,
  getVoicePreviewURL,
  getVoiceTwinStatus,
  listModels,
  listVoiceTwins,
  prescreenAudio,
  synthesizeAudio,
  uploadVoiceTwin,
} from "@/core/voice-twin/api";

// Mock the fetch wrapper and config before importing the api module
vi.mock("@/core/api/fetcher", () => ({
  fetch: vi.fn(),
}));

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://localhost:8001",
}));

const mockFetch = vi.mocked(fetch);

/** Cast partial mock response to Response for vitest mocks. */
function asRes(obj: Record<string, unknown>): Response {
  return obj as unknown as Response;
}

beforeEach(() => {
  mockFetch.mockReset();
});

describe("listModels", () => {
  test("calls GET /api/voice-twins/models", async () => {
    const models = [
      {
        model_type: 5,
        name: "CosyVoice2",
        description: "Default",
        languages: [0, 1],
        resource_id: "res-1",
      },
    ];
    mockFetch.mockResolvedValueOnce(
      asRes({
        ok: true,
        status: 200,
        json: () => Promise.resolve(models),
      }),
    );

    const result = await listModels();
    expect(result).toEqual(models);
    expect(mockFetch).toHaveBeenCalledWith(
      "http://localhost:8001/api/voice-twins/models",
    );
  });
});

describe("prescreenAudio", () => {
  test("sends file via FormData", async () => {
    const prescreenResponse = {
      temp_file_id: "tmp-123",
      duration: 10.5,
      format: "wav",
      needs_transcode: false,
      status: "pass" as const,
      warnings: [],
    };
    mockFetch.mockResolvedValueOnce(
      asRes({
        ok: true,
        status: 200,
        json: () => Promise.resolve(prescreenResponse),
      }),
    );

    const file = new File(["audio data"], "test.wav", { type: "audio/wav" });
    const result = await prescreenAudio(file);
    expect(result).toEqual(prescreenResponse);
    expect(mockFetch).toHaveBeenCalledWith(
      "http://localhost:8001/api/voice-twins/prescreen",
      expect.objectContaining({
        method: "POST",
        body: expect.any(FormData),
      }),
    );
  });
});

describe("uploadVoiceTwin", () => {
  test("sends POST with JSON body", async () => {
    const twin = {
      id: "twin-1",
      user_id: "user-1",
      voice_name: "My Voice",
      speaker_id: "spk-1",
      model_type: 5,
      language: 0,
      status: "uploading" as const,
    };
    mockFetch.mockResolvedValueOnce(
      asRes({
        ok: true,
        status: 200,
        json: () => Promise.resolve(twin),
      }),
    );

    const params = {
      temp_file_id: "tmp-123",
      voice_name: "My Voice",
      model_type: 5,
      language: 0,
    };
    const result = await uploadVoiceTwin(params);
    expect(result).toEqual(twin);

    const call = mockFetch.mock.calls[0]!;
    const calledUrl = call[0] as string;
    // No query string — params travel as JSON body now (see api.ts).
    expect(calledUrl).toContain("/upload");
    expect(calledUrl).not.toContain("?");

    const options = call[1] as Record<string, unknown>;
    expect(options).toEqual(
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(params),
      }),
    );
  });
});

describe("listVoiceTwins", () => {
  test("calls GET /api/voice-twins", async () => {
    const twins = [{ id: "twin-1", voice_name: "Voice A", status: "ready" }];
    mockFetch.mockResolvedValueOnce(
      asRes({
        ok: true,
        status: 200,
        json: () => Promise.resolve(twins),
      }),
    );

    const result = await listVoiceTwins();
    expect(result).toEqual(twins);
    expect(mockFetch).toHaveBeenCalledWith(
      "http://localhost:8001/api/voice-twins",
    );
  });
});

describe("getVoiceTwinStatus", () => {
  test("calls GET /api/voice-twins/{id}/status with the id URI-encoded", async () => {
    const statusResponse = {
      id: "twin-1",
      speaker_id: "spk-1",
      status: "ready",
      demo_available: true,
    };
    mockFetch.mockResolvedValueOnce(
      asRes({
        ok: true,
        status: 200,
        json: () => Promise.resolve(statusResponse),
      }),
    );

    const result = await getVoiceTwinStatus("twin/1");
    expect(result).toEqual(statusResponse);
    expect(mockFetch).toHaveBeenCalledWith(
      "http://localhost:8001/api/voice-twins/twin%2F1/status",
    );
  });
});

describe("getDemoAudioURL", () => {
  test("returns correct URL without fetching", () => {
    const url = getDemoAudioURL("twin-1");
    expect(url).toBe("http://localhost:8001/api/voice-twins/twin-1/demo");
    expect(mockFetch).not.toHaveBeenCalled();
  });
});

describe("getVoicePreviewURL", () => {
  test("builds bare URL when no text", () => {
    expect(getVoicePreviewURL("preset:146")).toBe(
      "http://localhost:8001/api/voice-twins/production/voices/preset%3A146/preview",
    );
    expect(mockFetch).not.toHaveBeenCalled();
  });

  test("appends text truncated to 100 chars", () => {
    const long = "a".repeat(200);
    const url = getVoicePreviewURL("vt-1", long);
    expect(url).toContain("?text=");
    expect(url).toContain(encodeURIComponent("a".repeat(100)));
    expect(url).not.toContain(encodeURIComponent("a".repeat(101)));
  });

  test("omits text param when blank", () => {
    expect(getVoicePreviewURL("vt-1", "   ")).toBe(
      "http://localhost:8001/api/voice-twins/production/voices/vt-1/preview",
    );
  });
});

describe("synthesizeAudio", () => {
  test("returns blob URL", async () => {
    const blob = new Blob(["audio data"], { type: "audio/mp3" });
    mockFetch.mockResolvedValueOnce(
      asRes({
        ok: true,
        status: 200,
        blob: () => Promise.resolve(blob),
      }),
    );

    const createObjectURLSpy = vi
      .spyOn(URL, "createObjectURL")
      .mockReturnValue("blob:http://localhost:3000/mock-blob-url");

    try {
      const result = await synthesizeAudio("twin-1", { text: "Hello world" });
      expect(result).toBe("blob:http://localhost:3000/mock-blob-url");
      expect(createObjectURLSpy).toHaveBeenCalledWith(blob);
    } finally {
      createObjectURLSpy.mockRestore();
    }
  });

  // Merged from the former hooks.test.ts (its one non-duplicated
  // request-shape assertion).
  test("sends POST with JSON body to /{id}/synthesize", async () => {
    const blob = new Blob(["audio"], { type: "audio/mp3" });
    mockFetch.mockResolvedValueOnce(
      asRes({
        ok: true,
        status: 200,
        blob: () => Promise.resolve(blob),
      }),
    );

    const createObjectURLSpy = vi
      .spyOn(URL, "createObjectURL")
      .mockReturnValue("blob:http://localhost:3000/fake");

    try {
      await synthesizeAudio("v1", { text: "Hello", output_format: "mp3" });

      const url = mockFetch.mock.calls[0]![0] as string;
      const options = mockFetch.mock.calls[0]![1] as Record<string, unknown>;
      expect(url).toContain("/v1/synthesize");
      expect(options.method).toBe("POST");
      expect(options.headers).toEqual(
        expect.objectContaining({ "Content-Type": "application/json" }),
      );
      const body = JSON.parse(options.body as string);
      expect(body).toEqual(
        expect.objectContaining({ text: "Hello", output_format: "mp3" }),
      );
    } finally {
      createObjectURLSpy.mockRestore();
    }
  });
});

describe("deleteVoiceTwin", () => {
  test("calls DELETE /api/voice-twins/{id}", async () => {
    mockFetch.mockResolvedValueOnce(
      asRes({
        ok: true,
        status: 204,
      }),
    );

    await deleteVoiceTwin("twin-1");
    expect(mockFetch).toHaveBeenCalledWith(
      "http://localhost:8001/api/voice-twins/twin-1",
      { method: "DELETE" },
    );
  });
});

describe("error handling", () => {
  test("throws the server's detail message on a non-ok response", async () => {
    mockFetch.mockResolvedValueOnce(
      asRes({
        ok: false,
        status: 503,
        statusText: "Service Unavailable",
        json: () =>
          Promise.resolve({ detail: "Service temporarily unavailable" }),
      }),
    );

    await expect(listModels()).rejects.toThrow(
      "Service temporarily unavailable",
    );
  });

  // Merged from the former hooks.test.ts. (Its old name said "fallback to
  // statusText" — wrong: the assertion proves the fallback is the endpoint's
  // own message when the error body carries no detail, e.g. non-JSON.)
  test("falls back to the endpoint's own message when the error body has no detail", async () => {
    mockFetch.mockResolvedValueOnce(
      asRes({
        ok: false,
        status: 500,
        statusText: "Internal Server Error",
        json: () => Promise.reject(new Error("not json")),
      }),
    );

    await expect(listModels()).rejects.toThrow("Failed to list models");
  });
});
