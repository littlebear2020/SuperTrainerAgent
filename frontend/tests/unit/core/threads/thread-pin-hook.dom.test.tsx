/**
 * P2-9: usePinThread — PATCH /api/threads/{id} with the deerflow_pinned
 * metadata key. Failure must surface (no silent swallow); success updates
 * the thread search caches so the list re-sorts immediately.
 *
 * 仓库无 @testing-library/react：沿用 project-mutations-error.test.tsx 的
 * createRoot + act 手写 renderHook 约定；happy-dom 提供 DOM。
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetch as csrfFetch } from "@/core/api/fetcher";
import { usePinThread } from "@/core/threads/hooks";
import type { AgentThread } from "@/core/threads/types";

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://localhost:8001",
}));
vi.mock("@/core/api/fetcher", () => ({ fetch: vi.fn() }));

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const mockFetch = vi.mocked(csrfFetch);

function jsonResponse(body: unknown, ok = true, status = 200) {
  return {
    ok,
    status,
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

let cleanups: Array<() => Promise<void>> = [];

async function renderHook(queryClient: QueryClient) {
  let latest: ReturnType<typeof usePinThread> | null = null;
  function Probe() {
    latest = usePinThread();
    return null;
  }
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root: Root = createRoot(container);
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <Probe />
      </QueryClientProvider>,
    );
  });
  cleanups.push(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });
  return () => {
    if (latest === null) {
      throw new Error("probe not rendered");
    }
    return latest;
  };
}

async function runMutation(fn: () => void) {
  await act(async () => {
    fn();
    await new Promise((r) => setTimeout(r, 0));
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  cleanups = [];
});

afterEach(async () => {
  for (const cleanup of cleanups) {
    await cleanup();
  }
});

describe("usePinThread", () => {
  it("PATCHes the deerflow_pinned metadata key to the threads endpoint", async () => {
    mockFetch.mockResolvedValue(
      jsonResponse({ metadata: { deerflow_pinned: true } }),
    );
    const queryClient = new QueryClient({
      defaultOptions: { mutations: { retry: false } },
    });
    const getHook = await renderHook(queryClient);

    await runMutation(() =>
      getHook().mutate({ threadId: "t 1", pinned: true }),
    );

    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [url, init] = mockFetch.mock.calls[0] ?? [];
    expect(url).toBe("http://localhost:8001/api/threads/t%201");
    expect(init?.method).toBe("PATCH");
    expect(JSON.parse(init?.body as string)).toEqual({
      metadata: { deerflow_pinned: true },
    });
  });

  it("updates cached thread metadata on success", async () => {
    mockFetch.mockResolvedValue(
      jsonResponse({ metadata: { deerflow_pinned: true } }),
    );
    const queryClient = new QueryClient({
      defaultOptions: { mutations: { retry: false } },
    });
    queryClient.setQueryData(["threads", "search", { limit: 50 }], [
      { thread_id: "t1", metadata: {} },
      { thread_id: "t2", metadata: { other: 1 } },
    ] satisfies Array<Partial<AgentThread>>);
    const getHook = await renderHook(queryClient);

    await runMutation(() => getHook().mutate({ threadId: "t1", pinned: true }));

    const cached = queryClient.getQueryData<Array<AgentThread>>([
      "threads",
      "search",
      { limit: 50 },
    ]);
    expect(cached?.[0]?.metadata).toEqual({ deerflow_pinned: true });
    expect(cached?.[1]?.metadata).toEqual({ other: 1 });
  });

  it("surfaces the server error detail instead of swallowing it", async () => {
    mockFetch.mockResolvedValue(
      jsonResponse({ detail: "Thread not found" }, false, 404),
    );
    const queryClient = new QueryClient({
      defaultOptions: { mutations: { retry: false } },
    });
    const getHook = await renderHook(queryClient);

    await runMutation(() => getHook().mutate({ threadId: "t1", pinned: true }));

    expect(getHook().isError).toBe(true);
    expect(getHook().error).toBeInstanceOf(Error);
    expect(getHook().error?.message).toContain("Thread not found");
  });
});
