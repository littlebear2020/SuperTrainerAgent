/**
 * Writer React Query hooks.
 */

import {
  type QueryClient,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { toast } from "sonner";

import {
  type ListWriterDocsParams,
  type WriterDocListResponse,
  createWriterDoc,
  deleteWriterDoc,
  getContentTask,
  getWriterDoc,
  listWriterDocs,
  submitPptExport,
  updateWriterDoc,
  WriterDocConflictError,
} from "./api";
import type {
  PptExportParams,
  WriterDocCreateParams,
  WriterDocUpdateParams,
} from "./types";

// ── Query keys ───────────────────────────────────────────────────────────

const WRITER_KEY = "writerDocs";
const CONTENT_TASK_KEY = "contentTask";

// ── List docs ────────────────────────────────────────────────────────────

/**
 * B5-T2: Paginated document list hook with search/filter/sort.
 * queryKey includes params so different views don't share cache.
 * keepPreviousData prevents flash on page change.
 */
export function useWriterDocs(params?: ListWriterDocsParams) {
  return useQuery({
    queryKey: [WRITER_KEY, "list", params ?? {}],
    queryFn: () => listWriterDocs(params),
    placeholderData: (prev) => prev,
  });
}

// ── Single doc ───────────────────────────────────────────────────────────

export function useWriterDoc(docId: string | undefined) {
  return useQuery({
    queryKey: [WRITER_KEY, docId],
    queryFn: () => getWriterDoc(docId!),
    enabled: !!docId,
  });
}

// ── Create doc ───────────────────────────────────────────────────────────

/**
 * Mutation options for creating a writer doc, extracted so the error-surfacing
 * behavior is unit-testable without a DOM. ``onError`` shows a toast with the
 * backend ``detail`` — without it, a failed create POST (403/503/500) was
 * swallowed by React Query and the user saw "click create → nothing happens".
 */
export function createWriterDocMutationOptions(qc: QueryClient) {
  return {
    mutationFn: (params: WriterDocCreateParams) => createWriterDoc(params),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [WRITER_KEY] });
    },
    onError: (err: unknown) => {
      const message =
        err instanceof Error && err.message ? err.message : "请稍后重试";
      toast.error(`创建失败：${message}`);
    },
  };
}

export function useCreateWriterDoc() {
  const qc = useQueryClient();
  return useMutation(createWriterDocMutationOptions(qc));
}

// ── Update doc (auto-save) ───────────────────────────────────────────────

/**
 * Mutation options for updating a writer doc, extracted so the error-surfacing
 * behavior is unit-testable without a DOM (same approach as
 * `createWriterDocMutationOptions`). ``onError`` shows a toast — without it,
 * a failed save POST (403/503/500) was swallowed by React Query (default
 * mutation retry: 0) and the user lost content silently on a transient blip.
 *
 * Deliberately does NOT add `retry` to the mutation options — auto-retry
 * could double-save. A manual retry button in the header handles re-attempts.
 */
export function updateWriterDocMutationOptions(qc: QueryClient, docId: string) {
  return {
    mutationFn: (params: WriterDocUpdateParams) =>
      updateWriterDoc(docId, params),
    onSuccess: (data) => {
      // 2026-10-06 性能批：PUT 返回值直接写入缓存——修前 invalidate 触发全文
      // GET 重拉（保存一次 = 全文一上一下）；服务器数据即刚提交的数据。
      qc.setQueryData([WRITER_KEY, docId], data);
    },
    onError: (err: unknown) => {
      // B2-T5: WriterDocConflictError is handled by the local onError in
      // writer-page (shows the conflict toast with force-overwrite action).
      // The global onError must NOT show a second toast (R4 double-report).
      if (err instanceof WriterDocConflictError) return;
      const message =
        err instanceof Error && err.message ? err.message : "请稍后重试";
      toast.error(`保存失败：${message}`);
    },
  };
}

export function useUpdateWriterDoc(docId: string) {
  const qc = useQueryClient();
  return useMutation(updateWriterDocMutationOptions(qc, docId));
}

// ── Delete doc ───────────────────────────────────────────────────────────

/** Extracted for the same reason as updateWriterDocMutationOptions: a failed
 * delete (CSRF 403/500) must toast, not vanish (P1-11). */
export function deleteWriterDocMutationOptions(qc: QueryClient) {
  return {
    mutationFn: (id: string) => deleteWriterDoc(id),
    onSuccess: (_data, id) => {
      // 2026-10-06 性能批：对齐 threads 删除清理草稿的 B4-3 模式——修前草稿键
      // 永不清理，重建同 id 文档会复活已删内容的本地残稿。
      try {
        localStorage.removeItem(`writer_draft_${id}`);
      } catch {
        /* ignore */
      }
      void qc.invalidateQueries({ queryKey: [WRITER_KEY] });
    },
    onError: (err: unknown) => {
      const message =
        err instanceof Error && err.message ? err.message : "请稍后重试";
      toast.error(`删除失败：${message}`);
    },
  };
}

export function useDeleteWriterDoc() {
  const qc = useQueryClient();
  return useMutation(deleteWriterDocMutationOptions(qc));
}

// ── PPT Export (with polling) ────────────────────────────────────────────

export function useSubmitPptExport() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (params: PptExportParams) => submitPptExport(params),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [CONTENT_TASK_KEY] });
    },
  });
}

/**
 * B1-T2: Pure refetchInterval predicate for useContentTask.
 * Returns false (stop polling) for terminal statuses (ready/failed/cancelled),
 * 2000 (keep polling) otherwise. Extracted so the terminal-status set is
 * unit-testable without spying on useQuery (which fails in ESM).
 */
export function contentTaskRefetchInterval(query: {
  state: { data?: { status?: string } };
}): false | number {
  const status = query.state.data?.status;
  if (status === "ready" || status === "failed" || status === "cancelled") {
    return false;
  }
  return 2000;
}

export function useContentTask(taskId: string | undefined) {
  return useQuery({
    queryKey: [CONTENT_TASK_KEY, taskId],
    queryFn: () => getContentTask(taskId!),
    enabled: !!taskId,
    refetchInterval: contentTaskRefetchInterval,
  });
}

// ── Save reliability helpers (W5) ────────────────────────────────────────

/**
 * Pure helper: should the `beforeunload` listener warn the user?
 *
 * Warns when there are unflushed changes — either a debounce-pending save
 * (dirty) or an in-flight save (saving). When clean (no pending debounce, no
 * in-flight save), returns false so the listener is a no-op (doesn't block
 * navigation).
 *
 * Extracted as a pure function so the dirty-tracking logic is unit-testable
 * without a DOM or `addEventListener` registration.
 */
export function shouldWarnBeforeUnload(
  isDirty: boolean,
  isSaving: boolean,
): boolean {
  return isDirty || isSaving;
}
