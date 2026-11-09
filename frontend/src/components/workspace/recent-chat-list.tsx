"use client";

import {
  Download,
  FileJson,
  FileText,
  MoreHorizontal,
  Pencil,
  Pin,
  PinOff,
  Share2,
  Trash2,
} from "lucide-react";
import Link from "next/link";
import { useParams, usePathname, useRouter } from "next/navigation";
import { useCallback, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuAction,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import { getAPIClient } from "@/core/api";
import { useI18n } from "@/core/i18n/hooks";
import {
  exportThreadAsJSON,
  exportThreadAsMarkdown,
} from "@/core/threads/export";
import {
  useDeleteThread,
  usePinThread,
  useRenameThread,
  useThreads,
} from "@/core/threads/hooks";
import type { AgentThread, AgentThreadState } from "@/core/threads/types";
import {
  isThreadPinned,
  pathOfThread,
  sortPinnedThreads,
  titleOfThread,
} from "@/core/threads/utils";
import { env } from "@/env";
import { isIMEComposing } from "@/lib/ime";

export function RecentChatList() {
  const { t } = useI18n();
  const router = useRouter();
  const pathname = usePathname();
  const { thread_id: threadIdFromPath, agent_name: agentNameFromPath } =
    useParams<{
      thread_id: string;
      agent_name?: string;
    }>();
  const { data: threads = [] } = useThreads();
  // Server already sorts pinned-first, but the optimistic cache updates from
  // usePinThread land before the refetch — re-sort locally so the pin flip
  // moves the row immediately. Stable sort keeps the server order otherwise.
  const displayedThreads = useMemo(() => sortPinnedThreads(threads), [threads]);
  const { mutate: deleteThread } = useDeleteThread();
  const { mutate: renameThread } = useRenameThread();
  const { mutate: pinThread } = usePinThread();

  // Rename dialog state
  const [renameDialogOpen, setRenameDialogOpen] = useState(false);
  const [renameThreadId, setRenameThreadId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  // DEF-4：剪贴板被拒时待兜底展示的分享链接（null = 不弹）
  const [shareFallbackUrl, setShareFallbackUrl] = useState<string | null>(null);
  // P2-1/BUG-003（2026-09-14）：删除先弹确认层——修前 Delete 菜单项直调
  // handleDelete 立即删除并跳转（无确认，误触即丢失会话）。
  const [pendingDelete, setPendingDelete] = useState<{
    threadId: string;
    title: string;
  } | null>(null);

  const handleDelete = useCallback(
    (threadId: string) => {
      deleteThread({ threadId });
      if (threadId === threadIdFromPath) {
        const threadIndex = threads.findIndex((t) => t.thread_id === threadId);
        let nextThreadPath = pathOfThread("new", {
          agent_name: agentNameFromPath,
        });
        if (threadIndex > -1) {
          if (threads[threadIndex + 1]) {
            nextThreadPath = pathOfThread(threads[threadIndex + 1]!);
          } else if (threads[threadIndex - 1]) {
            nextThreadPath = pathOfThread(threads[threadIndex - 1]!);
          }
        }
        void router.push(nextThreadPath);
      }
    },
    [agentNameFromPath, deleteThread, router, threadIdFromPath, threads],
  );

  const handleRenameClick = useCallback(
    (threadId: string, currentTitle: string) => {
      setRenameThreadId(threadId);
      setRenameValue(currentTitle);
      setRenameDialogOpen(true);
    },
    [],
  );

  const handleRenameSubmit = useCallback(() => {
    if (renameThreadId && renameValue.trim()) {
      renameThread({ threadId: renameThreadId, title: renameValue.trim() });
      setRenameDialogOpen(false);
      setRenameThreadId(null);
      setRenameValue("");
    }
  }, [renameThread, renameThreadId, renameValue]);

  const handleTogglePin = useCallback(
    (thread: AgentThread) => {
      pinThread(
        {
          threadId: thread.thread_id,
          pinned: !isThreadPinned(thread),
        },
        {
          onError: (err) => {
            toast.error(
              err instanceof Error ? err.message : t.chats.pinChatFailed,
            );
          },
        },
      );
    },
    [pinThread, t.chats.pinChatFailed],
  );

  const handleShare = useCallback(
    async (thread: AgentThread) => {
      // DEF-4（2026-10-07）：删 VERCEL_URL 硬编码回退——本地/内网部署下它会
      // 给出打不开的上游公网链接；一律用当前 origin。剪贴板被拒（非安全
      // 上下文/权限拒绝）不再只 toast，弹可选中复制的链接文本框兜底。
      const shareUrl = `${window.location.origin}${pathOfThread(thread)}`;
      try {
        await navigator.clipboard.writeText(shareUrl);
        toast.success(t.clipboard.linkCopied);
      } catch {
        setShareFallbackUrl(shareUrl);
      }
    },
    [t],
  );

  const handleExport = useCallback(
    async (thread: AgentThread, format: "markdown" | "json") => {
      try {
        const apiClient = getAPIClient();
        const state = await apiClient.threads.getState<AgentThreadState>(
          thread.thread_id,
        );
        const messages = state.values?.messages ?? [];
        if (messages.length === 0) {
          toast.error(t.conversation.noMessages);
          return;
        }
        if (format === "markdown") {
          exportThreadAsMarkdown(thread, messages);
        } else {
          exportThreadAsJSON(thread, messages);
        }
        toast.success(t.common.exportSuccess);
      } catch {
        toast.error("Failed to export conversation");
      }
    },
    [t],
  );

  if (threads.length === 0) {
    return null;
  }
  return (
    <>
      <SidebarGroup>
        <SidebarGroupLabel className="text-muted-foreground text-[10px] font-semibold tracking-[1.2px] uppercase">
          {env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY !== "true"
            ? t.sidebar.recentChats
            : t.sidebar.demoChats}
        </SidebarGroupLabel>
        <SidebarGroupContent className="group-data-[collapsible=icon]:pointer-events-none group-data-[collapsible=icon]:-mt-8 group-data-[collapsible=icon]:opacity-0">
          <SidebarMenu>
            <div className="flex w-full flex-col gap-1">
              {displayedThreads.map((thread) => {
                const isActive = pathOfThread(thread) === pathname;
                const pinned = isThreadPinned(thread);
                return (
                  <SidebarMenuItem
                    key={thread.thread_id}
                    className="group/side-menu-item"
                  >
                    <SidebarMenuButton isActive={isActive} asChild>
                      <div>
                        <Link
                          className="text-muted-foreground block w-full whitespace-nowrap group-hover/side-menu-item:overflow-hidden"
                          href={pathOfThread(thread)}
                        >
                          {pinned && (
                            <Pin
                              aria-hidden="true"
                              className="text-muted-foreground mr-1 inline size-3.5 shrink-0"
                            />
                          )}
                          {titleOfThread(thread)}
                        </Link>
                        {env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY !== "true" && (
                          <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                              <SidebarMenuAction
                                showOnHover
                                className="bg-background/50 hover:bg-background"
                              >
                                <MoreHorizontal />
                                <span className="sr-only">{t.common.more}</span>
                              </SidebarMenuAction>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent
                              className="glass-dropdown w-48 rounded-xl"
                              side={"right"}
                              align={"start"}
                            >
                              <DropdownMenuItem
                                onSelect={() => handleTogglePin(thread)}
                              >
                                {pinned ? (
                                  <PinOff className="text-muted-foreground" />
                                ) : (
                                  <Pin className="text-muted-foreground" />
                                )}
                                <span>
                                  {pinned ? t.chats.unpinChat : t.chats.pinChat}
                                </span>
                              </DropdownMenuItem>
                              <DropdownMenuItem
                                onSelect={() =>
                                  handleRenameClick(
                                    thread.thread_id,
                                    titleOfThread(thread),
                                  )
                                }
                              >
                                <Pencil className="text-muted-foreground" />
                                <span>{t.common.rename}</span>
                              </DropdownMenuItem>
                              <DropdownMenuItem
                                onSelect={() => handleShare(thread)}
                              >
                                <Share2 className="text-muted-foreground" />
                                {/* DEF-4：消歧——这是「复制链接」动作，与发布
                                    灵感库的「分享」语义不同（此前同名易误导） */}
                                <span>{t.clipboard.copyLink}</span>
                              </DropdownMenuItem>
                              <DropdownMenuSub>
                                <DropdownMenuSubTrigger>
                                  <Download className="text-muted-foreground" />
                                  <span>{t.common.export}</span>
                                </DropdownMenuSubTrigger>
                                <DropdownMenuSubContent>
                                  <DropdownMenuItem
                                    onSelect={() =>
                                      handleExport(thread, "markdown")
                                    }
                                  >
                                    <FileText className="text-muted-foreground" />
                                    <span>{t.common.exportAsMarkdown}</span>
                                  </DropdownMenuItem>
                                  <DropdownMenuItem
                                    onSelect={() =>
                                      handleExport(thread, "json")
                                    }
                                  >
                                    <FileJson className="text-muted-foreground" />
                                    <span>{t.common.exportAsJSON}</span>
                                  </DropdownMenuItem>
                                </DropdownMenuSubContent>
                              </DropdownMenuSub>
                              <DropdownMenuSeparator />
                              <DropdownMenuItem
                                onSelect={() =>
                                  setPendingDelete({
                                    threadId: thread.thread_id,
                                    title: titleOfThread(thread),
                                  })
                                }
                              >
                                <Trash2 className="text-muted-foreground" />
                                <span>{t.common.delete}</span>
                              </DropdownMenuItem>
                            </DropdownMenuContent>
                          </DropdownMenu>
                        )}
                      </div>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                );
              })}
            </div>
          </SidebarMenu>
        </SidebarGroupContent>
      </SidebarGroup>

      {/* DEF-4：剪贴板被拒时的兜底弹层——链接可选中复制 */}
      {shareFallbackUrl !== null && (
        <ShareFallbackDialog
          url={shareFallbackUrl}
          onClose={() => setShareFallbackUrl(null)}
        />
      )}

      {/* Rename Dialog */}
      <Dialog open={renameDialogOpen} onOpenChange={setRenameDialogOpen}>
        <DialogContent className="sm:max-w-[425px]">
          <DialogHeader>
            <DialogTitle>{t.common.rename}</DialogTitle>
          </DialogHeader>
          <div className="py-4">
            <Input
              value={renameValue}
              onChange={(e) => setRenameValue(e.target.value)}
              placeholder={t.common.rename}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !isIMEComposing(e)) {
                  e.preventDefault();
                  handleRenameSubmit();
                }
              }}
            />
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setRenameDialogOpen(false)}
            >
              {t.common.cancel}
            </Button>
            <Button onClick={handleRenameSubmit}>{t.common.save}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* P2-1/BUG-003：删除确认弹层（形态与文案对齐视频任务删除弹层） */}
      <AlertDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => {
          if (!open) setPendingDelete(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t.chats.deleteChatConfirmTitle}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t.chats.deleteChatConfirmDescription}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t.common.cancel}</AlertDialogCancel>
            <AlertDialogAction
              className="bg-red-600 hover:bg-red-700"
              onClick={() => {
                if (pendingDelete) {
                  handleDelete(pendingDelete.threadId);
                }
                setPendingDelete(null);
              }}
            >
              {t.chats.deleteChatConfirmAction}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

/**
 * DEF-4（2026-10-07）：分享链接复制被拒时的兜底弹层。
 *
 * navigator.clipboard 仅在安全上下文（HTTPS/localhost）且获授权时可用——
 * 内网 HTTP 部署下必拒。修前 catch 只 toast「复制失败」，用户拿不到链接；
 * 现在展示只读文本框：自动全选，按钮走 execCommand 兼容回退，再失败
 * 用户也可手动选中复制（文本框可选中是底线保证）。
 */
function ShareFallbackDialog({
  url,
  onClose,
}: {
  url: string;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const inputRef = useRef<HTMLInputElement>(null);

  const tryCopy = () => {
    const el = inputRef.current;
    if (!el) return;
    el.focus();
    el.select();
    // execCommand 为 deprecated 但恰好覆盖 clipboard API 不可用的场景
    //（非安全上下文）；返回 false 时文本已全选，手动 Ctrl+C 仍可达。
    if (document.execCommand("copy")) {
      toast.success(t.clipboard.linkCopied);
      onClose();
    }
  };

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent className="sm:max-w-[480px]">
        <DialogHeader>
          <DialogTitle>{t.chats.shareFallbackTitle}</DialogTitle>
          <DialogDescription>
            {t.chats.shareFallbackDescription}
          </DialogDescription>
        </DialogHeader>
        <div className="py-2">
          <Input
            ref={(el) => {
              inputRef.current = el ?? null;
              el?.select();
            }}
            value={url}
            readOnly
            onFocus={(e) => e.currentTarget.select()}
            className="font-mono text-xs"
          />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            {t.common.close}
          </Button>
          <Button onClick={tryCopy}>{t.clipboard.copyToClipboard}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
