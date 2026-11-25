import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { useThread } from "@/components/workspace/messages/context";

import {
  loadArtifactContent,
  loadArtifactContentFromToolCall,
  probeWriteFileRef,
  type WriteFileContentState,
  type WriteFileTargetProbe,
} from "./loader";

/**
 * write-file 查询 key 里的归属信号。目标定下来（settled）之后加载标志不再影响判定，
 * 不进 key——否则 result 到达时 GET 一次、流结束 isLoading 翻 false 又换新 key 再 GET
 * 一次，同一份当前文件至少请求两遍。
 */
function revisionOf(
  probe: WriteFileTargetProbe | null,
  flags: {
    sdkLoading: boolean;
    streaming: boolean;
    historyLoading: boolean;
    hasMore: boolean;
  },
): string {
  if (!probe) return "";
  if (probe.phase === "settled") return probe.phase;
  return [
    probe.phase,
    flags.sdkLoading,
    flags.streaming,
    flags.historyLoading,
    flags.hasMore,
  ].join("|");
}

type WriteFileQueryLike = {
  isError: boolean;
  error: unknown;
  data: WriteFileContentState | undefined;
};

/**
 * write-file 引用当前的内容状态。查询禁用时没有状态（「暂不加载」不泄漏成「等待中」，
 * 与普通文件分支一致）；loader 自己把 GET 失败收敛成 failed，这里只兜底查询本身的
 * 意外错误，不让它回退成永久的 pending。
 */
function resolveWriteFileState(
  query: WriteFileQueryLike,
  enabled: boolean,
): WriteFileContentState | undefined {
  if (!enabled) return undefined;
  if (query.isError) {
    return {
      status: "failed",
      message:
        query.error instanceof Error
          ? query.error.message
          : String(query.error),
    };
  }
  return query.data ?? { status: "pending" };
}

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
  // `enabled` 对两类查询同义：调用方说「暂不加载内容」时，write-file 引用也不判定
  // （append / str_replace 成功后会真实 GET 文件）
  const contentEnabled = enabled !== false;
  const revision = revisionOf(probe, {
    sdkLoading: thread.isThreadLoading,
    streaming: thread.isLoading,
    historyLoading: isHistoryLoading ?? false,
    hasMore: hasMoreHistory ?? false,
  });
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
    enabled: contentEnabled && isWriteFile,
    // 每次归属信号变化都是新 key；同一 key 内不做缓存（流式中的 pending 要能翻）
    staleTime: 0,
    gcTime: 0,
  });

  const { data, isLoading, error } = useQuery({
    queryKey: ["artifact", filepath, threadId],
    queryFn: () => {
      return loadArtifactContent({ filepath, threadId });
    },
    // write-file 引用永远不走普通文件的查询（按前缀挡掉）
    enabled: contentEnabled && !isWriteFile,
    // Short window only to dedupe the burst of mounts for one artifact (e.g. .skill
    // ZIP extraction). A long staleTime served stale content after the agent
    // rewrote the file in a later run.
    staleTime: 5 * 1000,
  });
  const writeFileState = isWriteFile
    ? resolveWriteFileState(writeFileQuery, contentEnabled)
    : undefined;
  return {
    content:
      writeFileState?.status === "ready"
        ? writeFileState.content
        : data?.content,
    url: isWriteFile ? undefined : data?.url,
    sha256: isWriteFile ? undefined : data?.sha256,
    /** write-file 引用的五态；普通文件、或查询被 `enabled: false` 关掉时为 undefined。 */
    writeFileState,
    isLoading: isWriteFile
      ? contentEnabled &&
        (writeFileQuery.isPending || writeFileState?.status === "pending")
      : isLoading,
    error,
  };
}
