import assert from "node:assert/strict";
import test, { type TestContext } from "node:test";

import { extractImageFromResponse } from "./qihu360.ts";

function mockFetch(
  t: TestContext,
  impl: (url: string, opts?: RequestInit) => Promise<Response>,
): void {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = ((input: URL | RequestInfo, init?: RequestInit) =>
    impl(typeof input === "string" ? input : String(input), init)) as typeof fetch;
  t.after(() => {
    globalThis.fetch = originalFetch;
  });
}

test("360 response extraction decodes a base64 image when b64_json is present", async () => {
  // Single valid base64 byte sequence (0x01 0x02 0x03) — no network needed.
  const image = await extractImageFromResponse({
    data: [{ b64_json: "AQID" }],
  });
  assert.deepEqual([...image], [1, 2, 3]);
});

test("360 response extraction downloads a returned image URL", async (t) => {
  mockFetch(t, async () =>
    new Response(Uint8Array.from([10, 20, 30]), {
      status: 200,
      headers: { "Content-Type": "image/png" },
    }),
  );

  const image = await extractImageFromResponse({
    data: [{ url: "https://cdn.example.com/img.png" }],
  });
  assert.deepEqual([...image], [10, 20, 30]);
});

test("360 download error surfaces the URL and fetch cause code", async (t) => {
  mockFetch(t, async () => {
    throw Object.assign(new Error("connect timeout"), {
      cause: { code: "UND_ERR_CONNECT_TIMEOUT" },
    });
  });

  await assert.rejects(
    () =>
      extractImageFromResponse({
        data: [{ url: "https://cdn.example.com/fail.png" }],
      }),
    (err: Error) => {
      assert.match(err.message, /Download failed for https:\/\/cdn\.example\.com\/fail\.png/);
      assert.match(err.message, /UND_ERR_CONNECT_TIMEOUT/);
      return true;
    },
  );
});

test("360 non-ok HTTP response surfaces the URL and status code", async (t) => {
  mockFetch(t, async () =>
    new Response("Forbidden", {
      status: 403,
      statusText: "Forbidden",
    }),
  );

  await assert.rejects(
    () =>
      extractImageFromResponse({
        data: [{ url: "https://cdn.example.com/forbidden.png" }],
      }),
    (err: Error) => {
      assert.match(err.message, /https:\/\/cdn\.example\.com\/forbidden\.png/);
      assert.match(err.message, /HTTP 403/);
      return true;
    },
  );
});

test("360 body-download failure (post-connect stream reset) surfaces URL + cause", async (t) => {
  // fetch resolves (connect ok, HTTP 200) but the body stream errors mid-read.
  mockFetch(t, async () =>
    ({
      ok: true,
      status: 200,
      statusText: "OK",
      arrayBuffer: () => Promise.reject(new Error("stream reset mid-body")),
    }) as unknown as Response,
  );

  await assert.rejects(
    () =>
      extractImageFromResponse({
        data: [{ url: "https://cdn.example.com/body-reset.png" }],
      }),
    (err: Error) => {
      assert.match(err.message, /Download failed for https:\/\/cdn\.example\.com\/body-reset\.png/);
      assert.match(err.message, /stream reset mid-body/);
      return true;
    },
  );
});

test("360 download wires an abort signal so a hung fetch can time out", async (t) => {
  let capturedOpts: RequestInit | undefined;
  mockFetch(t, async (_url, opts) => {
    capturedOpts = opts;
    return new Response(Uint8Array.from([1, 2, 3]), {
      status: 200,
      headers: { "Content-Type": "image/png" },
    });
  });

  await extractImageFromResponse({
    data: [{ url: "https://cdn.example.com/with-signal.png" }],
  });

  assert.ok(capturedOpts?.signal, "fetch must be called with an abort signal for timeout");
  assert.equal(
    typeof (capturedOpts!.signal as AbortSignal)?.addEventListener,
    "function",
    "signal must be a real AbortSignal",
  );
});

test("360 download surfaces a recognizable cause when fetch times out", async (t) => {
  // undici raises a HeadersTimeoutError on timeout; our cause extraction should
  // fall back to error.name so the timeout is identifiable in the message.
  mockFetch(t, async () => {
    const err = new Error("headers timeout");
    err.name = "HeadersTimeoutError";
    throw err;
  });

  await assert.rejects(
    () =>
      extractImageFromResponse({
        data: [{ url: "https://cdn.example.com/slow.png" }],
      }),
    (err: Error) => {
      assert.match(err.message, /Download failed for https:\/\/cdn\.example\.com\/slow\.png/);
      assert.match(err.message, /HeadersTimeoutError/);
      return true;
    },
  );
});

test("360 response with missing or empty data throws a clear no-image error", async () => {
  await assert.rejects(
    () => extractImageFromResponse({} as never),
    /No image in 360 API response/,
  );
  await assert.rejects(
    () => extractImageFromResponse({ data: [] }),
    /No image in 360 API response/,
  );
});
