import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { useThread } from "@/components/workspace/messages/context";

import {
  loadArtifactContent,
  loadArtifactContentFromToolCall,
  probeWriteFileRef,
  type WriteFileContentState,
} from "./loader";

/**
 * 产物正文。普通文件走 GET；write-file 引用（`write-file:` 前缀）按五态判定
 * （ready / pending / not_found / invalid / failed，见 loader）。
 *
 * 五态的 query key 带着归属信号（目标参数是否完整、result 是否到位）与加载状态
 * （SDK 首屏、自定义 run 历史、hasMoreHistory）：run 还没 onFinish 时 result 一到，
 * pending 就能过渡到 ready / failed，不用等到流结束。
 */
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
  const { thread, isHistoryLoading, hasMoreHistory } = useThread();
  const probe = useMemo(
    () => (isWriteFile ? probeWriteFileRef(filepath, thread) : null),
    [filepath, isWriteFile, thread],
  );
  const revision = probe
    ? [
        probe.phase,
        thread.isThreadLoading,
        thread.isLoading,
        isHistoryLoading ?? false,
        hasMoreHistory ?? false,
      ].join("|")
    : "";
  const writeFileQuery = useQuery({
    queryKey: ["artifact", "write-file", filepath, threadId, revision],
    queryFn: () =>
      loadArtifactContentFromToolCall({
        url: filepath,
        thread,
        threadId,
        isHistoryLoading,
        hasMoreHistory,
      }),
    enabled: isWriteFile,
    // 每次归属信号变化都是新 key；同一 key 内不做缓存（流式中的 pending 要能翻）
    staleTime: 0,
    gcTime: 0,
  });

  const { data, isLoading, error } = useQuery({
    queryKey: ["artifact", filepath, threadId],
    queryFn: () => {
      return loadArtifactContent({ filepath, threadId });
    },
    enabled: enabled && !isWriteFile,
    // Short window only to dedupe the burst of mounts for one artifact (e.g. .skill
    // ZIP extraction). A long staleTime served stale content after the agent
    // rewrote the file in a later run.
    staleTime: 5 * 1000,
  });
  const writeFileState: WriteFileContentState | undefined = isWriteFile
    ? (writeFileQuery.data ?? { status: "pending" })
    : undefined;
  return {
    content:
      writeFileState?.status === "ready"
        ? writeFileState.content
        : data?.content,
    url: isWriteFile ? undefined : data?.url,
    sha256: isWriteFile ? undefined : data?.sha256,
    /** write-file 引用的五态；普通文件为 undefined。 */
    writeFileState,
    isLoading: isWriteFile
      ? writeFileQuery.isPending || writeFileState?.status === "pending"
      : isLoading,
    error,
  };
}
