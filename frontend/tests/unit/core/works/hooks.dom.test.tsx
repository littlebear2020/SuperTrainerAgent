import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentThread } from "@/core/threads/types";
import type { VideoTaskBrief } from "@/core/video-gen/types";
import { WORK_CAPABILITY_SOURCES } from "@/core/works/capability-sources";
import { useArtifactSummaries, useWorks } from "@/core/works/hooks";
import type { ThreadArtifactSummary } from "@/core/works/types";
import type { WorkCapability } from "@/core/workspace/video-assets-route";

const h = vi.hoisted(() => ({
  fetchSummaries: vi.fn(),
  threads: [] as AgentThread[],
  tasks: [] as VideoTaskBrief[],
  threadsLoading: false,
  tasksLoading: false,
  threadsError: false,
  tasksError: false,
  threadsOptions: undefined as { enabled?: boolean } | undefined,
  tasksOptions: undefined as { enabled?: boolean } | undefined,
  threadsRefetch: vi.fn(),
  tasksRefetch: vi.fn(),
}));

vi.mock("@/core/works/api", () => ({
  fetchArtifactSummaries: h.fetchSummaries,
}));
vi.mock("@/core/threads/hooks", () => ({
  useThreads: (_params: unknown, options?: { enabled?: boolean }) => {
    h.threadsOptions = options;
    return {
      data: h.threads,
      isLoading: h.threadsLoading,
      isError: h.threadsError,
      refetch: h.threadsRefetch,
    };
  },
}));
vi.mock("@/core/video-gen/hooks", () => ({
  useVideoTasks: (options?: { enabled?: boolean }) => {
    h.tasksOptions = options;
    return {
      data: h.tasks,
      isLoading: h.tasksLoading,
      isError: h.tasksError,
      refetch: h.tasksRefetch,
    };
  },
}));

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

let latestSummaries: ReturnType<typeof useArtifactSummaries> | null = null;
let latestWorks: ReturnType<typeof useWorks> | null = null;

function SummaryProbe({ ids }: { ids: readonly string[] }) {
  // eslint-disable-next-line react-hooks/globals
  latestSummaries = useArtifactSummaries(ids);
  return null;
}

function WorksProbe({ capability }: { capability: WorkCapability }) {
  // eslint-disable-next-line react-hooks/globals
  latestWorks = useWorks(capability);
  return null;
}

function currentSummaries() {
  if (!latestSummaries) throw new Error("summary probe not rendered");
  return latestSummaries;
}

function currentWorks() {
  if (!latestWorks) throw new Error("works probe not rendered");
  return latestWorks;
}

function makeThread(id: string): AgentThread {
  return {
    thread_id: id,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-02T00:00:00Z",
    metadata: { agent_name: "creative-studio" },
    status: "idle",
    values: { title: `Title ${id}`, messages: [], artifacts: [] },
    interrupts: {},
  } as unknown as AgentThread;
}

function summary(threadId: string): ThreadArtifactSummary {
  return { thread_id: threadId, delivery_state: "not_delivered", files: [] };
}

async function waitForAssertion(assertion: () => void) {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    try {
      assertion();
      return;
    } catch (error) {
      if (attempt === 39) throw error;
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 5));
      });
    }
  }
}

describe("works data hooks", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    vi.clearAllMocks();
    h.threads = [];
    h.tasks = [];
    h.threadsLoading = false;
    h.tasksLoading = false;
    h.threadsError = false;
    h.tasksError = false;
    h.threadsOptions = undefined;
    h.tasksOptions = undefined;
    h.threadsRefetch.mockImplementation(() =>
      Promise.resolve({ data: h.threads }),
    );
    h.tasksRefetch.mockImplementation(() => Promise.resolve({ data: h.tasks }));
    latestSummaries = null;
    latestWorks = null;
    queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false, refetchOnWindowFocus: false },
      },
    });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    queryClient.clear();
    container.remove();
  });

  async function render(element: ReactElement) {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          {element}
        </QueryClientProvider>,
      );
    });
  }

  it("loads 101 unique IDs in sequential 50/50/1 batches", async () => {
    const ids = Array.from(
      { length: 101 },
      (_, index) => `thread-${String(index).padStart(3, "0")}`,
    );
    let active = 0;
    let maxActive = 0;
    h.fetchSummaries.mockImplementation(async (batch: string[]) => {
      active += 1;
      maxActive = Math.max(maxActive, active);
      await Promise.resolve();
      active -= 1;
      return batch.map(summary);
    });

    await render(<SummaryProbe ids={[...ids, "thread-000"]} />);
    await waitForAssertion(() =>
      expect(currentSummaries().data).toHaveLength(101),
    );

    expect(h.fetchSummaries.mock.calls.map((call) => call[0])).toEqual([
      ids.slice(0, 50),
      ids.slice(50, 100),
      ids.slice(100),
    ]);
    expect(maxActive).toBe(1);
    expect(
      new Set(currentSummaries().data?.map((item) => item.thread_id)).size,
    ).toBe(101);
  });

  it("uses the same sorted unique query key when input order changes", async () => {
    h.fetchSummaries.mockImplementation(async (batch: string[]) =>
      batch.map(summary),
    );
    const ids = ["thread-c", "thread-a", "thread-b", "thread-a"];

    await render(<SummaryProbe ids={ids} />);
    await waitForAssertion(() =>
      expect(currentSummaries().data).toHaveLength(3),
    );
    const queryKey = queryClient.getQueryCache().getAll()[0]?.queryKey;
    const requestCount = h.fetchSummaries.mock.calls.length;

    await render(<SummaryProbe ids={["thread-b", "thread-c", "thread-a"]} />);

    expect(queryClient.getQueryCache().getAll()).toHaveLength(1);
    expect(queryClient.getQueryCache().getAll()[0]?.queryKey).toEqual(queryKey);
    expect(JSON.stringify(queryKey)).toContain("thread-a");
    expect(h.fetchSummaries).toHaveBeenCalledTimes(requestCount);
  });

  it("does not request an empty ID set", async () => {
    await render(<SummaryProbe ids={[]} />);

    expect(h.fetchSummaries).not.toHaveBeenCalled();
    expect(currentSummaries().isLoading).toBe(false);
    expect(currentSummaries().isError).toBe(false);
  });

  it("discards partial batches and retries the whole query after a failure", async () => {
    const ids = Array.from(
      { length: 101 },
      (_, index) => `thread-${String(index).padStart(3, "0")}`,
    );
    let failSecondBatch = true;
    h.fetchSummaries.mockImplementation(async (batch: string[]) => {
      if (batch[0] === "thread-050" && failSecondBatch) {
        throw new Error("batch failed");
      }
      return batch.map(summary);
    });

    await render(<SummaryProbe ids={ids} />);
    await waitForAssertion(() => expect(currentSummaries().isError).toBe(true));

    expect(currentSummaries().data).toBeUndefined();
    expect(h.fetchSummaries).toHaveBeenCalledTimes(2);

    failSecondBatch = false;
    await act(async () => {
      await currentSummaries().refetch();
    });
    await waitForAssertion(() =>
      expect(currentSummaries().data).toHaveLength(101),
    );

    expect(currentSummaries().isError).toBe(false);
    expect(h.fetchSummaries).toHaveBeenCalledTimes(5);
  });

  it("does not derive not_delivered works while summaries are loading", async () => {
    h.threads = [makeThread("thread-1")];
    let resolveRequest: ((value: ThreadArtifactSummary[]) => void) | undefined;
    h.fetchSummaries.mockImplementation(
      () =>
        new Promise<ThreadArtifactSummary[]>((resolve) => {
          resolveRequest = resolve;
        }),
    );

    await render(<WorksProbe capability="creative-studio" />);

    expect(currentWorks().isLoading).toBe(true);
    expect(currentWorks().works).toEqual([]);

    await act(async () => {
      resolveRequest?.([summary("thread-1")]);
      await Promise.resolve();
    });
    await waitForAssertion(() => expect(currentWorks().works).toHaveLength(1));

    expect(currentWorks().works[0]?.agentDeliveryState).toBe("not_delivered");
    expect(currentWorks().isLoading).toBe(false);
  });

  it("keeps the last successful works when a summary refresh fails", async () => {
    h.threads = [makeThread("thread-1")];
    h.fetchSummaries.mockResolvedValueOnce([summary("thread-1")]);
    await render(<WorksProbe capability="creative-studio" />);
    await waitForAssertion(() => expect(currentWorks().works).toHaveLength(1));

    h.fetchSummaries.mockRejectedValueOnce(new Error("refresh failed"));
    await act(async () => {
      await expect(currentWorks().refetch()).rejects.toThrow("refresh failed");
    });
    await waitForAssertion(() => expect(currentWorks().isError).toBe(true));

    expect(currentWorks().works).toHaveLength(1);
    expect(currentWorks().works[0]?.id).toBe("thread:thread-1");
  });

  it("keeps successful Agent works when an expanded ID-set summary fails", async () => {
    const first = makeThread("thread-1");
    const second = makeThread("thread-2");
    h.threads = [first];
    h.fetchSummaries.mockResolvedValueOnce([summary("thread-1")]);
    await render(<WorksProbe capability="creative-studio" />);
    await waitForAssertion(() => expect(currentWorks().works).toHaveLength(1));

    h.threads = [first, second];
    h.fetchSummaries.mockRejectedValueOnce(new Error("expanded batch failed"));
    await render(<WorksProbe capability="creative-studio" />);
    await waitForAssertion(() => expect(currentWorks().isError).toBe(true));

    expect(currentWorks().works.map((work) => work.id)).toEqual([
      "thread:thread-1",
    ]);
  });

  it("merges matching cached summaries across ID-set changes", async () => {
    const first = makeThread("thread-1");
    const second = makeThread("thread-2");
    h.threads = [first];
    h.fetchSummaries.mockResolvedValueOnce([summary("thread-1")]);
    await render(<WorksProbe capability="creative-studio" />);
    await waitForAssertion(() => expect(currentWorks().works).toHaveLength(1));

    h.threads = [second];
    h.fetchSummaries.mockResolvedValueOnce([summary("thread-2")]);
    await render(<WorksProbe capability="creative-studio" />);
    await waitForAssertion(() =>
      expect(currentWorks().works.map((work) => work.id)).toEqual([
        "thread:thread-2",
      ]),
    );

    h.threads = [first, second];
    h.fetchSummaries.mockRejectedValueOnce(new Error("combined batch failed"));
    await render(<WorksProbe capability="creative-studio" />);
    await waitForAssertion(() => expect(currentWorks().isError).toBe(true));

    expect(currentWorks().works.map((work) => work.id)).toEqual([
      "thread:thread-1",
      "thread:thread-2",
    ]);
  });

  it("refetches summaries for the ID set returned by the thread refresh", async () => {
    const first = makeThread("thread-1");
    const second = makeThread("thread-2");
    h.threads = [first];
    h.fetchSummaries.mockResolvedValueOnce([summary("thread-1")]);
    await render(<WorksProbe capability="creative-studio" />);
    await waitForAssertion(() => expect(currentWorks().works).toHaveLength(1));

    h.threadsRefetch.mockResolvedValueOnce({ data: [first, second] });
    h.fetchSummaries.mockClear();
    h.fetchSummaries.mockResolvedValue([
      summary("thread-1"),
      summary("thread-2"),
    ]);
    await act(async () => {
      await currentWorks().refetch();
    });

    expect(h.fetchSummaries).toHaveBeenCalledWith(["thread-1", "thread-2"]);
  });

  it("uses configured Agent names instead of assuming they equal capability IDs", async () => {
    const source = WORK_CAPABILITY_SOURCES["creative-studio"];
    const originalNames = source.agentNames;
    Reflect.set(source, "agentNames", ["creative-agent"]);
    const thread = makeThread("custom-agent-thread");
    Object.assign(thread, { metadata: { agent_name: "creative-agent" } });
    h.threads = [thread];
    h.fetchSummaries.mockResolvedValueOnce([summary(thread.thread_id)]);

    try {
      await render(<WorksProbe capability="creative-studio" />);
      await waitForAssertion(() =>
        expect(currentWorks().works).toHaveLength(1),
      );
    } finally {
      Reflect.set(source, "agentNames", originalNames);
    }
  });

  it("does not refresh old summaries when the thread refresh fails", async () => {
    const first = makeThread("thread-1");
    h.threads = [first];
    h.fetchSummaries.mockResolvedValueOnce([summary("thread-1")]);
    await render(<WorksProbe capability="creative-studio" />);
    await waitForAssertion(() => expect(currentWorks().works).toHaveLength(1));

    h.threadsRefetch.mockResolvedValueOnce({
      data: undefined,
      error: new Error("thread refresh failed"),
    });
    h.fetchSummaries.mockClear();
    await act(async () => {
      await expect(currentWorks().refetch()).rejects.toThrow(
        "thread refresh failed",
      );
    });

    expect(h.fetchSummaries).not.toHaveBeenCalled();
  });

  it("awaits an already-started task refresh when summary refresh fails", async () => {
    const thread = makeThread("course-thread");
    Object.assign(thread, { metadata: { agent_name: "course-video" } });
    h.threads = [thread];
    h.fetchSummaries.mockResolvedValueOnce([summary(thread.thread_id)]);
    await render(<WorksProbe capability="course-video" />);
    await waitForAssertion(() => expect(currentWorks().works).toHaveLength(1));

    let finishTasks: (() => void) | undefined;
    h.tasksRefetch.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishTasks = () => resolve({ data: [] });
        }),
    );
    h.fetchSummaries.mockRejectedValueOnce(new Error("summary refresh failed"));
    let rejected = false;
    let refreshPromise = Promise.resolve();
    await act(async () => {
      refreshPromise = currentWorks().refetch();
      void refreshPromise.catch(() => {
        rejected = true;
      });
      await Promise.resolve();
    });
    await waitForAssertion(() => expect(h.fetchSummaries).toHaveBeenCalled());

    expect(rejected).toBe(false);
    await act(async () => {
      finishTasks?.();
      await expect(refreshPromise).rejects.toThrow("summary refresh failed");
    });
  });

  it("preserves the thread error when the concurrent task refresh also fails", async () => {
    const thread = makeThread("course-thread");
    Object.assign(thread, { metadata: { agent_name: "course-video" } });
    h.threads = [thread];
    h.fetchSummaries.mockResolvedValueOnce([summary(thread.thread_id)]);
    await render(<WorksProbe capability="course-video" />);
    await waitForAssertion(() => expect(currentWorks().works).toHaveLength(1));

    const threadError = new Error("thread refresh failed");
    h.threadsRefetch.mockResolvedValueOnce({
      data: undefined,
      error: threadError,
    });
    h.tasksRefetch.mockRejectedValueOnce(new Error("task refresh failed"));

    await act(async () => {
      await expect(currentWorks().refetch()).rejects.toBe(threadError);
    });
    expect(h.tasksRefetch).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["creative-studio", true, false],
    ["ai_video", false, true],
    ["course-video", true, true],
  ] as const)(
    "%s enables only its actual sources and refetches only those sources",
    async (capability, agentEnabled, tasksEnabled) => {
      await render(<WorksProbe capability={capability} />);

      expect(h.threadsOptions).toEqual({ enabled: agentEnabled });
      expect(h.tasksOptions).toEqual({ enabled: tasksEnabled });

      await act(async () => {
        await currentWorks().refetch();
      });
      expect(h.threadsRefetch).toHaveBeenCalledTimes(agentEnabled ? 1 : 0);
      expect(h.tasksRefetch).toHaveBeenCalledTimes(tasksEnabled ? 1 : 0);
      if (!agentEnabled) expect(h.fetchSummaries).not.toHaveBeenCalled();
    },
  );
});
