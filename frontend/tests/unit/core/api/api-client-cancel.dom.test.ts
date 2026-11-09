/**
 * ``runs.cancel`` error handling in the API client.
 *
 * Clicking stop while a run is in its finish window (backend already flipped
 * to ``success``, SSE hasn't drained) makes the gateway answer 409
 * "is not cancellable". The SDK rejects, and because the call happens inside a
 * floating promise in the SDK's own stream callback, `await thread.stop()`
 * in component code cannot catch it — it surfaces as an unhandled rejection.
 * The only place to contain it is this wrapper.
 *
 * A 409 on a terminal run is a genuine no-op: the run is already stopped, so
 * the user's intent holds. Every other failure must still propagate.
 */

import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const cancelMock = vi.fn();
const streamMock = vi.fn();
const joinStreamMock = vi.fn();

vi.mock("@langchain/langgraph-sdk/client", () => ({
  Client: class {
    runs = {
      cancel: cancelMock,
      stream: streamMock,
      joinStream: joinStreamMock,
    };
  },
  // Run creation is bound off a standalone RunsClient (see api-client.ts);
  // the mock only needs a bindable `stream` for client construction.
  RunsClient: class {
    stream = streamMock;
  },
}));

vi.mock("../../../../src/core/config", () => ({
  getLangGraphBaseURL: () => "http://localhost:2024",
}));

/** Build an SDK-shaped HTTP error. */
function httpError(
  status: number,
  message: string,
): Error & { status: number } {
  const error = new Error(message) as Error & { status: number };
  error.status = status;
  return error;
}

const NOT_CANCELLABLE = () =>
  httpError(409, "HTTP 409: Run run-1 is not cancellable (status: success)");

async function freshClient() {
  vi.resetModules();
  const { getAPIClient } = await import("@/core/api/api-client");
  return getAPIClient();
}

beforeEach(() => {
  cancelMock.mockReset().mockResolvedValue(undefined);
  window.sessionStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("runs.cancel error containment", () => {
  test("passes arguments through to the SDK unchanged", async () => {
    const client = await freshClient();

    await client.runs.cancel("thread-1", "run-1", false, "interrupt");

    expect(cancelMock).toHaveBeenCalledWith(
      "thread-1",
      "run-1",
      false,
      "interrupt",
    );
  });

  test("swallows the terminal-state 409 instead of rejecting", async () => {
    cancelMock.mockRejectedValue(NOT_CANCELLABLE());
    const client = await freshClient();

    await expect(
      client.runs.cancel("thread-1", "run-1"),
    ).resolves.toBeUndefined();
  });

  test("clears the stale reconnect key for the cancelled run", async () => {
    cancelMock.mockRejectedValue(NOT_CANCELLABLE());
    window.sessionStorage.setItem("lg:stream:thread-1", "run-1");
    const client = await freshClient();

    await client.runs.cancel("thread-1", "run-1");

    expect(window.sessionStorage.getItem("lg:stream:thread-1")).toBeNull();
  });

  test("leaves a newer run's reconnect key untouched", async () => {
    cancelMock.mockRejectedValue(NOT_CANCELLABLE());
    window.sessionStorage.setItem("lg:stream:thread-1", "run-2");
    const client = await freshClient();

    await client.runs.cancel("thread-1", "run-1");

    expect(window.sessionStorage.getItem("lg:stream:thread-1")).toBe("run-2");
  });

  test("still rejects on a real cancel failure", async () => {
    cancelMock.mockRejectedValue(httpError(500, "HTTP 500: boom"));
    const client = await freshClient();

    await expect(client.runs.cancel("thread-1", "run-1")).rejects.toThrow(
      "boom",
    );
  });

  test("still rejects when the run is live on another worker", async () => {
    // A 409, but not the terminal-state one — the run is genuinely still
    // running and failing to cancel it must stay visible.
    cancelMock.mockRejectedValue(
      httpError(
        409,
        "HTTP 409: Run run-1 is not active on this worker and cannot be cancelled",
      ),
    );
    const client = await freshClient();

    await expect(client.runs.cancel("thread-1", "run-1")).rejects.toThrow(
      "not active on this worker",
    );
  });

  test("still rejects a 404 for an unknown run", async () => {
    cancelMock.mockRejectedValue(httpError(404, "HTTP 404: Run not found"));
    const client = await freshClient();

    await expect(client.runs.cancel("thread-1", "run-1")).rejects.toThrow(
      "not found",
    );
  });

  test("does not clear the reconnect key when the error propagates", async () => {
    cancelMock.mockRejectedValue(httpError(500, "HTTP 500: boom"));
    window.sessionStorage.setItem("lg:stream:thread-1", "run-1");
    const client = await freshClient();

    await expect(client.runs.cancel("thread-1", "run-1")).rejects.toThrow();
    expect(window.sessionStorage.getItem("lg:stream:thread-1")).toBe("run-1");
  });
});
