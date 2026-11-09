"use client";

import { ChevronRight, Clock, History, Loader2, RotateCcw } from "lucide-react";
import { memo, useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { relativeTime } from "@/core/utils/datetime";
import {
  getDocVersion,
  listDocVersions,
  restoreDocVersion,
} from "@/core/writer/api";
import type { DocVersionBrief, DocVersionDetail } from "@/core/writer/types";
import { cn } from "@/lib/utils";

import { DiffView } from "./diff-view";

interface VersionPanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  docId: string;
  currentContent: string;
  onRestore: (content: string) => void;
}

export function reasonLabel(reason: string): string {
  switch (reason) {
    case "auto":
      return "自动保存";
    case "manual":
      return "手动保存";
    case "restore":
      return "版本回滚";
    case "destructive":
      return "覆盖保存";
    case "rename":
      return "重命名";
    case "retype":
      return "改类型";
    default:
      // 开发期信号（评审问题 6）：新增 snapshot reason 忘加映射时不再静默显示英文
      console.warn(`reasonLabel: unknown snapshot reason "${reason}"`);
      return reason;
  }
}

function VersionPanelInner({
  open,
  onOpenChange,
  docId,
  currentContent,
  onRestore,
}: VersionPanelProps) {
  const [versions, setVersions] = useState<DocVersionBrief[]>([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<DocVersionDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [restoring, setRestoring] = useState(false);
  // B5-T4 fix: request sequence for race condition — fast clicks on two
  // versions should only show the last one's detail.
  const selectSeqRef = useRef(0);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setSelected(null);
    listDocVersions(docId)
      .then(setVersions)
      .catch(() => {
        setVersions([]);
        toast.error("版本记录加载失败，请稍后重试");
      })
      .finally(() => setLoading(false));
  }, [open, docId]);

  const handleSelect = useCallback(
    async (v: DocVersionBrief) => {
      const seq = ++selectSeqRef.current;
      setDetailLoading(true);
      try {
        const detail = await getDocVersion(docId, v.id);
        // B5-T4: discard stale response if a newer selection was made
        if (seq !== selectSeqRef.current) return;
        setSelected(detail);
      } catch {
        if (seq !== selectSeqRef.current) return;
        setSelected(null);
      } finally {
        if (seq === selectSeqRef.current) setDetailLoading(false);
      }
    },
    [docId],
  );

  const handleRestore = useCallback(async () => {
    if (!selected) return;
    setRestoring(true);
    try {
      const doc = await restoreDocVersion(docId, selected.id);
      onRestore(doc.content_json);
      onOpenChange(false);
    } catch (err) {
      const message =
        err instanceof Error && err.message ? err.message : "请稍后重试";
      toast.error(`回滚失败：${message}`);
    } finally {
      setRestoring(false);
    }
  }, [docId, selected, onRestore, onOpenChange]);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-[700px] sm:max-w-[700px]">
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            <History className="h-5 w-5 text-indigo-500" />
            版本历史
          </SheetTitle>
        </SheetHeader>
        <div className="flex min-h-0 flex-1 overflow-hidden">
          {/* Version list */}
          <div className="w-64 shrink-0 overflow-y-auto border-r border-[var(--border)] px-3 py-2">
            {loading ? (
              <div className="flex items-center justify-center py-8">
                <Loader2
                  size={16}
                  className="animate-spin text-[var(--muted-foreground)]"
                />
              </div>
            ) : versions.length === 0 ? (
              <p className="py-8 text-center text-xs text-[var(--muted-foreground)]">
                暂无版本记录
              </p>
            ) : (
              <div className="space-y-1">
                {versions.map((v) => (
                  <button
                    key={v.id}
                    onClick={() => handleSelect(v)}
                    className={cn(
                      "flex w-full items-center gap-2 rounded-md px-2 py-2 text-left transition-colors",
                      selected?.id === v.id
                        ? "bg-indigo-50 text-indigo-700 dark:bg-indigo-950/30 dark:text-indigo-300"
                        : "hover:bg-[var(--muted)]",
                    )}
                  >
                    <Clock
                      size={12}
                      className="shrink-0 text-[var(--muted-foreground)]"
                    />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1">
                        <span className="text-xs font-medium">
                          v{v.version_number}
                        </span>
                        <span className="text-[10px] text-[var(--muted-foreground)]">
                          {reasonLabel(v.snapshot_reason)}
                        </span>
                      </div>
                      <div className="text-[10px] text-[var(--muted-foreground)]">
                        {v.created_at ? relativeTime(v.created_at, "date") : ""}
                      </div>
                    </div>
                    <ChevronRight
                      size={12}
                      className="shrink-0 text-[var(--muted-foreground)]"
                    />
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Preview area */}
          <div className="flex min-w-0 flex-1 flex-col">
            {detailLoading ? (
              <div className="flex flex-1 items-center justify-center">
                <Loader2
                  size={20}
                  className="animate-spin text-[var(--muted-foreground)]"
                />
              </div>
            ) : selected ? (
              <>
                <div className="flex items-center justify-between border-b border-[var(--border)] px-4 py-2">
                  <div className="text-sm">
                    <span className="font-medium">
                      v{selected.version_number}
                    </span>
                    <span className="ml-2 text-xs text-[var(--muted-foreground)]">
                      {selected.title} ·{" "}
                      {selected.created_at
                        ? relativeTime(selected.created_at, "date")
                        : ""}
                    </span>
                  </div>
                  <Button
                    size="sm"
                    onClick={handleRestore}
                    disabled={restoring}
                  >
                    {restoring ? (
                      <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                    ) : (
                      <RotateCcw className="mr-1 h-3 w-3" />
                    )}
                    回滚到此版本
                  </Button>
                </div>
                <div className="flex-1 overflow-y-auto px-6 py-4">
                  {/* B5-T4: show diff between this version and current document */}
                  <DiffView
                    original={selected.content_json}
                    revised={currentContent}
                  />
                </div>
              </>
            ) : (
              <div className="flex flex-1 items-center justify-center text-sm text-[var(--muted-foreground)]">
                选择左侧版本查看内容
              </div>
            )}
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}

// 2026-10-06 性能批：memo 化——markdown 变化（击键）时 props 未变的子树跳过重渲
export const VersionPanel = memo(VersionPanelInner);
