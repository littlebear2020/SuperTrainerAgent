/**
 * Force-snapshot helper tests (Task W11 — §9.3 destructive-op undo guarantee).
 *
 * The destructive apply handlers (handleFullDraftApply, handleFillEditor,
 * handleReplaceText) wholesale-replace editor content. Before such an op,
 * the backend must have an undo point — a version snapshot of the current
 * (pre-apply) state. The force_snapshot signal tells the PUT handler to
 * use reason="destructive" (bypasses the 30s auto-throttle).
 *
 * Two pure helpers are extracted from writer-page.tsx for DOM-free testing:
 * - isDestructiveApply: maps a method name to whether it should force-snapshot
 * - consumeForceSnapshot: reads+resets the ref (so only the next save carries it)
 *
 * Mirrors hooks.test.ts / clear-document.test.ts: pure-function tests with vi.
 */

import { expect, test } from "vitest";

import {
  consumeForceSnapshot,
  isDestructiveApply,
} from "@/components/workspace/writer/writer-page";

// ── isDestructiveApply ───────────────────────────────────────────────────

test("isDestructiveApply returns true for fullDraftApply (wholesale replace)", () => {
  expect(isDestructiveApply("fullDraftApply")).toBe(true);
});

test("isDestructiveApply returns true for fillEditor (from-scratch fill)", () => {
  expect(isDestructiveApply("fillEditor")).toBe(true);
});

test("isDestructiveApply returns true for replaceText (selection polish/rewrite/expand/summarize-replace)", () => {
  expect(isDestructiveApply("replaceText")).toBe(true);
});

test("isDestructiveApply returns FALSE for insertBelow (non-destructive — original survives)", () => {
  expect(isDestructiveApply("insertBelow")).toBe(false);
});

// ── consumeForceSnapshot ─────────────────────────────────────────────────

test("consumeForceSnapshot returns true and resets ref to false when set", () => {
  const ref = { current: true };
  expect(consumeForceSnapshot(ref)).toBe(true);
  expect(ref.current).toBe(false);
});

test("consumeForceSnapshot returns false and stays false when not set", () => {
  const ref = { current: false };
  expect(consumeForceSnapshot(ref)).toBe(false);
  expect(ref.current).toBe(false);
});

test("consumeForceSnapshot resets after first consume — second call always false", () => {
  const ref = { current: true };
  expect(consumeForceSnapshot(ref)).toBe(true);
  expect(consumeForceSnapshot(ref)).toBe(false);
  expect(ref.current).toBe(false);
});
