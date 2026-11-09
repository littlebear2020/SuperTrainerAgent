/**
 * Writer share API + share-dialog helper tests.
 *
 * Covers Task W25 (§7):
 * - createDocShare no longer sends `permission` in the request body
 * - classifyShareLoadResult correctly distinguishes "none" / "loaded" / "failed"
 *
 * The bug this guards: share-dialog's `.catch(() => setShareData(null))` treated
 * load failures the same as "no share exists" — the user saw the create-form
 * instead of an error + retry. classifyShareLoadResult makes the tri-state
 * testable.
 */

import { expect, test, vi, beforeEach } from "vitest";

vi.mock("@/core/api/fetcher", () => ({
  fetch: vi.fn(),
}));

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://localhost:8001",
}));

import { fetch } from "@/core/api/fetcher";
import { createDocShare, classifyShareLoadResult } from "@/core/writer/api";

const mockFetch = vi.mocked(fetch);

/** Cast a partial mock to a Response (vitest mocks don't need a real Response). */
function asRes(obj: Record<string, unknown>): Response {
  return obj as unknown as Response;
}

beforeEach(() => {
  mockFetch.mockReset();
});

// ── createDocShare: body must NOT include permission ─────────────────────

test("createDocShare does NOT send permission in the request body", async () => {
  mockFetch.mockResolvedValueOnce(
    asRes({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve({
          id: "shr_abc",
          doc_id: "doc_123",
          owner_id: "user1",
          share_token: "tok_xyz",
          expires_at: null,
          created_at: null,
        }),
    }),
  );

  await createDocShare("doc_123", { expires_days: 7 });

  const options = mockFetch.mock.calls[0]![1] as Record<string, unknown>;
  const body = JSON.parse(options.body as string);
  expect(body).not.toHaveProperty("permission");
  expect(body).toEqual({ expires_days: 7 });
});

test("createDocShare sends empty object when no opts provided", async () => {
  mockFetch.mockResolvedValueOnce(
    asRes({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve({
          id: "shr_abc",
          doc_id: "doc_123",
          owner_id: "user1",
          share_token: "tok_xyz",
          expires_at: null,
          created_at: null,
        }),
    }),
  );

  await createDocShare("doc_123");

  const options = mockFetch.mock.calls[0]![1] as Record<string, unknown>;
  const body = JSON.parse(options.body as string);
  expect(body).not.toHaveProperty("permission");
  expect(body).toEqual({});
});

// ── classifyShareLoadResult: tri-state classification ────────────────────

test("classifyShareLoadResult returns 'none' for null", () => {
  const result = classifyShareLoadResult(null);
  expect(result.state).toBe("none");
});

test("classifyShareLoadResult returns 'loaded' for a DocShare object", () => {
  const share = {
    id: "shr_abc",
    doc_id: "doc_123",
    owner_id: "user1",
    share_token: "tok_xyz",
    expires_at: null,
    created_at: null,
  };
  const result = classifyShareLoadResult(share);
  expect(result.state).toBe("loaded");
  if (result.state === "loaded") {
    expect(result.data.share_token).toBe("tok_xyz");
  }
});

test("classifyShareLoadResult returns 'failed' for an Error", () => {
  const err = new Error("Network error");
  const result = classifyShareLoadResult(err);
  expect(result.state).toBe("failed");
  if (result.state === "failed") {
    expect(result.error).toBe("Network error");
  }
});

test("classifyShareLoadResult returns 'failed' for non-Error thrown value", () => {
  const result = classifyShareLoadResult("some string error");
  expect(result.state).toBe("failed");
  if (result.state === "failed") {
    expect(result.error).toBe("some string error");
  }
});
