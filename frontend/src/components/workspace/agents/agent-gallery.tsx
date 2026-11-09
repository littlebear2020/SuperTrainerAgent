"use client";

import { BotIcon, PlusIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useTheme } from "next-themes";

import { useAgents } from "@/core/agents";
import type { Agent } from "@/core/agents";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import { AgentCard } from "./agent-card";

export function AgentGallery() {
  const { t } = useI18n();
  const { agents, isLoading } = useAgents();
  const router = useRouter();
  const { resolvedTheme } = useTheme();
  const isDark = resolvedTheme === "dark";

  const handleNewAgent = () => {
    router.push("/workspace/agents/new");
  };

  // Split agents by category
  const publicAgents = agents.filter((a) => a.category === "public");
  const customAgents = agents.filter((a) => a.category !== "public");

  return (
    <div className="scrollbar-none flex size-full flex-col overflow-y-auto p-6">
      <div className="mx-auto w-full max-w-6xl space-y-8">
        {/* Header */}
        <div className="flex items-center justify-between">
          <h1
            className={cn(
              "flex items-center gap-2 text-lg font-extrabold",
              isDark ? "text-white" : "text-slate-800",
            )}
          >
            <BotIcon className="h-5 w-5 text-indigo-500" />
            {t.agents.title}
          </h1>
          <button
            className="flex items-center gap-1.5 rounded-xl bg-gradient-to-r from-indigo-500 to-purple-600 px-4 py-2.5 text-[13px] font-semibold text-white transition-all hover:shadow-[0_0_20px_rgba(99,102,241,0.3)] hover:brightness-110"
            onClick={handleNewAgent}
          >
            <PlusIcon className="h-4 w-4" />
            {t.agents.newAgent}
          </button>
        </div>

        {/* Content */}
        {isLoading ? (
          <div className="flex h-40 items-center justify-center">
            <div className="h-6 w-6 animate-spin rounded-full border-2 border-indigo-500 border-t-transparent" />
          </div>
        ) : agents.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
            <BotIcon
              className={cn(
                "h-10 w-10",
                isDark ? "text-slate-600" : "text-slate-300",
              )}
            />
            <div>
              <p
                className={cn(
                  "font-medium",
                  isDark ? "text-white" : "text-slate-800",
                )}
              >
                {t.agents.emptyTitle}
              </p>
              <p
                className={cn(
                  "mt-1 text-sm",
                  isDark ? "text-slate-400" : "text-slate-500",
                )}
              >
                {t.agents.emptyDescription}
              </p>
            </div>
            <button
              className="mt-2 flex items-center gap-1.5 rounded-xl bg-gradient-to-r from-indigo-500 to-purple-600 px-4 py-2 text-[13px] font-semibold text-white transition-all hover:shadow-[0_0_20px_rgba(99,102,241,0.3)] hover:brightness-110"
              onClick={handleNewAgent}
            >
              <PlusIcon className="h-4 w-4" />
              {t.agents.newAgent}
            </button>
          </div>
        ) : (
          <div className="space-y-8">
            {/* Public agents section */}
            {publicAgents.length > 0 && (
              <AgentSection
                title={t.agents.publicAgents}
                agents={publicAgents}
                isDark={isDark}
              />
            )}

            {/* Custom agents section */}
            {customAgents.length > 0 && (
              <AgentSection
                title={t.agents.customAgents}
                agents={customAgents}
                canDelete
                isDark={isDark}
              />
            )}

            {/* If all agents are public (no custom), still show custom section with empty hint */}
            {customAgents.length === 0 && publicAgents.length > 0 && (
              <div>
                <h3
                  className={cn(
                    "mb-4 text-[10px] font-extrabold tracking-[1.2px] uppercase",
                    isDark ? "text-slate-400" : "text-slate-500",
                  )}
                >
                  {t.agents.customAgents}
                </h3>
                <div
                  className={cn(
                    "flex min-h-[120px] items-center justify-center rounded-2xl border border-dashed",
                    isDark ? "border-white/10" : "border-indigo-200/50",
                  )}
                >
                  <p
                    className={cn(
                      "text-sm",
                      isDark ? "text-slate-400" : "text-slate-500",
                    )}
                  >
                    {t.agents.noCustomAgents}
                  </p>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function AgentSection({
  title,
  agents,
  canDelete = false,
  isDark,
}: {
  title: string;
  agents: Agent[];
  canDelete?: boolean;
  isDark: boolean;
}) {
  return (
    <div>
      <h3
        className={cn(
          "mb-4 text-[10px] font-extrabold tracking-[1.2px] uppercase",
          isDark ? "text-slate-400" : "text-slate-500",
        )}
      >
        {title}
      </h3>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {agents.map((agent) => (
          <AgentCard key={agent.name} agent={agent} canDelete={canDelete} />
        ))}
      </div>
    </div>
  );
}
