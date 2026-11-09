import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { useThread } from "@/components/workspace/messages/context";

import { loadArtifactContent, loadArtifactContentFromToolCall } from "./loader";

export function useArtifactContent({
  filepath,
  threadId,
  enabled,
}: {
  filepath: string;
  threadId: string;
  enabled?: boolean;
}) {
  const isWriteFile = useMemo(() => {
    return filepath.startsWith("write-file:");
  }, [filepath]);
  const { thread } = useThread();
  const content = useMemo(() => {
    if (isWriteFile) {
      return loadArtifactContentFromToolCall({ url: filepath, thread });
    }
    return null;
  }, [filepath, isWriteFile, thread]);

  const { data, isLoading, error } = useQuery({
    queryKey: ["artifact", filepath, threadId],
    queryFn: () => {
      return loadArtifactContent({ filepath, threadId });
    },
    enabled,
    // Short window only to dedupe the burst of mounts for one artifact (e.g. .skill
    // ZIP extraction). A long staleTime served stale content after the agent
    // rewrote the file in a later run.
    staleTime: 5 * 1000,
  });
  return {
    content: isWriteFile ? content : data?.content,
    url: isWriteFile ? undefined : data?.url,
    sha256: isWriteFile ? undefined : data?.sha256,
    isLoading,
    error,
  };
}
