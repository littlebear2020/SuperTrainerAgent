import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo } from "react";

import { useThreads } from "@/core/threads/hooks";
import { agentNameOfThread } from "@/core/threads/utils";
import { useVideoTasks } from "@/core/video-gen/hooks";
import type { WorkCapability } from "@/core/workspace/video-assets-route";

import { fetchArtifactSummaries } from "./api";
import { WORK_CAPABILITY_SOURCES } from "./capability-sources";
import { buildAgentWorks, buildTaskWorks, chunkThreadIds } from "./model";
import type { ThreadArtifactSummary, WorkSummary } from "./types";

const THREAD_SEARCH_PARAMS = {
  sortBy: "updated_at",
  sortOrder: "desc",
  select: ["thread_id", "created_at", "updated_at", "values", "metadata"],
} satisfies NonNullable<Parameters<typeof useThreads>[0]>;

function artifactSummariesKey(normalizedIds: readonly string[]) {
  return ["works", "artifact-summaries", normalizedIds] as const;
}

async function loadArtifactSummaries(
  normalizedIds: readonly string[],
): Promise<ThreadArtifactSummary[]> {
  const summaries: ThreadArtifactSummary[] = [];
  for (const batch of chunkThreadIds(normalizedIds)) {
    summaries.push(...(await fetchArtifactSummaries(batch)));
  }
  return summaries;
}

export function useArtifactSummaries(threadIds: readonly string[]) {
  const normalizedIds = useMemo(
    () => chunkThreadIds(threadIds).flat(),
    [threadIds],
  );

  return useQuery<ThreadArtifactSummary[]>({
    queryKey: artifactSummariesKey(normalizedIds),
    enabled: normalizedIds.length > 0,
    queryFn: () => loadArtifactSummaries(normalizedIds),
  });
}

export function useWorks(capability: WorkCapability): {
  works: WorkSummary[];
  isLoading: boolean;
  isError: boolean;
  refetch: () => Promise<void>;
} {
  const queryClient = useQueryClient();
  const sources = WORK_CAPABILITY_SOURCES[capability];
  const hasAgentSource = sources.agentNames.length > 0;
  const hasTaskSource = sources.taskTypes.length > 0;
  const threadsQuery = useThreads(THREAD_SEARCH_PARAMS, {
    enabled: hasAgentSource,
  });
  const tasksQuery = useVideoTasks({ enabled: hasTaskSource });
  const threads = useMemo(
    () => (hasAgentSource ? (threadsQuery.data ?? []) : []),
    [hasAgentSource, threadsQuery.data],
  );
  const threadIds = useMemo(
    () =>
      hasAgentSource
        ? threads
            .filter((thread) =>
              sources.agentNames.some(
                (name) => name === agentNameOfThread(thread),
              ),
            )
            .map((thread) => thread.thread_id)
        : [],
    [hasAgentSource, sources.agentNames, threads],
  );
  const summariesQuery = useArtifactSummaries(threadIds);
  const cachedSummaries = useMemo(() => {
    const currentThreadIds = new Set(threadIds);
    const cachedByThread = new Map<string, ThreadArtifactSummary>();
    const cachedQueries = queryClient
      .getQueryCache()
      .findAll({ queryKey: ["works", "artifact-summaries"] })
      .filter(
        (query) =>
          query.state.status === "success" && Array.isArray(query.state.data),
      )
      .sort(
        (left, right) => right.state.dataUpdatedAt - left.state.dataUpdatedAt,
      );
    for (const query of cachedQueries) {
      for (const summary of query.state.data as ThreadArtifactSummary[]) {
        if (
          currentThreadIds.has(summary.thread_id) &&
          !cachedByThread.has(summary.thread_id)
        ) {
          cachedByThread.set(summary.thread_id, summary);
        }
      }
    }
    return cachedByThread.size > 0
      ? Array.from(cachedByThread.values())
      : undefined;
  }, [queryClient, threadIds]);
  const usingCachedFallback =
    summariesQuery.data === undefined && cachedSummaries !== undefined;
  const summaries = summariesQuery.data ?? cachedSummaries;
  const summariesReady = threadIds.length === 0 || summaries !== undefined;

  const works = useMemo(() => {
    const summaryList = summaries ?? [];
    const coveredThreads =
      usingCachedFallback || summariesQuery.isError
        ? threads.filter((thread) =>
            summaryList.some(
              (summary) => summary.thread_id === thread.thread_id,
            ),
          )
        : threads;
    const agentWorks =
      hasAgentSource && summariesReady
        ? buildAgentWorks(coveredThreads, summaryList, capability)
        : [];
    const taskWorks = hasTaskSource
      ? buildTaskWorks(tasksQuery.data ?? [], capability)
      : [];
    return [...agentWorks, ...taskWorks];
  }, [
    capability,
    hasAgentSource,
    hasTaskSource,
    summaries,
    summariesQuery.isError,
    summariesReady,
    tasksQuery.data,
    threads,
    usingCachedFallback,
  ]);

  return {
    works,
    isLoading:
      (hasAgentSource &&
        (threadsQuery.isLoading ||
          (threadIds.length > 0 && summariesQuery.isLoading))) ||
      (hasTaskSource && tasksQuery.isLoading),
    isError:
      (hasAgentSource && (threadsQuery.isError || summariesQuery.isError)) ||
      (hasTaskSource && tasksQuery.isError),
    refetch: async () => {
      const tasksRequest = hasTaskSource ? tasksQuery.refetch() : null;
      try {
        if (hasAgentSource) {
          const refreshed = await threadsQuery.refetch();
          if (refreshed.error) throw refreshed.error;
          const refreshedThreads = refreshed.data ?? threads;
          const nextIds = refreshedThreads
            .filter((thread) =>
              sources.agentNames.some(
                (name) => name === agentNameOfThread(thread),
              ),
            )
            .map((thread) => thread.thread_id);
          const normalizedIds = chunkThreadIds(nextIds).flat();
          if (normalizedIds.length > 0) {
            await queryClient.fetchQuery({
              queryKey: artifactSummariesKey(normalizedIds),
              queryFn: () => loadArtifactSummaries(normalizedIds),
            });
          }
        }
      } finally {
        if (tasksRequest) await tasksRequest;
      }
    },
  };
}
