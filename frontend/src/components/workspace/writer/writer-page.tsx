"use client";

import { useQueryClient } from "@tanstack/react-query";
import type { Editor } from "@tiptap/react";
import {
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  Download,
  Eraser,
  Eye,
  FileText,
  History,
  Loader2,
  PanelRight,
  PenLine,
  Plus,
  RotateCcw,
  Search,
  Share2,
  Sparkles,
  Trash2,
  WandSparkles,
} from "lucide-react";
import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { BetaBadge } from "@/components/workspace/beta-badge";
import {
  DialogProvider,
  useConfirm,
  usePrompt,
} from "@/components/workspace/dialog-provider";
import { EmptyState } from "@/components/workspace/empty-state";
import { relativeTime } from "@/core/utils/datetime";
import {
  getWriterDoc,
  updateWriterDoc,
  WriterDocConflictError,
} from "@/core/writer/api";
import { countWords, stripMarkdownForCount } from "@/core/writer/count-words";
import {
  DOC_TYPE_CONFIGS,
  DOC_TYPE_ORDER,
} from "@/core/writer/doc-type-config";
import { extractMarkdownFromAiOutput } from "@/core/writer/extract-markdown";
import {
  shouldWarnBeforeUnload,
  useDeleteWriterDoc,
  useUpdateWriterDoc,
  useWriterDoc,
  useWriterDocs,
} from "@/core/writer/hooks";
import type { DocType } from "@/core/writer/types";
import { cn } from "@/lib/utils";

import { clearDocument } from "./clear-document";
import { ContinueWritingButton } from "./continue-writing-button";
import { EditorToolbar } from "./editor-toolbar";
import { ExtensionPanel } from "./extensions-panel";
import { FillConfirmDialog } from "./fill-confirm-dialog";
import { FromScratchPanel } from "./from-scratch-panel";
import { FullDraftModal } from "./full-draft-modal";
import { MarkdownPreview } from "./markdown-preview";
import { NewDocDialog } from "./new-doc-dialog";
import { PdfExportDialog } from "./pdf-export-dialog";
import { PptExportDialog } from "./ppt-export-dialog";
import { SelectionPopover } from "./selection-popover";
import { ShareDialog } from "./share-dialog";
import { VersionPanel } from "./version-panel";

// 2026-10-06 性能批：tiptap 全家桶动态导入——进入编辑态才加载（列表/预览路径不背）
const TiptapEditor = dynamic(
  () => import("./tiptap-editor").then((m) => m.TiptapEditor),
  {
    ssr: false,
    loading: () => (
      <div className="flex h-40 items-center justify-center text-xs text-[var(--muted-foreground)]">
        加载编辑器...
      </div>
    ),
  },
);

// ── Relative time ──────────────────────────────────────────────────────────

// ── Force-snapshot helpers (W11 — §9.3 destructive-op undo guarantee) ──────
// Pure functions extracted for DOM-free unit testing.

/** Whether a content-apply method should force a pre-apply version snapshot.
 * Destructive ops (fullDraftApply/fillEditor/replaceText) wholesale-replace
 * content and need a guaranteed undo point. insertBelow is non-destructive
 * (original content survives) and must NOT snapshot. */
export function isDestructiveApply(method: string): boolean {
  return (
    method === "fullDraftApply" ||
    method === "fillEditor" ||
    method === "replaceText"
  );
}

/** Read the force-snapshot flag from a ref and reset it to false.
 * Returns the value that was set so the caller can pass it in the PUT body.
 * Resetting ensures only the *next* save after a destructive op carries it. */
export function consumeForceSnapshot(ref: { current: boolean }): boolean {
  const val = ref.current;
  ref.current = false;
  return val;
}

// ── Fill-into-editor confirm helpers (W17 — §5.2E) ───────────────────────
// Pure functions extracted for DOM-free unit testing.
// Defect §5.1.5: handleFillEditor wholesale-replaced editor content without
// confirm. Now: if the editor is non-empty, show a 3-option dialog
// (替换/追加/取消); if empty, fill directly.

/** The user's choice when the fill-into-editor confirm dialog is shown. */
export type FillChoice = "replace" | "append" | "cancel";

/** Whether the editor has real content → should show the confirm dialog.
 * Returns true if there's any non-whitespace content. */
export function shouldConfirmFill(currentMarkdown: string): boolean {
  return currentMarkdown.trim().length > 0;
}

/** Build the appended content: existing + "\n\n" + newMarkdown.
 * The blank-line separator keeps the appended content as a new block. */
export function buildAppendContent(
  existing: string,
  newMarkdown: string,
): string {
  return `${existing}\n\n${newMarkdown}`;
}

/** Dispatch the fill action based on the user's choice.
 * Calls replace() for "replace", append() for "append", nothing for "cancel". */
export function dispatchFillAction(
  choice: FillChoice,
  handlers: { replace: () => void; append: () => void },
): void {
  if (choice === "replace") {
    handlers.replace();
  } else if (choice === "append") {
    handlers.append();
  }
  // "cancel" → no-op
}

/** Whether a pre-apply force-snapshot should be set for the given fill choice.
 * Replace is destructive (wholesale overwrite) → snapshot for undo point.
 * Append is non-destructive (existing survives) → no snapshot (like insertBelow).
 * Cancel is a no-op. */
export function shouldForceSnapshotForFill(choice: FillChoice): boolean {
  return choice === "replace";
}

// ── Title derivation (W27 — §9.4 title consistency) ─────────────────────
// Pure function extracted for DOM-free testing.
// Defect §9.4: the stored `title` was never synced from the first H1 heading
// — the editor showed the H1 via `displayTitle` but the list showed "未命名
// 文章" because the stored title stayed at the creation default. The autosave
// now writes the derived title when it differs from the stored value.

/**
 * Derive a display/storage title from the first `# H1` heading in the
 * markdown. Uses the same regex as `displayTitle` (`/^#\s+(.+)$/m`) — a
 * single-`#` ATX heading at the start of a line. H2+ (`## …`) and mid-line
 * `#` are not matched. If no H1 is found, returns `fallback` (the current
 * stored title) so the caller can detect "no change" and skip the write.
 */
export function deriveTitleFromMarkdown(
  markdown: string,
  fallback: string,
): string {
  const headingMatch = /^#\s+(.+)$/m.exec(markdown);
  return headingMatch?.[1] ?? fallback;
}

/**
 * P1-7: whether this save should overwrite the stored title with the H1-derived
 * value. Returns false when the user has manually renamed (don't clobber their
 * title) or when the derived value already equals the stored title (no-op write).
 */
export function shouldDeriveTitleFromH1(
  content: string,
  currentTitle: string,
  manuallyRenamed: boolean,
): boolean {
  if (manuallyRenamed) return false;
  // B2-T6: only derive for default "未命名" titles. A manually set title
  // (even across sessions) should never be reverted by H1 derivation.
  if (!currentTitle.startsWith("未命名")) return false;
  return deriveTitleFromMarkdown(content, currentTitle) !== currentTitle;
}

// ── List-preview markdown strip (W28 — §9.5 full strip) ─────────────────
// Pure function extracted for DOM-free testing.
// Defect §9.5: the list preview used `.replace(/[#*\n]/g, " ")` — strips
// ONLY `#`, `*`, `\n`, so links/images/tables/inline-code showed as 乱码.
// Now reuses `stripMarkdownForCount` (full strip: links→text, images→alt,
// tables→cell text, inline code→text, headings/list markers/bold/italic
// stripped) then collapses whitespace runs to single spaces + trims.

/**
 * Strip markdown markup to readable prose for list-card previews.
 * Delegates to `stripMarkdownForCount` (which already handles all markdown
 * constructs) then collapses whitespace runs to a single space and trims
 * — the count function leaves newlines/multi-spaces intact because it
 * counts by word boundaries, but the preview needs a single-line string.
 */
export function stripMarkdownForPreview(markdown: string): string {
  return stripMarkdownForCount(markdown).replace(/\s+/g, " ").trim();
}

// ── Props ──────────────────────────────────────────────────────────────────

interface WriterPageProps {
  docId?: string;
}

// ── Document List Page ─────────────────────────────────────────────────────

function WriterDocList() {
  const router = useRouter();
  const qc = useQueryClient();
  // B5-T2 fix: search/filter/sort/pagination state
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [docTypeFilter, setDocTypeFilter] = useState<string>("all");
  const [sortBy, setSortBy] = useState<string>("updated_at");
  const [sortDir, setSortDir] = useState<string>("desc");
  const [offset, setOffset] = useState(0);
  const PAGE_SIZE = 20;

  // Debounce search input (300ms) + reset pagination on change
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearch(search);
      setOffset(0);
    }, 300);
    return () => clearTimeout(timer);
  }, [search]);

  const docsQuery = useWriterDocs({
    limit: PAGE_SIZE,
    offset,
    search: debouncedSearch || undefined,
    doc_type: docTypeFilter !== "all" ? docTypeFilter : undefined,
    sort_by: sortBy as "updated_at" | "created_at" | "title",
    sort_dir: sortDir as "asc" | "desc",
  });

  const deleteMut = useDeleteWriterDoc();
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const [newDocOpen, setNewDocOpen] = useState(false);
  // B5-T3: inline rename state
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");

  const handleCreated = useCallback(
    (docId: string) => {
      router.push(`/workspace/writer/${docId}`);
    },
    [router],
  );

  // B5-T3: inline rename from list card -- re-get for fresh version
  const handleRenameSubmit = useCallback(
    async (docId: string) => {
      const newTitle = renameValue.trim();
      if (!newTitle) {
        setRenamingId(null);
        return;
      }
      try {
        const fresh = await getWriterDoc(docId);
        await updateWriterDoc(docId, {
          title: newTitle,
          base_version: fresh.version,
        });
        toast.success("重命名成功");
        // I10 fix: invalidate list cache so the new title shows immediately
        void qc.invalidateQueries({ queryKey: ["writerDocs"] });
      } catch (err) {
        if (err instanceof WriterDocConflictError) {
          toast.error("文档已在别处修改，刷新后再改名");
        } else {
          toast.error("重命名失败");
        }
      }
      setRenamingId(null);
    },
    [renameValue, qc],
  );

  const handleDelete = useCallback(
    async (docId: string) => {
      await deleteMut.mutateAsync(docId);
      setPendingDeleteId(null);
    },
    [deleteMut],
  );

  // B5-T2: extract items from paginated response
  const docs = docsQuery.data?.items ?? [];
  const hasMore = docsQuery.data?.has_more ?? false;
  return (
    <div className="flex h-full min-h-full flex-col overflow-hidden">
      {/* Header */}
      <header className="flex shrink-0 items-center justify-between border-b border-[var(--border)] px-6 py-3">
        <div className="flex items-center gap-3">
          <PenLine size={18} className="text-[var(--muted-foreground)]" />
          <div>
            <div className="flex items-center gap-2 text-sm font-semibold text-[var(--foreground)]">
              写作
              <BetaBadge />
            </div>
            <div className="text-xs text-[var(--muted-foreground)]">
              创建和编辑各类内容，AI 辅助写作，多格式导出
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" onClick={() => setNewDocOpen(true)}>
            <Plus className="mr-1 h-3.5 w-3.5" />
            新建文档
          </Button>
        </div>
      </header>

      {/* B5-T2 fix: search / filter / sort toolbar */}
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-[var(--border)] px-6 py-2.5">
        <div className="relative min-w-[160px] flex-1">
          <Search
            size={14}
            className="absolute top-1/2 left-2.5 -translate-y-1/2 text-[var(--muted-foreground)]"
          />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索文档标题..."
            maxLength={100}
            className="h-8 w-full rounded-md border border-[var(--border)] bg-[var(--background)] pr-3 pl-8 text-sm outline-none focus:border-[var(--primary)]"
          />
        </div>
        <Select
          value={docTypeFilter}
          onValueChange={(v) => {
            setDocTypeFilter(v);
            setOffset(0);
          }}
        >
          <SelectTrigger className="h-8 w-[120px] text-sm">
            <SelectValue placeholder="类型" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部类型</SelectItem>
            {DOC_TYPE_ORDER.map((dt) => (
              <SelectItem key={dt} value={dt}>
                {DOC_TYPE_CONFIGS[dt]?.label ?? dt}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select
          value={`${sortBy}:${sortDir}`}
          onValueChange={(v) => {
            const [by, dir] = v.split(":");
            setSortBy(by ?? "updated_at");
            setSortDir(dir ?? "desc");
            setOffset(0);
          }}
        >
          <SelectTrigger className="h-8 w-[140px] text-sm">
            <SelectValue placeholder="排序" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="updated_at:desc">最近更新</SelectItem>
            <SelectItem value="updated_at:asc">最久未更新</SelectItem>
            <SelectItem value="created_at:desc">最新创建</SelectItem>
            <SelectItem value="created_at:asc">最早创建</SelectItem>
            <SelectItem value="title:asc">标题 A-Z</SelectItem>
            <SelectItem value="title:desc">标题 Z-A</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Content */}
      <main className="flex-1 overflow-y-auto px-6 py-6">
        {docsQuery.isLoading ? (
          <div className="flex items-center justify-center gap-2 py-20 text-sm text-[var(--muted-foreground)]">
            <Loader2 size={16} className="animate-spin" />
            加载中...
          </div>
        ) : docsQuery.isError ? (
          <div className="flex flex-col items-center justify-center gap-2 py-20 text-sm text-[var(--muted-foreground)]">
            <p>文档列表加载失败，请检查网络后刷新重试</p>
          </div>
        ) : docs.length === 0 ? (
          /* DSN-005（2026-09-14）：空态区分搜索——有搜索词时「没有找到匹配的文档」
             + 清除搜索 CTA；无搜索词保持新建文档三件套。收敛到 EmptyState。 */
          <EmptyState
            className="rounded-xl border border-dashed border-[var(--border)] bg-[var(--secondary)]/30 px-8 py-16"
            icon={PenLine}
            title={debouncedSearch ? "没有找到匹配的文档" : "还没有文档"}
            description={debouncedSearch ? undefined : "开始创建你的第一篇内容"}
            action={
              debouncedSearch ? (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    setSearch("");
                  }}
                >
                  清除搜索
                </Button>
              ) : (
                <Button size="sm" onClick={() => setNewDocOpen(true)}>
                  <Plus className="mr-1 h-3.5 w-3.5" />
                  新建文档
                </Button>
              )
            }
          />
        ) : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {docs.map((doc) => {
              const isPendingDelete = pendingDeleteId === doc.id;
              const isDeleting =
                deleteMut.isPending && deleteMut.variables === doc.id;
              // W28 §9.5: full markdown-strip (links→text, images→alt,
              // tables→cell text, inline code→text) — the old
              // `.replace(/[#*\n]/g, " ")` left link URLs, image syntax,
              // table pipes, and code backticks as 乱码 in the list card.
              const previewText =
                typeof doc.content_json === "string"
                  ? stripMarkdownForPreview(doc.content_json).slice(0, 160)
                  : "空文档";

              return (
                <div
                  key={doc.id}
                  role="button"
                  tabIndex={0}
                  onClick={() => router.push(`/workspace/writer/${doc.id}`)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      router.push(`/workspace/writer/${doc.id}`);
                    }
                  }}
                  className="group relative flex h-44 cursor-pointer flex-col rounded-xl border border-[var(--border)] bg-[var(--secondary)]/40 p-4 transition-all hover:border-[var(--primary)]/40 hover:bg-[var(--secondary)]/70 hover:shadow-sm"
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex min-w-0 items-start gap-2">
                      <FileText
                        size={14}
                        className="mt-0.5 shrink-0 text-[var(--muted-foreground)]"
                      />
                      <div className="min-w-0">
                        <div className="truncate text-sm font-medium text-[var(--foreground)]">
                          {renamingId === doc.id ? (
                            <input
                              value={renameValue}
                              onChange={(e) => setRenameValue(e.target.value)}
                              onClick={(e) => e.stopPropagation()}
                              onKeyDown={(e) => {
                                // P2-2/BUG-004：入口即停冒泡——修前 Enter 冒泡到
                                // 卡片 onKeyDown 触发误导航进编辑器（Space 同理）。
                                e.stopPropagation();
                                if (e.key === "Enter") {
                                  e.preventDefault();
                                  void handleRenameSubmit(doc.id);
                                } else if (e.key === "Escape") {
                                  setRenamingId(null);
                                }
                              }}
                              onKeyUp={(e) => e.stopPropagation()}
                              onKeyPress={(e) => e.stopPropagation()}
                              onBlur={() => void handleRenameSubmit(doc.id)}
                              autoFocus
                              className="w-full rounded border border-[var(--primary)]/40 bg-[var(--background)] px-1 py-0.5 text-sm outline-none focus:border-[var(--primary)]"
                            />
                          ) : (
                            doc.title || "未命名文档"
                          )}
                        </div>
                        <div className="flex items-center gap-1.5">
                          <span className="text-[10px] text-[var(--muted-foreground)]/70">
                            {relativeTime(doc.updated_at)}
                          </span>
                          {doc.doc_type && doc.doc_type !== "general" && (
                            <span className="rounded bg-indigo-500/10 px-1 py-0.5 text-[9px] font-medium text-indigo-600 dark:text-indigo-400">
                              {DOC_TYPE_CONFIGS[doc.doc_type as DocType]
                                ?.label ?? doc.doc_type}
                            </span>
                          )}
                        </div>
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-0.5">
                      {/* B5-T3 fix: rename button as sibling element (was dead code inside onClick) */}
                      <button
                        onClick={(event) => {
                          event.stopPropagation();
                          setRenamingId(doc.id);
                          setRenameValue(doc.title || "");
                        }}
                        title="重命名"
                        className="shrink-0 rounded-md p-1 text-[var(--muted-foreground)]/60 opacity-0 transition-colors group-hover:opacity-100 hover:bg-[var(--muted)] hover:text-[var(--foreground)]"
                      >
                        <PenLine size={13} />
                      </button>
                      <button
                        onClick={(event) => {
                          event.stopPropagation();
                          if (isPendingDelete) {
                            void handleDelete(doc.id);
                          } else {
                            setPendingDeleteId(doc.id);
                          }
                        }}
                        disabled={isDeleting}
                        title={
                          isPendingDelete ? "再次点击确认删除" : "删除文档"
                        }
                        className={cn(
                          "shrink-0 rounded-md p-1 transition-colors disabled:opacity-50",
                          isPendingDelete
                            ? "bg-rose-500/15 text-rose-600 dark:text-rose-400"
                            : "text-[var(--muted-foreground)]/60 opacity-0 group-hover:opacity-100 hover:bg-rose-500/10 hover:text-rose-600 dark:hover:text-rose-400",
                        )}
                      >
                        {isDeleting ? (
                          <Loader2 size={13} className="animate-spin" />
                        ) : (
                          <Trash2 size={13} />
                        )}
                      </button>
                    </div>
                  </div>
                  <p className="mt-3 line-clamp-4 flex-1 text-xs leading-relaxed text-[var(--muted-foreground)]">
                    {previewText || "空文档"}
                  </p>
                </div>
              );
            })}
          </div>
        )}
        {/* B5-T2 fix: pagination controls driven by has_more */}
        {!docsQuery.isLoading && !docsQuery.isError && docs.length > 0 && (
          <div className="flex items-center justify-center gap-4 pt-4 pb-2">
            <Button
              variant="outline"
              size="sm"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            >
              <ChevronLeft size={14} className="mr-1" />
              上一页
            </Button>
            <span className="text-xs text-[var(--muted-foreground)]">
              第 {Math.floor(offset / PAGE_SIZE) + 1} 页
            </span>
            <Button
              variant="outline"
              size="sm"
              disabled={!hasMore}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              下一页
              <ChevronRight size={14} className="ml-1" />
            </Button>
          </div>
        )}
      </main>

      <NewDocDialog
        open={newDocOpen}
        onOpenChange={setNewDocOpen}
        onCreated={handleCreated}
      />
    </div>
  );
}

// ── Editor Page ────────────────────────────────────────────────────────────

function WriterEditor({ docId }: { docId: string }) {
  const router = useRouter();
  const confirm = useConfirm();
  const prompt = usePrompt();

  // Data
  const docQuery = useWriterDoc(docId);
  const updateMut = useUpdateWriterDoc(docId);

  // Editor state — ref for imperative access, state for re-render
  const [markdown, setMarkdown] = useState("");
  const editorRef = useRef<Editor | null>(null);
  const [editor, setEditor] = useState<Editor | null>(null);
  const [showPreview, setShowPreview] = useState(false);

  // Title editing
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const titleInputRef = useRef<HTMLInputElement>(null);

  // Modals
  const [pptOpen, setPptOpen] = useState(false);
  const [pdfOpen, setPdfOpen] = useState(false);
  const [fullDraftOpen, setFullDraftOpen] = useState(false);
  const [versionOpen, setVersionOpen] = useState(false);
  // P2-3/BUG-005：分享弹层开关
  const [shareOpen, setShareOpen] = useState(false);

  // Publish panel (Phase 2: voice + video)
  const [publishOpen, setPublishOpen] = useState(false);

  // From-scratch panel (Phase 4: conversational creation)
  const [scratchOpen, setScratchOpen] = useState(false);

  // Fill-into-editor confirm (W17 — §5.2E): when the editor is non-empty and
  // the user clicks "填入编辑器", hold the pending markdown and show a 3-option
  // dialog (替换/追加/取消) instead of wholesale overwriting.
  const [fillConfirmOpen, setFillConfirmOpen] = useState(false);
  const pendingFillMarkdownRef = useRef("");

  // Auto-save
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  const [isSaving, setIsSaving] = useState(false);
  const [lastSaved, setLastSaved] = useState(false);
  const [saveError, setSaveError] = useState(false);
  // P2-10: a 409 conflict freezes autosave until the user reloads — otherwise
  // the stale tab keeps hammering a losing PUT every 3s (and would silently
  // overwrite the newer content if we ever retried with a fresh base).
  const [saveConflict, setSaveConflict] = useState(false);

  // Dirty tracking for beforeunload — true when debounce-pending (content
  // changed but save not yet fired). Set true in handleMarkdownChange, false
  // when the debounce fires (isSaving then tracks the in-flight save).
  const dirtyRef = useRef(false);

  // Force-snapshot flag: set true by destructive applies (fullDraftApply,
  // fillEditor, replaceText) so the next autosave PUT carries force_snapshot
  // → backend snapshots the pre-apply state (undo point) with reason="destructive"
  // (bypasses the 30s auto-throttle). Consumed+reset in triggerSave.
  const forceSnapshotNextRef = useRef(false);

  // P1-7: title manual-rename flag. Set true when the user renames via the
  // title bar; while true, autosave skips H1->title derivation so the manual
  // name isn't reverted. Reset on doc switch (WriterEditor is keyed by docId,
  // so this ref naturally resets via remount).
  const titleManuallySetRef = useRef(false);

  const currentDoc = docQuery.data;
  const docTitle = currentDoc?.title ?? "加载中...";
  const docType = currentDoc?.doc_type ?? "general";
  const typeConfig = DOC_TYPE_CONFIGS[docType] ?? DOC_TYPE_CONFIGS.general;

  const handleDocTypeChange = useCallback(
    (newType: DocType) => {
      // P2-2/BUG-004：加载未完成不发 PUT——base_version=undefined 会走后端
      // legacy 无 CAS 路径（无条件覆盖）。
      if (!currentDoc) return;
      updateMut.mutate({
        doc_type: newType,
        base_version: currentDoc?.version,
      });
    },
    [updateMut, currentDoc],
  );

  // Stats
  const wordCount = useMemo(() => countWords(markdown), [markdown]);
  const charCount = useMemo(() => markdown.length, [markdown]);

  // ── Load document content (with draft version safety) ──────────────────

  const isFirstLoad = useRef(true);
  // P2-2/BUG-004（2026-09-14）：加载门控——首载内容确定前编辑器不挂载。
  // 修前 currentDoc 未就绪时 TiptapEditor 已挂载可交互，加载窗口内的空内容
  // onUpdate 会以 base_version=undefined 触发空保存（后端 legacy 无 CAS 覆盖）。
  const [docLoaded, setDocLoaded] = useState(false);
  useEffect(() => {
    if (!currentDoc || !isFirstLoad.current) return;
    void (async () => {
      try {
        const serverContent =
          typeof currentDoc.content_json === "string"
            ? currentDoc.content_json
            : "";
        const serverVersion = currentDoc.updated_at;

        let content = serverContent;

        try {
          const raw = localStorage.getItem(`writer_draft_${docId}`);
          if (raw) {
            const draft = JSON.parse(raw) as {
              content: string;
              ts: number;
              baseVersion?: string;
              baseVersionInt?: number;
            };
            const hasContent = draft.content?.trim();

            // B2-T4: prefer baseVersionInt (number) for precise version comparison.
            // Fall back to baseVersion (string, old format = updated_at) for
            // compatibility with drafts saved by the old frontend.
            if (
              typeof draft.baseVersionInt === "number" &&
              currentDoc?.version === draft.baseVersionInt &&
              hasContent
            ) {
              content = draft.content;
            } else if (
              typeof draft.baseVersionInt === "number" &&
              currentDoc?.version !== draft.baseVersionInt &&
              hasContent
            ) {
              const draftTs = draft.ts;
              const serverTs = new Date(serverVersion).getTime();
              if (draftTs > serverTs) {
                const ok = await confirm({
                  title: "恢复草稿",
                  description: "检测到本地有较新的草稿，是否恢复？",
                });
                content = ok ? draft.content : serverContent;
              }
            } else if (
              typeof draft.baseVersion === "string" &&
              draft.baseVersion === serverVersion &&
              hasContent
            ) {
              content = draft.content;
            } else if (
              typeof draft.baseVersion === "string" &&
              draft.baseVersion !== serverVersion &&
              hasContent
            ) {
              const draftTs = draft.ts;
              const serverTs = new Date(serverVersion).getTime();
              if (draftTs > serverTs) {
                const ok = await confirm({
                  title: "恢复草稿",
                  description: "检测到本地有较新的草稿，是否恢复？",
                });
                content = ok ? draft.content : serverContent;
              }
            }
          }
        } catch {
          try {
            const localDraft = localStorage.getItem(`writer_draft_${docId}`);
            if (localDraft) content = localDraft;
          } catch {
            /* ignore */
          }
        }

        setMarkdown(content);
        isFirstLoad.current = false;
      } finally {
        // review-③（2026-09-14）：docLoaded 必置位不变量。confirm 的 reject 本就被
        // 上方外层 catch 兜底（实测不卡占位，守卫测试在案）；finally 防御未来在
        // catch 与收尾之间新增的抛点把编辑器永久卡在加载占位。
        setDocLoaded(true);
      }
    })();
  }, [currentDoc, docId, confirm]);

  // ── Auto-save + localStorage buffer (with baseVersion) ────────────────

  const triggerSave = useCallback(
    (content: string, immediate = false) => {
      clearTimeout(saveTimerRef.current);
      setSaveError(false); // new attempt clears prior error

      const doSave = () => {
        if (saveConflict) return; // 冲突未解除前不再自动保存（P2-10）
        // P2-2/BUG-004：version 非整数（未加载完成=undefined / 旧格式字符串版本）
        // → 拒绝保存——base_version 缺失会让后端走 legacy 无 CAS 路径（无条件覆盖）。
        if (typeof currentDoc?.version !== "number") return;
        dirtyRef.current = false; // debounce fired — isSaving tracks in-flight
        setIsSaving(true);
        const forceSnapshot = consumeForceSnapshot(forceSnapshotNextRef);
        // W27 §9.4: sync the stored title from the first H1 heading. The
        // editor's displayTitle already shows the H1, but the stored `title`
        // field was never updated — so the list showed "未命名文章" for docs
        // whose body starts with `# 标题`. Only include `title` when the
        // derived value differs from the stored title (avoid unnecessary
        // writes on every autosave).
        const currentTitle = currentDoc?.title ?? "";
        const saveBody: {
          content_json: string;
          force_snapshot: boolean;
          title?: string;
          base_version?: number;
        } = {
          content_json: content,
          force_snapshot: forceSnapshot,
          base_version: currentDoc?.version,
        };
        // P1-7: 仅在用户未手动改名时，才用 H1 派生 title 回写。
        if (
          shouldDeriveTitleFromH1(
            content,
            currentTitle,
            titleManuallySetRef.current,
          )
        ) {
          saveBody.title = deriveTitleFromMarkdown(content, currentTitle);
        }
        updateMut.mutate(saveBody, {
          onSuccess: (updated) => {
            setIsSaving(false);
            setLastSaved(true);
            setSaveError(false);
            try {
              const newVersion =
                updated?.updated_at ?? currentDoc?.updated_at ?? "";
              localStorage.setItem(
                `writer_draft_${docId}`,
                JSON.stringify({
                  content,
                  ts: Date.now(),
                  baseVersionInt: updated?.version ?? currentDoc?.version,
                  baseUpdatedAt: newVersion,
                }),
              );
            } catch {
              try {
                localStorage.removeItem(`writer_draft_${docId}`);
              } catch {
                /* ignore */
              }
            }
          },
          onError: (err) => {
            setIsSaving(false);
            if (err instanceof WriterDocConflictError) {
              setSaveConflict(true);
              // 必须给解除路径（评审问题 4）：没有 action 的话用户不刷新就
              // B2-T5: dual action -- force-overwrite or reload.
              // action prop accepts ReactNode, so we render two buttons.
              toast.error("保存冲突：文档已在其他窗口被修改", {
                id: "writer-save-conflict",
                duration: Number.POSITIVE_INFINITY,
                action: (
                  <div className="flex items-center gap-2">
                    <button
                      className="rounded bg-[var(--primary)] px-2 py-1 text-xs text-white"
                      onClick={async () => {
                        try {
                          const fresh = await getWriterDoc(docId);
                          updateMut.mutate(
                            {
                              content_json: content,
                              base_version: fresh.version,
                              force_snapshot: true,
                            },
                            {
                              onSuccess: () => {
                                setSaveConflict(false);
                                toast.success(
                                  "已强制覆盖（对方修改已存入版本历史）",
                                  {
                                    id: "writer-save-conflict",
                                  },
                                );
                              },
                              onError: () => {
                                toast.error("强制覆盖失败，请刷新重试", {
                                  id: "writer-save-conflict",
                                });
                              },
                            },
                          );
                        } catch {
                          toast.error("获取最新版本失败，请刷新重试", {
                            id: "writer-save-conflict",
                          });
                        }
                      }}
                    >
                      强制覆盖
                    </button>
                    <button
                      className="rounded border border-[var(--border)] px-2 py-1 text-xs text-[var(--foreground)]"
                      onClick={() => window.location.reload()}
                    >
                      重新加载
                    </button>
                  </div>
                ),
              });
            } else {
              setSaveError(true);
            }
          },
        });
      };

      if (immediate) {
        doSave();
      } else {
        saveTimerRef.current = setTimeout(doSave, 3000);
      }
    },
    [docId, updateMut, currentDoc, saveConflict],
  );

  // ── Editor ready callback ──────────────────────────────────────────────

  const handleEditorReady = useCallback((e: Editor) => {
    editorRef.current = e;
    setEditor(e);
  }, []);

  // 2026-10-06 性能批：选区订阅下沉至 VoicePanel（经 ExtensionPanel 透传
  // editor）——修前 selectedText state 在页面级，拖选/扩选每次整树重渲。

  // ── Markdown change → auto-save ────────────────────────────────────────

  // 2026-10-06 性能批：草稿 localStorage 写加 1000ms trailing 防抖——修前逐击键
  // 同步序列化全文（长文数千次/分钟的 JSON.stringify+setItem 主线开销）。
  // 代价：极端崩溃丢最后一秒输入（计划已评估可接受）；卸载/切文档时冲刷兜底。
  const draftWriteTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingDraftWriteRef = useRef<(() => void) | null>(null);
  const flushDraftWrite = useCallback(() => {
    if (draftWriteTimerRef.current !== null) {
      clearTimeout(draftWriteTimerRef.current);
      draftWriteTimerRef.current = null;
    }
    pendingDraftWriteRef.current?.();
    pendingDraftWriteRef.current = null;
  }, []);
  useEffect(
    () => () => {
      flushDraftWrite();
    },
    [flushDraftWrite],
  );

  const handleMarkdownChange = useCallback(
    (value: string) => {
      dirtyRef.current = true; // content changed — debounce pending
      // Buffer to localStorage (debounced trailing 1s)
      const writeDraft = () => {
        try {
          const baseVersion = currentDoc?.updated_at ?? "";
          const baseVersionInt = currentDoc?.version;
          localStorage.setItem(
            `writer_draft_${docId}`,
            JSON.stringify({
              content: value,
              ts: Date.now(),
              baseVersionInt,
              baseVersion,
            }),
          );
        } catch {
          /* ignore */
        }
      };
      pendingDraftWriteRef.current = writeDraft;
      if (draftWriteTimerRef.current !== null)
        clearTimeout(draftWriteTimerRef.current);
      draftWriteTimerRef.current = setTimeout(() => {
        draftWriteTimerRef.current = null;
        pendingDraftWriteRef.current = null;
        writeDraft();
      }, 1000);

      setMarkdown(value);
      setLastSaved(false);
      triggerSave(value);
    },
    [docId, currentDoc, triggerSave],
  );

  // ── beforeunload: warn on unflushed changes ───────────────────────────
  // When there's a debounce-pending save (dirty) or an in-flight save,
  // prevent the browser from silently abandoning the save. When clean,
  // the listener is a no-op (doesn't block navigation). The localStorage
  // draft buffer already persists content across reload + restores with a
  // confirm on next load, so this covers the mid-save-navigation case.
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (shouldWarnBeforeUnload(dirtyRef.current, isSaving)) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [isSaving]);

  // ── Set editor content via TipTap commands ─────────────────────────────

  const setContentViaEditor = useCallback((newMarkdown: string) => {
    const e = editorRef.current;
    if (!e) return;
    e.commands.setContent(newMarkdown);
  }, []);

  // ── Fill editor from FromScratchPanel ─────────────────────────────────

  const handleFillEditor = useCallback(
    (newMarkdown: string) => {
      // W17 §5.2E: if the editor is non-empty, confirm before filling
      // (替换/追加/取消). If empty, fill directly (nothing to lose).
      if (!shouldConfirmFill(markdown)) {
        setContentViaEditor(newMarkdown);
        setScratchOpen(false);
        return;
      }
      // Editor non-empty → show confirm dialog
      pendingFillMarkdownRef.current = newMarkdown;
      setFillConfirmOpen(true);
    },
    [markdown, setContentViaEditor],
  );

  // Handle the user's fill choice from the confirm dialog.
  const handleFillChoice = useCallback(
    (choice: FillChoice) => {
      // Set force-snapshot BEFORE applying (W11 — guarantees undo point for
      // destructive replace; append/cancel are non-destructive).
      if (shouldForceSnapshotForFill(choice)) {
        forceSnapshotNextRef.current = true;
      }
      dispatchFillAction(choice, {
        replace: () => setContentViaEditor(pendingFillMarkdownRef.current),
        append: () =>
          setContentViaEditor(
            buildAppendContent(markdown, pendingFillMarkdownRef.current),
          ),
      });
      setFillConfirmOpen(false);
      if (choice !== "cancel") {
        setScratchOpen(false);
      }
    },
    [markdown, setContentViaEditor],
  );

  // ── Replace text (from SelectionPopover) ──────────────────────────────

  const handleReplaceText = useCallback((replacement: string) => {
    const e = editorRef.current;
    if (!e) return;
    forceSnapshotNextRef.current = true; // destructive — guarantee undo point
    const { from, to } = e.state.selection;
    e.chain().focus().insertContentAt({ from, to }, replacement).run();
  }, []);

  // ── Insert text below selection (概要 default — original survives) ────
  // Used by SelectionPopover's 概要 op: the summary is inserted immediately
  // after the selection's `to` position on a new paragraph. The zero-width
  // range {from: to, to} inserts rather than replaces (TipTap insertContentAt).
  // The `\n\n` prefix keeps the summary as a new block: tiptap-markdown's
  // insertContentAt override parses the string as markdown, and normalizeInline
  // preserves the first <p> wrapper when content starts with \n\n (block insert).
  const handleInsertBelow = useCallback((text: string) => {
    const e = editorRef.current;
    if (!e) return;
    const { to } = e.state.selection;
    e.chain().focus().insertContentAt({ from: to, to }, `\n\n${text}`).run();
  }, []);

  // ── Full draft apply ──────────────────────────────────────────────────

  const handleFullDraftApply = useCallback(
    (newMarkdown: string) => {
      // B3-T4: Try to extract markdown from JSON-wrapped AI output
      // instead of immediately rejecting (user waited tens of seconds for nothing)
      const extracted = extractMarkdownFromAiOutput(newMarkdown);
      if (extracted === null) {
        // Valid JSON but no extractable content -> show error
        toast.error("AI 返回了非预期格式的内容，请重试或修改指令。");
        return;
      }
      if (extracted !== newMarkdown) {
        // Successfully extracted from JSON wrapper -> warn the user
        toast.warning("AI 返回了包装格式，已自动提取正文");
      }
      forceSnapshotNextRef.current = true; // destructive - guarantee undo point
      setContentViaEditor(extracted);
    },
    [setContentViaEditor],
  );

  // ── Clear document ────────────────────────────────────────────────────

  const handleClear = useCallback(() => {
    // Clearing is destructive → confirm; then clear the editor AND explicitly
    // persist the empty state (state + localStorage draft + autosave) so the
    // clear survives a reload — the old impl only called clearContent() and
    // relied on an implicit onUpdate→setMarkdown chain that could drop the
    // change. Uses the `editor` state (live instance), not editorRef.
    // W29 §9.6: confirm via the imperative DialogProvider (Promise<boolean>)
    // instead of window.confirm.
    void clearDocument({
      confirm: () =>
        confirm({
          title: "清空文档",
          description: "确定清空当前文档全部内容？可在编辑器内 Ctrl+Z 撤销。",
          variant: "destructive",
          confirmLabel: "清空",
        }),
      clearEditor: () => editor?.commands.clearContent(),
      persistEmpty: () => handleMarkdownChange(""),
    });
  }, [editor, handleMarkdownChange, confirm]);

  // ── Download Markdown ─────────────────────────────────────────────────

  const handleDownload = useCallback(() => {
    const blob = new Blob([markdown], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${docTitle}.md`;
    a.click();
    URL.revokeObjectURL(url);
  }, [markdown, docTitle]);

  // ── Insert image from DesignPanel ─────────────────────────────────────

  const handleInsertImage = useCallback((src: string, alt?: string) => {
    editorRef.current
      ?.chain()
      .focus()
      .setImage({ src, alt: alt ?? "" })
      .run();
  }, []);

  // ── Delete document ───────────────────────────────────────────────────

  const deleteMut = useDeleteWriterDoc();
  const handleDelete = useCallback(async () => {
    await deleteMut.mutateAsync(docId);
    router.push("/workspace/writer");
  }, [docId, deleteMut, router]);

  // ── Extract title from first heading ──────────────────────────────────

  const displayTitle = useMemo(
    () => deriveTitleFromMarkdown(markdown, docTitle),
    [markdown, docTitle],
  );

  // ── Title editing ─────────────────────────────────────────────────────

  const startEditingTitle = useCallback(() => {
    setTitleDraft(docTitle);
    setIsEditingTitle(true);
    setTimeout(() => titleInputRef.current?.focus(), 0);
  }, [docTitle]);

  const commitTitle = useCallback(() => {
    // P2-2/BUG-004：加载未完成不发 PUT（base_version=undefined → legacy 无 CAS）。
    if (!currentDoc) return;
    setIsEditingTitle(false);
    if (titleDraft && titleDraft !== docTitle) {
      // P1-7: 用户手动改名，停用 H1->title 派生回写，避免下一次 autosave 打回。
      titleManuallySetRef.current = true;
      // B2-T6: send base_version for CAS
      updateMut.mutate({
        title: titleDraft,
        base_version: currentDoc?.version,
      });
    }
  }, [titleDraft, docTitle, updateMut, currentDoc]);

  // ── Render ────────────────────────────────────────────────────────────

  // B1-T8: show error state when document load fails (was silently stuck
  // on "加载中..." with disabled buttons and empty editor).
  if (docQuery.isError) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3">
        <FileText size={32} className="text-[var(--muted-foreground)]/50" />
        <p className="text-sm text-[var(--muted-foreground)]">
          文档不存在或无权访问
        </p>
        <Button
          variant="outline"
          size="sm"
          onClick={() => router.push("/workspace/writer")}
        >
          返回列表
        </Button>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <header className="flex shrink-0 items-center justify-between border-b border-[var(--border)] px-4 py-1.5">
        <div className="flex min-w-0 items-center gap-3 text-sm text-[var(--muted-foreground)]">
          <button
            onClick={() => router.push("/workspace/writer")}
            className="inline-flex shrink-0 items-center gap-1 rounded-md px-1.5 py-0.5 text-xs font-medium text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)] hover:text-[var(--foreground)]"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            <span>文档</span>
          </button>
          <span className="text-[var(--muted-foreground)]/40">/</span>
          {isEditingTitle ? (
            <input
              ref={titleInputRef}
              value={titleDraft}
              onChange={(e) => setTitleDraft(e.target.value)}
              onBlur={commitTitle}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  commitTitle();
                } else if (e.key === "Escape") {
                  setIsEditingTitle(false);
                }
              }}
              maxLength={120}
              className="max-w-[24rem] min-w-0 flex-1 rounded-md border border-[var(--primary)]/40 bg-[var(--background)] px-2 py-0.5 text-sm font-medium text-[var(--foreground)] outline-none focus:border-[var(--primary)] focus:ring-1 focus:ring-[var(--primary)]/30"
            />
          ) : (
            <span
              role="button"
              tabIndex={0}
              onDoubleClick={startEditingTitle}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === "F2") {
                  e.preventDefault();
                  startEditingTitle();
                }
              }}
              title="双击重命名"
              className="min-w-0 cursor-text truncate rounded-md border border-transparent px-2 py-0.5 text-sm font-medium text-[var(--foreground)] transition-colors hover:bg-[var(--muted)]/60 focus:ring-1 focus:ring-[var(--primary)]/30 focus:outline-none"
            >
              {displayTitle}
            </span>
          )}
          <Select
            value={docType}
            onValueChange={(v) => handleDocTypeChange(v as DocType)}
          >
            <SelectTrigger className="h-6 w-auto gap-1 border-none px-1.5 text-[10px] text-[var(--muted-foreground)] hover:text-[var(--foreground)]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {DOC_TYPE_ORDER.map((t) => (
                <SelectItem key={t} value={t} className="text-xs">
                  {DOC_TYPE_CONFIGS[t].label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <span className="hidden text-xs sm:inline">
            {wordCount} 词 · {charCount} 字符
          </span>
          {isSaving ? (
            <span className="hidden items-center gap-1 text-[10px] text-[var(--muted-foreground)]/70 sm:inline-flex">
              <Loader2 size={10} className="animate-spin" />
              保存中...
            </span>
          ) : saveError ? (
            <span className="hidden items-center gap-1 text-[10px] sm:inline-flex">
              <span className="text-rose-600 dark:text-rose-400">保存失败</span>
              <button
                onClick={() => triggerSave(markdown, true)}
                disabled={saveConflict}
                className="inline-flex items-center gap-0.5 rounded px-1 py-0.5 text-[10px] font-medium text-rose-600 transition-colors hover:bg-rose-500/10 disabled:cursor-not-allowed disabled:opacity-40 dark:text-rose-400"
                title={saveConflict ? "存在保存冲突，请先处理" : "重新保存"}
              >
                <RotateCcw size={10} />
                重试
              </button>
            </span>
          ) : lastSaved ? (
            <span className="hidden text-[10px] text-[var(--muted-foreground)]/60 sm:inline">
              已保存
            </span>
          ) : null}
        </div>
        <div className="flex items-center gap-1.5">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setShowPreview((v) => !v)}
            className={cn(
              "h-7 gap-1 px-2 text-xs",
              showPreview && "text-[var(--primary)]",
            )}
            title={showPreview ? "返回编辑" : "预览最终效果"}
          >
            <Eye size={13} />
            {showPreview ? "编辑" : "预览"}
          </Button>
          {/* P2-3/BUG-005（2026-09-14）：分享入口——组件与后端/前端 API 早已三方
              就绪，仅缺挂载点（全仓零 import）。 */}
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setShareOpen(true)}
            disabled={!currentDoc}
            className="h-7 gap-1 px-2 text-xs"
            title="分享文档"
          >
            <Share2 size={13} />
            分享
          </Button>
          <button
            onClick={handleClear}
            title="清空文档"
            className="rounded p-1.5 text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)] hover:text-[var(--foreground)]"
          >
            <Eraser size={16} />
          </button>
          <button
            onClick={handleDownload}
            title="导出 Markdown"
            className="rounded p-1.5 text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)] hover:text-[var(--foreground)]"
          >
            <Download size={16} />
          </button>
          <Button
            size="sm"
            variant="default"
            onClick={() => setFullDraftOpen(true)}
            className="h-7 gap-1 px-2.5 text-xs"
          >
            <WandSparkles size={13} />
            全文 AI 编辑
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setScratchOpen(true)}
            className="h-7 gap-1 px-2.5 text-xs"
          >
            <Sparkles className="h-3 w-3" />
            从零创作
          </Button>
          {typeConfig.exports.includes("ppt") && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => setPptOpen(true)}
              disabled={!currentDoc}
              className="h-7 gap-1 px-2.5 text-xs"
            >
              <FileText className="h-3 w-3" />
              导出 PPT
            </Button>
          )}
          {typeConfig.exports.includes("pdf") && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => setPdfOpen(true)}
              disabled={!currentDoc}
              className="h-7 gap-1 px-2.5 text-xs"
            >
              <FileText className="h-3 w-3" />
              导出 PDF
            </Button>
          )}
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setVersionOpen(true)}
            disabled={!currentDoc}
            className="h-7 gap-1 px-2 text-xs"
            title="版本历史"
          >
            <History className="h-3 w-3" />
          </Button>
          {(typeConfig.showVoicePanel || typeConfig.showDesignPanel) && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setPublishOpen((v) => !v)}
              className={cn(
                "h-7 gap-1 px-2 text-xs",
                publishOpen && "text-[var(--primary)]",
              )}
              title="创作工具"
            >
              <PanelRight className="h-3 w-3" />
              创作工具
            </Button>
          )}
          <button
            onClick={async () => {
              const ok = await confirm({
                title: "删除文档",
                description: `确定删除「${docTitle}」？`,
                variant: "destructive",
                confirmLabel: "删除",
              });
              if (ok) await handleDelete();
            }}
            title="删除文档"
            className="rounded p-1.5 text-[var(--muted-foreground)] transition-colors hover:bg-rose-500/10 hover:text-rose-600"
          >
            <Trash2 size={16} />
          </button>
        </div>
      </header>

      {/* Toolbar */}
      <div className="flex items-center gap-1 border-b border-[var(--border)]">
        <EditorToolbar editor={editor} prompt={prompt} />
        <ContinueWritingButton
          editor={editor}
          docType={docType}
          docTitle={docTitle}
          markdown={markdown}
        />
      </div>

      {/* Editor / Preview + Publish Panel */}
      <div className="relative flex min-h-0 flex-1">
        <div className="min-w-0 flex-1">
          {/* P2-2/BUG-004：docLoaded 门控——首载内容确定前渲染占位（无任何
              可输入元素），加载窗口不存在可交互空编辑器。 */}
          {!docLoaded ? (
            <div className="flex h-full flex-col items-center justify-center gap-3">
              <Loader2
                size={24}
                className="animate-spin text-[var(--muted-foreground)]/50"
              />
              <p className="text-sm text-[var(--muted-foreground)]">
                加载中...
              </p>
            </div>
          ) : showPreview ? (
            <div className="h-full overflow-y-auto p-5">
              <div
                className={cn(
                  "mx-auto",
                  typeConfig.allowMobilePreview && "max-w-[375px]",
                )}
              >
                <MarkdownPreview content={markdown || "_暂无内容_"} />
              </div>
              {typeConfig.allowMobilePreview && (
                <div className="mx-auto mt-2 max-w-[375px] text-center text-[10px] text-[var(--muted-foreground)]/60">
                  📱 手机预览模式（375px）
                </div>
              )}
            </div>
          ) : (
            <TiptapEditor
              content={markdown}
              onChange={handleMarkdownChange}
              onEditorReady={handleEditorReady}
              placeholder={`在此编写${typeConfig.label}...`}
            />
          )}
        </div>

        <ExtensionPanel
          docType={docType}
          typeConfig={typeConfig}
          markdown={markdown}
          editor={editor}
          docTitle={docTitle}
          open={publishOpen}
          onOpenChange={setPublishOpen}
          onInsertImage={handleInsertImage}
        />

        <FromScratchPanel
          docType={docType}
          docTitle={docTitle}
          onFillEditor={handleFillEditor}
          open={scratchOpen}
          onClose={() => setScratchOpen(false)}
        />
      </div>

      {/* SelectionPopover */}
      <SelectionPopover
        editor={editor}
        markdown={markdown}
        docTitle={docTitle}
        contentType={docType}
        onReplaceText={handleReplaceText}
        onInsertBelow={handleInsertBelow}
      />

      {/* Full Draft Modal */}
      <FullDraftModal
        open={fullDraftOpen}
        onOpenChange={setFullDraftOpen}
        markdown={markdown}
        contentType={docType}
        onApply={handleFullDraftApply}
      />

      {/* PPT Export Dialog */}
      <PptExportDialog
        open={pptOpen}
        onOpenChange={setPptOpen}
        docId={docId}
        docTitle={docTitle}
      />

      {/* B4-T3: PDF Export Dialog */}
      <PdfExportDialog
        open={pdfOpen}
        onOpenChange={setPdfOpen}
        docId={docId}
        docTitle={docTitle}
      />

      {/* Version History Panel */}
      <VersionPanel
        open={versionOpen}
        onOpenChange={setVersionOpen}
        docId={docId}
        currentContent={markdown}
        onRestore={(content) => {
          setContentViaEditor(content);
        }}
      />

      {/* P2-3/BUG-005：分享弹层（生成/复制/撤销，公开页只读） */}
      <ShareDialog
        open={shareOpen}
        onOpenChange={setShareOpen}
        docId={docId}
        docTitle={docTitle}
      />

      {/* Fill-into-editor Confirm (W17 — §5.2E) */}
      <FillConfirmDialog
        open={fillConfirmOpen}
        onOpenChange={setFillConfirmOpen}
        onReplace={() => handleFillChoice("replace")}
        onAppend={() => handleFillChoice("append")}
      />
    </div>
  );
}

// ── Main Export ─────────────────────────────────────────────────────────────

export function WriterPage({ docId }: WriterPageProps) {
  if (!docId) {
    return <WriterDocList />;
  }
  // W29 §9.6: mount DialogProvider so useConfirm/usePrompt work in the editor.
  return (
    <DialogProvider>
      <WriterEditor key={docId} docId={docId} />
    </DialogProvider>
  );
}
