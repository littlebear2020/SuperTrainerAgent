/**
 * streamAssist AbortSignal forwarding (P2-12).
 *
 * streamAssist used to ignore any AbortController, so cancel/timeout could
 * only take effect between chunks — a hung stream blocked the for-await
 * forever. These tests lock the contract that a passed signal reaches fetch.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/core/config", () => ({ getBackendBaseURL: () => "" }));

import { streamAssist } from "@/core/writer/api";

function encode(str: string): Uint8Array {
  return new TextEncoder().encode(str);
}

function mockDoneResponse(): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(encode("data: [DONE]\n\n"));
      controller.close();
    },
  });
  return new Response(stream, { status: 200 });
}

let fetchSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  vi.stubGlobal("document", { cookie: "csrf_token=t" });
});

afterEach(() => {
  fetchSpy?.mockRestore();
  vi.unstubAllGlobals();
});

// Minimal valid AssistRequest — op + text are the only required fields.
const REQ = { op: "polish", text: "x" } as Parameters<typeof streamAssist>[0];

describe("streamAssist (P2-12 AbortSignal)", () => {
  it("forwards the AbortSignal to fetch", async () => {
    const ctrl = new AbortController();
    fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(mockDoneResponse());
    // drain the generator (yields nothing; returns on [DONE])
    for await (const _ of streamAssist(REQ, ctrl.signal)) {
      void _;
    }
    expect(fetchSpy).toHaveBeenCalledOnce();
    expect(fetchSpy.mock.calls?.[0]?.[1]).toMatchObject({
      signal: ctrl.signal,
    });
  });

  it("works without a signal (backward compatible)", async () => {
    fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(mockDoneResponse());
    for await (const _ of streamAssist(REQ)) {
      void _;
    }
    expect(fetchSpy.mock.calls?.[0]?.[1]).not.toHaveProperty("signal");
  });
});

// ── B1-T7: \n\n frame splitting (was \n) ───────────────────────────────

describe("streamAssist B1-T7 (\\n\\n frame splitting)", () => {
  function mockChunkedResponse(chunks: string[]): Response {
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of chunks) {
          controller.enqueue(encode(chunk));
        }
        controller.close();
      },
    });
    return new Response(stream, { status: 200 });
  }

  beforeEach(() => {
    vi.stubGlobal("document", { cookie: "csrf_token=t" });
  });

  afterEach(() => {
    fetchSpy?.mockRestore();
    vi.unstubAllGlobals();
  });

  it("parses frames split across chunks correctly", async () => {
    // Simulate a frame split across two TCP chunks:
    // chunk1: "data: {\"text\":\"hel"  (partial)
    // chunk2: "lo\"}\n\ndata: [DONE]\n\n"
    fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        mockChunkedResponse(['data: {"text":"hel', 'lo"}\n\ndata: [DONE]\n\n']),
      );

    const chunks: string[] = [];
    for await (const chunk of streamAssist(REQ)) {
      chunks.push(chunk);
    }
    expect(chunks).toEqual(["hello"]);
  });

  it("handles payload with embedded \\n (multi-line text)", async () => {
    // A JSON payload with \n in the text value should NOT break parsing.
    // The \n inside JSON is escaped as \\n in the wire format, so it won't
    // be split by \n\n. But verify the split doesn't break on \n within data: lines.
    fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        mockChunkedResponse([
          'data: {"text":"line1\\nline2"}\n\ndata: [DONE]\n\n',
        ]),
      );

    const chunks: string[] = [];
    for await (const chunk of streamAssist(REQ)) {
      chunks.push(chunk);
    }
    expect(chunks).toEqual(["line1\nline2"]);
  });
});

// ── C2 闭环: type:error frames must throw, not be swallowed ──────────
// Backend sends {"type":"error","content":"AI 响应超时,请重试"} on idle
// timeout (and skips [DONE]); callers' existing catch shows err.message.

describe("streamAssist C2 (error frame)", () => {
  function mockFramesResponse(frames: string[]): Response {
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const f of frames) {
          controller.enqueue(encode(f));
        }
        controller.close();
      },
    });
    return new Response(stream, { status: 200 });
  }

  beforeEach(() => {
    vi.stubGlobal("document", { cookie: "csrf_token=t" });
  });

  afterEach(() => {
    fetchSpy?.mockRestore();
    vi.unstubAllGlobals();
  });

  it("throws with the backend content when a type:error frame arrives mid-stream", async () => {
    // Realistic timeout shape: partial text, then error frame, no [DONE].
    fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        mockFramesResponse([
          'data: {"text":"partial","type":"text","content":"partial"}\n\n',
          'data: {"type":"error","content":"AI 响应超时,请重试"}\n\n',
        ]),
      );

    const chunks: string[] = [];
    await expect(async () => {
      for await (const chunk of streamAssist(REQ)) {
        chunks.push(chunk);
      }
    }).rejects.toThrow("AI 响应超时,请重试");
    // Text streamed before the error frame was still delivered
    expect(chunks).toEqual(["partial"]);
  });

  it("falls back to the neutral default when error-frame content is missing, empty, or whitespace", async () => {
    // ?? only guards null/undefined — "" would otherwise surface as an empty
    // error message (review follow-up: explicit emptiness check)
    fetchSpy = vi.spyOn(globalThis, "fetch");
    for (const frame of [
      'data: {"type":"error"}\n\n',
      'data: {"type":"error","content":""}\n\n',
      'data: {"type":"error","content":"   "}\n\n',
    ]) {
      fetchSpy.mockResolvedValue(mockFramesResponse([frame]));
      await expect(async () => {
        for await (const _ of streamAssist(REQ)) {
          void _;
        }
      }).rejects.toThrow("AI 服务异常,请重试");
    }
  });
});
