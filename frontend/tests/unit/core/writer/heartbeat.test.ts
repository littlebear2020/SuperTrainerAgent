import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { createHeartbeat } from "@/core/writer/heartbeat";

// ── W16: Heartbeat (30s idle timeout replacing 120s hard timeout) ──────────
//
// The heartbeat replaces the fixed 120s wall-clock hard timeout with an
// idle timer: reset on each received chunk; fires onTimeout only after 30s
// of NO chunks. Long active generations are never cut off. §5.2D + §5.1.4.

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

// Test 1: reset() on each chunk keeps the heartbeat alive — onTimeout never
// fires as long as resets arrive within the idle window. Proves long active
// generations (chunks flowing every <30s) aren't cut off. §5.2D.
test("test_heartbeat_resets_on_chunk: resets within 30s keep heartbeat alive", () => {
  const onTimeout = vi.fn();
  const hb = createHeartbeat(30_000, onTimeout);

  // Start the heartbeat (simulating stream start)
  hb.reset();

  // Simulate chunks arriving at t=0, t=20s, t=40s — all within 30s of each other
  hb.reset(); // t=0 (first chunk)
  vi.advanceTimersByTime(20_000);
  hb.reset(); // t=20s (second chunk)
  vi.advanceTimersByTime(20_000);
  hb.reset(); // t=40s (third chunk)

  // Advance past what would have been each individual timer's fire time —
  // but since reset was called, the timer was restarted each time.
  vi.advanceTimersByTime(29_000);

  // onTimeout should NOT have fired — chunks kept the heartbeat alive
  expect(onTimeout).not.toHaveBeenCalled();

  hb.clear();
});

// Test 2: if no reset() for the full idle window, onTimeout fires. Below the
// threshold it does NOT fire. Proves a stalled stream (no chunks for 30s)
// triggers the abort. §5.2D.
test("test_heartbeat_fires_after_idle: 30s of no chunks fires onTimeout", () => {
  const onTimeout = vi.fn();
  const hb = createHeartbeat(30_000, onTimeout);

  hb.reset(); // Start timer at t=0

  // Advance to t=29s — NOT yet timed out
  vi.advanceTimersByTime(29_000);
  expect(onTimeout).not.toHaveBeenCalled();

  // Advance to t=31s — now past the 30s idle window → fires
  vi.advanceTimersByTime(2_000);
  expect(onTimeout).toHaveBeenCalledTimes(1);

  hb.clear();
});

// Test 3: clear() cancels the timer — onTimeout never fires. Simulates normal
// stream completion (clear on done). §5.2D.
test("test_heartbeat_clear_cancels: clear prevents onTimeout from firing", () => {
  const onTimeout = vi.fn();
  const hb = createHeartbeat(30_000, onTimeout);

  hb.reset(); // Start timer

  // Clear before the idle window expires (normal stream completion)
  hb.clear();

  // Advance well past the idle window
  vi.advanceTimersByTime(60_000);

  // onTimeout should NOT have fired — clear cancelled the timer
  expect(onTimeout).not.toHaveBeenCalled();
});

// Test 4: confirm the old 120s hard timeout is gone from use-from-scratch.ts.
// The only timeout mechanism should be the heartbeat. §5.1.4.
test("test_no_120s_hard_timeout: use-from-scratch.ts has no setTimeout(..., 120_000)", () => {
  const sourcePath = resolve(
    __dirname,
    "../../../../src/core/writer/use-from-scratch.ts",
  );
  const source = readFileSync(sourcePath, "utf-8");

  // The old 120s hard timeout pattern must be gone
  expect(source).not.toContain("120_000");
  expect(source).not.toContain("120000");
  // The old bare setTimeout(() => controller.abort(), ...) must be gone
  expect(source).not.toMatch(/setTimeout\(\(\)\s*=>\s*controller\.abort\(\)/);
  // The heartbeat should be wired in
  expect(source).toContain("createHeartbeat");
  expect(source).toContain("heartbeat.reset");
  expect(source).toContain("heartbeat.clear");
});
