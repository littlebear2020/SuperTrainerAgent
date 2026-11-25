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
    // `enabled` 对两类查询同义：调用方说「暂不加载内容」时，write-file 引用也不判定
    // （append / str_replace 成功后会真实 GET 文件）
    enabled: enabled !== false && isWriteFile,
    // 每次归属信号变化都是新 key；同一 key 内不做缓存（流式中的 pending 要能翻）
    staleTime: 0,
    gcTime: 0,
  });

  const { data, isLoading, error } = useQuery({
    queryKey: ["artifact", filepath, threadId],
    queryFn: () => {
      return loadArtifactContent({ filepath, threadId });
    },
    // `enabled` 缺省 = 启用（TanStack 把 undefined 当 true，不能写 `enabled && …`）；
    // write-file 引用永远不走普通文件的查询
    enabled: enabled !== false && !isWriteFile,
    // Short window only to dedupe the burst of mounts for one artifact (e.g. .skill
    // ZIP extraction). A long staleTime served stale content after the agent
    // rewrote the file in a later run.
    staleTime: 5 * 1000,
  });
  const writeFileState: WriteFileContentState | undefined = isWriteFile
    ? writeFileQuery.isError
      ? {
          status: "failed",
          // loader 自己把 GET 失败收敛成 failed；这里只兜底查询本身的意外错误，
          // 不让它回退成永久的 pending
          message:
            writeFileQuery.error instanceof Error
              ? writeFileQuery.error.message
              : String(writeFileQuery.error),
        }
      : (writeFileQuery.data ?? { status: "pending" })
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
