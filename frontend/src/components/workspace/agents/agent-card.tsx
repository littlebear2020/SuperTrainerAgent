"use client";

import {
  BookOpen,
  Clapperboard,
  Film,
  MessageSquareIcon,
  Palette,
  Presentation,
  ShieldCheckIcon,
  Trash2Icon,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { resolveAgentName, useDeleteAgent } from "@/core/agents";
import type { Agent } from "@/core/agents";
import { agentAppearance } from "@/core/agents/appearance";
import { useI18n } from "@/core/i18n/hooks";
import { useGlassStyle } from "@/core/theme/glass";
import { cn } from "@/lib/utils";

/* Agent name → icon + color style mapping */
interface AgentCardProps {
  agent: Agent;
  /** Whether the current user can delete this agent */
  canDelete?: boolean;
}

export function AgentCard({ agent, canDelete = true }: AgentCardProps) {
  const { t } = useI18n();
  const router = useRouter();
  const deleteAgent = useDeleteAgent();
  const [deleteOpen, setDeleteOpen] = useState(false);
  const { glassStyle, isDark } = useGlassStyle();

  const isPublic = agent.category === "public";
  // 外观单一事实源：core/agents/appearance.ts（含退役别名归一与 fallback）
  const appearance = agentAppearance(agent.name);
  const IconComponent = appearance.icon;

  function handleChat() {
    router.push(`/workspace/agents/${agent.name}/chats/new`);
  }

  async function handleDelete() {
    try {
      await deleteAgent.mutateAsync(agent.name);
      toast.success(t.agents.deleteSuccess);
      setDeleteOpen(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <>
      <div
        style={glassStyle()}
        className="interactive-glass group relative flex flex-col overflow-hidden rounded-2xl p-5 transition-transform hover:scale-[1.01]"
      >
        {/* Top row: avatar + name */}
        <div className="relative z-[1] mb-3 flex items-start gap-3">
          <div
            className={`flex h-[42px] w-[42px] shrink-0 items-center justify-center rounded-xl text-[18px] ${appearance.bg} ${appearance.text}`}
          >
            <IconComponent className="h-5 w-5" />
          </div>
          <div className="min-w-0">
            <h3
              className={cn(
                "truncate text-[14.5px] font-semibold",
                isDark ? "text-white" : "text-slate-800",
              )}
            >
              {agent.name}
            </h3>
            <div className="mt-1 flex items-center gap-1.5">
              {agent.model && (
                <span
                  className={cn(
                    "inline-block rounded-md px-2 py-0.5 text-[10.5px] font-medium",
                    isDark
                      ? "bg-white/5 text-slate-400"
                      : "bg-indigo-500/5 text-slate-500",
                  )}
                >
                  {agent.model}
                </span>
              )}
              {isPublic ? (
                <span className="inline-flex items-center gap-1 rounded-md border border-indigo-500/30 bg-indigo-500/15 px-2 py-0.5 text-[10px] font-medium text-indigo-600 dark:text-indigo-400">
                  <ShieldCheckIcon className="h-3 w-3" />
                  {t.agents.categoryPublic}
                </span>
              ) : (
                <span
                  className={cn(
                    "inline-block rounded-md px-2 py-0.5 text-[10px] font-medium",
                    isDark
                      ? "bg-white/5 text-slate-400"
                      : "bg-indigo-500/5 text-slate-500",
                  )}
                >
                  {t.agents.categoryCustom}
                </span>
              )}
            </div>
            {agent.creator_name && (
              <p
                className={cn(
                  "mt-1 text-[10.5px]",
                  isDark ? "text-slate-400" : "text-slate-500",
                )}
              >
                by {agent.creator_name}
              </p>
            )}
          </div>
        </div>

        {/* Description */}
        {agent.description && (
          <p
            className={cn(
              "relative z-[1] mb-3.5 line-clamp-2 text-[12.5px] leading-relaxed",
              isDark ? "text-slate-400" : "text-slate-500",
            )}
          >
            {agent.description}
          </p>
        )}

        {/* Skills/Tags + Chat button */}
        <div
          className="relative z-[1] mt-auto flex items-center justify-between pt-3.5"
          style={{
            borderTop: `1px solid ${isDark ? "rgba(255,255,255,0.08)" : "rgba(99,102,241,0.12)"}`,
          }}
        >
          <div className="flex max-h-[2.75rem] flex-wrap gap-1 overflow-hidden">
            {agent.tool_groups?.map((group) => (
              <span
                key={`tg:${group}`}
                className={cn(
                  "rounded-md px-2 py-0.5 text-[10px]",
                  isDark
                    ? "bg-white/5 text-slate-400"
                    : "bg-indigo-500/5 text-slate-500",
                )}
              >
                {group}
              </span>
            ))}
            {agent.skills?.map((skill) => (
              <span
                key={`sk:${skill}`}
                className={cn(
                  "rounded-md px-2 py-0.5 text-[10px]",
                  isDark
                    ? "bg-white/5 text-slate-400"
                    : "bg-indigo-500/5 text-slate-500",
                )}
              >
                {skill}
              </span>
            ))}
          </div>
          <button
            className="shrink-0 rounded-lg border border-indigo-500/50 bg-transparent px-3.5 py-1.5 text-[11.5px] font-semibold text-indigo-600 transition-all hover:bg-indigo-600 hover:text-white hover:shadow-[0_4px_14px_rgba(99,102,241,0.3)] dark:text-indigo-400"
            onClick={handleChat}
          >
            <MessageSquareIcon className="mr-1 inline h-3.5 w-3.5" />
            {t.agents.chat}
          </button>
        </div>

        {/* Delete button */}
        {canDelete && (
          <button
            className="absolute top-3 right-3 z-[2] flex h-7 w-7 items-center justify-center rounded-md opacity-0 transition-all group-hover:opacity-100 hover:bg-red-500/10 hover:text-red-500"
            onClick={() => setDeleteOpen(true)}
            title={t.agents.delete}
          >
            <Trash2Icon
              className={cn(
                "h-3.5 w-3.5",
                isDark ? "text-slate-400" : "text-slate-500",
              )}
            />
          </button>
        )}
      </div>

      {/* Delete Confirm */}
      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t.agents.delete}</DialogTitle>
            <DialogDescription>{t.agents.deleteConfirm}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDeleteOpen(false)}
              disabled={deleteAgent.isPending}
            >
              {t.common.cancel}
            </Button>
            <Button
              variant="destructive"
              onClick={handleDelete}
              disabled={deleteAgent.isPending}
            >
              {deleteAgent.isPending ? t.common.loading : t.common.delete}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
