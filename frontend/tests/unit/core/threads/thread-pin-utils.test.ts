import { expect, test } from "vitest";

import type { AgentThread } from "@/core/threads/types";
import {
  THREAD_PINNED_METADATA_KEY,
  isThreadPinned,
  sortPinnedThreads,
} from "@/core/threads/utils";

function thread(
  threadId: string,
  metadata: Record<string, unknown> = {},
): AgentThread {
  return { thread_id: threadId, metadata } as unknown as AgentThread;
}

test("pinned metadata key matches the backend constant", () => {
  expect(THREAD_PINNED_METADATA_KEY).toBe("deerflow_pinned");
});

test("isThreadPinned only accepts boolean true", () => {
  expect(isThreadPinned(thread("t1", { deerflow_pinned: true }))).toBe(true);
  expect(isThreadPinned(thread("t1", { deerflow_pinned: false }))).toBe(false);
  expect(isThreadPinned(thread("t1", { deerflow_pinned: "true" }))).toBe(false);
  expect(isThreadPinned(thread("t1", {}))).toBe(false);
  expect(isThreadPinned(thread("t1"))).toBe(false);
});

test("sortPinnedThreads lifts pinned threads keeping relative order", () => {
  const threads = [
    thread("a"),
    thread("b", { deerflow_pinned: true }),
    thread("c"),
    thread("d", { deerflow_pinned: true }),
  ];

  const sorted = sortPinnedThreads(threads);

  // Pinned first (stable among themselves), then the rest in original order.
  expect(sorted.map((t) => t.thread_id)).toEqual(["b", "d", "a", "c"]);
});

test("sortPinnedThreads is a no-op without pinned threads", () => {
  const threads = [thread("a"), thread("b"), thread("c")];

  expect(sortPinnedThreads(threads).map((t) => t.thread_id)).toEqual([
    "a",
    "b",
    "c",
  ]);
});

test("sortPinnedThreads does not mutate its input", () => {
  const threads = [thread("a"), thread("b", { deerflow_pinned: true })];

  sortPinnedThreads(threads);

  expect(threads.map((t) => t.thread_id)).toEqual(["a", "b"]);
});
