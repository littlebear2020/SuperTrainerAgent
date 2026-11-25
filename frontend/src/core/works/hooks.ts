import { useQuery } from "@tanstack/react-query";
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

export function useArtifactSummaries(threadIds: readonly string[]) {
  const normalizedIds = useMemo(
    () => chunkThreadIds(threadIds).flat(),
    [threadIds],
  );

  return useQuery<ThreadArtifactSummary[]>({
    queryKey: ["works", "artifact-summaries", normalizedIds],
    enabled: normalizedIds.length > 0,
    queryFn: async () => {
      const summaries: ThreadArtifactSummary[] = [];
      for (const batch of chunkThreadIds(normalizedIds)) {
        summaries.push(...(await fetchArtifactSummaries(batch)));
      }
      return summaries;
    },
  });
}

export function useWorks(capability: WorkCapability): {
  works: WorkSummary[];
  isLoading: boolean;
  isError: boolean;
  refetch: () => Promise<void>;
} {
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
            .filter((thread) => agentNameOfThread(thread) === capability)
            .map((thread) => thread.thread_id)
        : [],
    [capability, hasAgentSource, threads],
  );
  const summariesQuery = useArtifactSummaries(threadIds);
  const summariesReady =
    threadIds.length === 0 || summariesQuery.data !== undefined;

  const works = useMemo(() => {
    const agentWorks =
      hasAgentSource && summariesReady
        ? buildAgentWorks(threads, summariesQuery.data ?? [], capability)
        : [];
    const taskWorks = hasTaskSource
      ? buildTaskWorks(tasksQuery.data ?? [], capability)
      : [];
    return [...agentWorks, ...taskWorks];
  }, [
    capability,
    hasAgentSource,
    hasTaskSource,
    summariesQuery.data,
    summariesReady,
    tasksQuery.data,
    threads,
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
      const requests: Promise<unknown>[] = [];
      if (hasAgentSource) requests.push(threadsQuery.refetch());
      if (hasTaskSource) requests.push(tasksQuery.refetch());
      if (hasAgentSource && threadIds.length > 0) {
        requests.push(summariesQuery.refetch());
      }
      await Promise.all(requests);
    },
  };
}
