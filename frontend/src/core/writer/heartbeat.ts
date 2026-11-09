/**
 * Idle-heartbeat timer for from-scratch streaming (§5.2D).
 *
 * Replaces the old fixed 120s wall-clock hard timeout (`setTimeout(() =>
 * controller.abort(), 120_000)`) that cut off legitimately long generations
 * mid-flight. The heartbeat is an **idle** timer: it resets on each received
 * SSE chunk and fires `onTimeout` only after `idleMs` of **no chunks**.
 * Long generations that keep producing chunks are never cut off.
 *
 * Usage:
 * ```ts
 * const hb = createHeartbeat(30_000, () => controller.abort());
 * hb.reset();               // start the idle timer (stream begins)
 * // ...on each chunk: hb.reset()
 * hb.clear();               // normal stream completion — cancel timer
 * ```
 *
 * Extracted as a pure helper so it is unit-testable with vitest fake timers
 * without React/DOM. Uses `setTimeout`/`clearTimeout` internally.
 */

export interface Heartbeat {
  /** Reset the idle timer — call on each received chunk (or stream start). */
  reset(this: void): void;
  /** Cancel the timer — call on normal stream completion. */
  clear(this: void): void;
}

/**
 * Create an idle-heartbeat timer.
 *
 * @param idleMs   Idle window in ms (30_000 for from-scratch streaming).
 * @param onTimeout Called when no `reset()` occurs within `idleMs`.
 * @returns `{ reset, clear }` — reset per chunk, clear on completion.
 */
export function createHeartbeat(
  idleMs: number,
  onTimeout: () => void,
): Heartbeat {
  let timer: ReturnType<typeof setTimeout> | null = null;

  function clear(): void {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  }

  function reset(): void {
    clear();
    timer = setTimeout(() => {
      timer = null;
      onTimeout();
    }, idleMs);
  }

  return { reset, clear };
}
