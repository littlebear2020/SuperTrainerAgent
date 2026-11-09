/**
 * csrf cookie bootstrap tests.
 *
 * The gateway only sets `csrf_token` on auth-endpoint POST responses, so a
 * browser that never logs in (no-auth / dev-bypass mode) has no cookie and
 * every state-changing Gateway POST is rejected with 403. `bootstrapCsrfToken`
 * fetches GET /api/v1/auth/csrf-token once on app mount to guarantee the
 * cookie exists. These tests pin the request shape and that network errors
 * are swallowed (a missing cookie blocks writes, not page load).
 */

import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { bootstrapCsrfToken } from "@/core/auth/csrf";

let fetchSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  fetchSpy = vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValue(new Response("{}", { status: 200 }));
});

afterEach(() => {
  fetchSpy.mockRestore();
});

test("bootstrapCsrfToken calls GET /api/v1/auth/csrf-token with credentials", async () => {
  await bootstrapCsrfToken();

  expect(fetchSpy).toHaveBeenCalledTimes(1);
  const [url, init] = fetchSpy.mock.calls[0]!;
  expect(url).toBe("/api/v1/auth/csrf-token");
  expect((init as RequestInit | undefined)?.credentials).toBe("include");
});

test("bootstrapCsrfToken swallows network errors silently", async () => {
  fetchSpy.mockRejectedValueOnce(new Error("network down"));

  await expect(bootstrapCsrfToken()).resolves.toBeUndefined();
});
