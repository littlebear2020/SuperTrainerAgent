/**
 * Run-creation retry policy in the API client.
 *
 * Creating a run is not idempotent: when the gateway accepted the POST but
 * the response was lost (network blip, gateway restart), the SDK's default
 * retry would create a second run — duplicate token spend and side effects.
 * The client therefore routes ``runs.stream`` through a ``RunsClient`` with
 * ``maxRetries: 0``, while recovery GETs keep the normal retry budget.
 *
 * These tests exercise the real SDK transport (no SDK module mock) with a
 * stubbed global ``fetch`` and assert the creation POST is attempted exactly
 * once, for both an HTTP 5xx and a raw network failure.
 */

import { afterEach, describe, expect, test, vi } from "vitest";

vi.mock("../../../../src/core/config", () => ({
  getLangGraphBaseURL: () => "http://localhost:2024",
}));

async function freshClient() {
  // Reset the module registry so the `_clients` singleton Map inside
  // api-client.ts does not hand back a client built by an earlier test.
  vi.resetModules();
  const { getAPIClient } = await import("@/core/api/api-client");
  return getAPIClient();
}

/** Consume a run stream to completion (or failure). */
async function consume(client: Awaited<ReturnType<typeof freshClient>>) {
  for await (const entry of client.runs.stream("thread-1", "lead_agent", {
    input: { messages: [] },
  })) {
    void entry;
  }
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("run creation retry policy", () => {
  test("does not retry run creation after an ambiguous HTTP 500", async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Response(JSON.stringify({ detail: "response lost" }), {
          status: 500,
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const client = await freshClient();

    await expect(consume(client)).rejects.toThrow("HTTP 500");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[1]?.method).toBe("POST");
  });

  test("does not retry run creation after a network failure", async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) => {
        throw new TypeError("connection interrupted");
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const client = await freshClient();

    await expect(consume(client)).rejects.toThrow("connection interrupted");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[1]?.method).toBe("POST");
  });
});
