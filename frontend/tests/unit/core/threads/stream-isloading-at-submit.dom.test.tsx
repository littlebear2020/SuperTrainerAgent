/**
 * Regression guard for the REAL @langchain/langgraph-sdk `useStream` hook:
 * `thread.isLoading` must go true AT SUBMIT TIME — not only after the first
 * SSE event/frame arrives.
 *
 * Why this matters (2026-08-06 thinking-phase investigation):
 *   The chat stop button and the StreamingIndicator are both driven by
 *   `thread.isLoading` (see the two [thread_id]/page.tsx `status` derivations
 *   and message-list.tsx line ~406). With thinking models (doubao / kimi) the
 *   backend emits NO SSE frames for 20s+ while the model thinks. If isLoading
 *   only flipped after the first frame, the user would see no "thinking"
 *   indicator and no stop button for the whole thinking window.
 *
 *   SDK 1.6.0 (the version pinned in pnpm-lock.yaml) sets isLoading=true in
 *   StreamManager.enqueue BEFORE any frame is consumed, so the current code is
 *   correct. This test locks that behavior in: if the SDK is ever swapped for a
 *   version that delays isLoading until the first frame, this test fails and
 *   surfaces the regression before it ships.
 *
 * Method: drive the real hook with a fake client whose runs.stream returns a
 * controllable async iterable. Submit, then assert isLoading BEFORE any frame
 * is pushed (both for an existing thread and for new-thread creation).
 */
import { useStream } from "@langchain/langgraph-sdk/react";
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

type StreamReturn = ReturnType<typeof useStream<Record<string, unknown>>>;

type StreamEvent = { event: string; data: unknown };

function createControllableStream() {
  const buffer: StreamEvent[] = [];
  let notify: (() => void) | null = null;
  let ended = false;
  const iterable: AsyncIterable<StreamEvent> = {
    [Symbol.asyncIterator]() {
      return {
        async next(): Promise<IteratorResult<StreamEvent>> {
          while (buffer.length === 0 && !ended) {
            await new Promise<void>((res) => {
              notify = res;
            });
            notify = null;
          }
          if (buffer.length > 0) {
            return { value: buffer.shift()!, done: false };
          }
          return { value: undefined, done: true };
        },
      };
    },
  };
  return {
    iterable,
    push(ev: StreamEvent) {
      buffer.push(ev);
      notify?.();
    },
    end() {
      ended = true;
      notify?.();
    },
  };
}

function makeFakeClient(iterable: AsyncIterable<StreamEvent>) {
  return {
    threads: {
      create: async () => ({ thread_id: "t-created" }),
      getHistory: async () => [],
      getState: async () => ({ checkpoint: null, values: {} }),
      update: async () => ({}),
      updateState: async () => ({}),
    },
    runs: {
      stream: async () => iterable,
      cancel: async () => ({}),
      joinStream: async () => iterable,
    },
  };
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  act(() => {
    root = createRoot(container);
  });
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
});

test("SDK useStream: isLoading timing relative to first SSE frame", async () => {
  // Fake timers make the settle waits deterministic (the only real-timer use
  // was flushing the SDK's internal scheduling with 10/20ms setTimeout waits).
  vi.useFakeTimers();
  try {
    const ctrl = createControllableStream();
    const fakeClient = makeFakeClient(ctrl.iterable);

    const cap: { current: StreamReturn | null } = { current: null };
    let submitFn: (() => Promise<void>) | null = null;

    function Probe() {
      const thread = useStream<Record<string, unknown>>({
        client: fakeClient as never,
        assistantId: "lead_agent",
        threadId: "t-probe",
        fetchStateHistory: false,
      });
      cap.current = thread;
      submitFn = async () => {
        await thread.submit({ messages: [] }, { threadId: "t-probe" });
      };
      return null;
    }

    act(() => {
      root.render(<Probe />);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });

    // Baseline: NOT loading before submit. Without this, an SDK that degrades
    // to isLoading-forever would pass the decisive assertion below vacuously.
    expect(cap.current!.isLoading).toBe(false);

    // Submit, but DO NOT push any SSE frame yet (simulates model thinking).
    await act(async () => {
      await submitFn!();
    });
    await act(async () => {
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(10);
    });

    // The decisive assertion for the report's claim: isLoading flips true AT
    // SUBMIT TIME, before any SSE frame arrives.
    expect(cap.current!.isLoading).toBe(true);

    // Still loading after the first frame while the stream is open.
    ctrl.push({ event: "values", data: { messages: [] } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });
    expect(cap.current!.isLoading).toBe(true);

    // And it clears when the stream ends — not loading-forever.
    ctrl.end();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });
    expect(cap.current!.isLoading).toBe(false);
  } finally {
    vi.useRealTimers();
  }
});

test("SDK useStream NEW THREAD: isLoading survives thread creation (no first frame)", async () => {
  vi.useFakeTimers();
  try {
    const ctrl = createControllableStream();
    const fakeClient = makeFakeClient(ctrl.iterable);

    const cap: { current: StreamReturn | null } = { current: null };
    let submitFn: (() => Promise<void>) | null = null;

    // Mirror hooks.ts: controlled onStreamThreadId, updated in onCreated.
    function Probe() {
      const [onStreamThreadId, setOnStreamThreadId] = useState<
        string | undefined
      >(undefined);
      const thread = useStream<Record<string, unknown>>({
        client: fakeClient as never,
        assistantId: "lead_agent",
        threadId: onStreamThreadId,
        fetchStateHistory: false,
        onCreated(meta) {
          setOnStreamThreadId(meta.thread_id);
        },
      });
      cap.current = thread;
      submitFn = async () => {
        await thread.submit({ messages: [] }, { threadId: onStreamThreadId });
      };
      return null;
    }

    act(() => {
      root.render(<Probe />);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });

    // Baseline: NOT loading before submit (guards against an isLoading-forever
    // SDK passing the assertion below vacuously).
    expect(cap.current!.isLoading).toBe(false);

    await act(async () => {
      await submitFn!();
    });
    await act(async () => {
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(20);
    });

    // Thread creation must not leave isLoading stuck false while thinking.
    expect(cap.current!.isLoading).toBe(true);

    ctrl.push({ event: "values", data: { messages: [] } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });
    expect(cap.current!.isLoading).toBe(true);

    ctrl.end();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });
    expect(cap.current!.isLoading).toBe(false);
  } finally {
    vi.useRealTimers();
  }
});
