/**
 * Writer create-doc hook behavior tests.
 *
 * The bug: `useCreateWriterDoc` had no onError, so a failed create POST was
 * swallowed by React Query — no toast, no navigation, "stuck" UI. To make the
 * new error-surfacing behavior unit-testable without a DOM (Vitest runs in
 * Node; @testing-library/react is not installed), the mutation OPTIONS are
 * extracted into `createWriterDocMutationOptions(qc)`, whose callbacks are
 * plain functions we can invoke directly.
 *
 * These tests pin: onError → toast.error with the backend detail, mutationFn
 * rejects so onError actually receives the detail, and onSuccess invalidates
 * the writer docs cache.
 */

import { QueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { beforeEach, expect, test, vi } from "vitest";

vi.mock("@/core/api/fetcher", () => ({
  fetch: vi.fn(),
}));

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://localhost:8001",
}));

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}));

import { fetch } from "@/core/api/fetcher";
import {
  contentTaskRefetchInterval,
  createWriterDocMutationOptions,
  shouldWarnBeforeUnload,
  updateWriterDocMutationOptions,
} from "@/core/writer/hooks";

const mockFetch = vi.mocked(fetch);
const mockToastError = vi.mocked(toast.error);

/** Cast a partial mock to a Response. */
function asRes(obj: Record<string, unknown>): Response {
  return obj as unknown as Response;
}

beforeEach(() => {
  mockFetch.mockReset();
  mockToastError.mockReset();
});

test("onError surfaces the backend detail via toast.error", () => {
  const options = createWriterDocMutationOptions(new QueryClient());

  options.onError(
    new Error("CSRF token missing. Include X-CSRF-Token header."),
  );

  expect(mockToastError).toHaveBeenCalledTimes(1);
  const msg = mockToastError.mock.calls[0]![0] as string;
  expect(msg).toContain("创建失败");
  expect(msg).toContain("CSRF token missing");
});

test("mutationFn rejects with backend detail so onError receives it", async () => {
  mockFetch.mockResolvedValueOnce(
    asRes({
      ok: false,
      status: 503,
      json: () => Promise.resolve({ detail: "Writer service not available" }),
    }),
  );

  const options = createWriterDocMutationOptions(new QueryClient());
  await expect(
    options.mutationFn({ title: "t", content_json: "", doc_type: "general" }),
  ).rejects.toThrow("Writer service not available");
});

test("onError falls back gracefully when error has no message", () => {
  const options = createWriterDocMutationOptions(new QueryClient());

  // Non-Error throw (e.g. a string) must not crash the toast callback.
  options.onError({} as Error);

  expect(mockToastError).toHaveBeenCalledTimes(1);
});

test("onSuccess invalidates the writer docs query cache", () => {
  const qc = new QueryClient();
  const spy = vi.spyOn(qc, "invalidateQueries");
  const options = createWriterDocMutationOptions(qc);

  options.onSuccess();

  expect(spy).toHaveBeenCalledWith({ queryKey: ["writerDocs"] });
});

// ── updateWriterDocMutationOptions (save reliability — W5) ──────────────

test("updateWriterDocMutationOptions onError surfaces the backend detail via toast.error", () => {
  const options = updateWriterDocMutationOptions(new QueryClient(), "doc-123");

  options.onError(
    new Error("CSRF token missing. Include X-CSRF-Token header."),
  );

  expect(mockToastError).toHaveBeenCalledTimes(1);
  const msg = mockToastError.mock.calls[0]![0] as string;
  expect(msg).toContain("保存失败");
  expect(msg).toContain("CSRF token missing");
});

test("updateWriterDocMutationOptions onError falls back gracefully when error has no message", () => {
  const options = updateWriterDocMutationOptions(new QueryClient(), "doc-123");

  // Non-Error throw (e.g. a string) must not crash the toast callback.
  options.onError({} as Error);

  expect(mockToastError).toHaveBeenCalledTimes(1);
  const msg = mockToastError.mock.calls[0]![0] as string;
  expect(msg).toContain("保存失败");
});

test("updateWriterDocMutationOptions onSuccess 直接把 PUT 返回值写入详情缓存（2026-10-06 性能批：省全文 GET）", () => {
  const qc = new QueryClient();
  const spy = vi.spyOn(qc, "setQueryData");
  const invalidateSpy = vi.spyOn(qc, "invalidateQueries");
  const options = updateWriterDocMutationOptions(qc, "doc-456");
  const saved = { id: "doc-456", title: "已保存" } as never;

  options.onSuccess(saved);

  expect(spy).toHaveBeenCalledWith(["writerDocs", "doc-456"], saved);
  expect(invalidateSpy).not.toHaveBeenCalledWith({
    queryKey: ["writerDocs", "doc-456"],
  });
});

test("updateWriterDocMutationOptions onError does NOT add retry to mutation options (avoid double-saves)", () => {
  const options = updateWriterDocMutationOptions(new QueryClient(), "doc-789");

  // The mutation must not auto-retry — a manual retry button handles that.
  expect((options as unknown as { retry?: unknown }).retry).toBeUndefined();
});

// ── shouldWarnBeforeUnload (pure helper — W5) ───────────────────────────

test("shouldWarnBeforeUnload returns true when dirty and not saving (debounce pending)", () => {
  expect(shouldWarnBeforeUnload(true, false)).toBe(true);
});

test("shouldWarnBeforeUnload returns true when saving (in-flight save)", () => {
  expect(shouldWarnBeforeUnload(false, true)).toBe(true);
});

test("shouldWarnBeforeUnload returns true when dirty and saving", () => {
  expect(shouldWarnBeforeUnload(true, true)).toBe(true);
});

test("shouldWarnBeforeUnload returns false when clean and not saving", () => {
  expect(shouldWarnBeforeUnload(false, false)).toBe(false);
});

// ── B1-T2: cancelled terminal status stops polling ──────────────────────

test("B1-T2: contentTaskRefetchInterval returns false for cancelled status", () => {
  // The backend terminal statuses include "cancelled" (TERMINAL_STATUSES in
  // task_framework/schemas.py). The old frontend only checked ready/failed ->
  // cancelled tasks polled forever every 2s. Now it must also stop.
  expect(
    contentTaskRefetchInterval({ state: { data: { status: "cancelled" } } }),
  ).toBe(false);
  expect(
    contentTaskRefetchInterval({ state: { data: { status: "ready" } } }),
  ).toBe(false);
  expect(
    contentTaskRefetchInterval({ state: { data: { status: "failed" } } }),
  ).toBe(false);

  // Non-terminal status continues polling
  expect(
    contentTaskRefetchInterval({ state: { data: { status: "running" } } }),
  ).toBe(2000);
  expect(
    contentTaskRefetchInterval({ state: { data: { status: "pending" } } }),
  ).toBe(2000);

  // No data yet -> keep polling
  expect(contentTaskRefetchInterval({ state: { data: undefined } })).toBe(2000);
});
