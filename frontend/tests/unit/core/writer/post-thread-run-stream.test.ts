/**
 * use-from-scratch CSRF attachment tests.
 *
 * Bug: `sendMessage` POSTed `/api/threads/{tid}/runs/stream` via bare
 * `globalThis.fetch`, bypassing the fetcher wrapper → no `X-CSRF-Token` → 403.
 * The streaming fetch is extracted into `postThreadRunStream` (mirrors how
 * `parseScratchSSE` / `createWriterDocMutationOptions` were extracted for
 * DOM-free testing) so the cookie→header contract is unit-testable in Node.
 */

import { afterEach, beforeEach, expect, test, vi } from "vitest";

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "",
}));

import { postThreadRunStream } from "@/core/writer/use-from-scratch";

let fetchSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  vi.stubGlobal("document", { cookie: "csrf_token=scratch-token" });
  fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response("event: end\ndata: {}\n\n", {
      status: 200,
      headers: { "content-type": "text/event-stream" },
    }),
  );
});

afterEach(() => {
  fetchSpy.mockRestore();
  vi.unstubAllGlobals();
});

test("postThreadRunStream POST carries X-CSRF-Token minted from the csrf cookie", async () => {
  await postThreadRunStream("tid-1", { assistant_id: "co-writer" });

  expect(fetchSpy).toHaveBeenCalledTimes(1);
  const [url, init] = fetchSpy.mock.calls[0]!;
  expect(url).toBe("/api/threads/tid-1/runs/stream");
  const options = init as RequestInit;
  expect(options.method).toBe("POST");
  expect(new Headers(options.headers).get("X-CSRF-Token")).toBe(
    "scratch-token",
  );
  expect(options.credentials).toBe("include");
});

test("postThreadRunStream forwards the payload as JSON body and signal", async () => {
  const ac = new AbortController();
  await postThreadRunStream(
    "tid-2",
    { assistant_id: "co-writer", input: { messages: [] } },
    ac.signal,
  );

  const options = fetchSpy.mock.calls[0]![1] as RequestInit;
  expect(JSON.parse(options.body as string)).toEqual({
    assistant_id: "co-writer",
    input: { messages: [] },
  });
  expect(options.signal).toBe(ac.signal);
});
