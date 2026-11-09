import {
  CheckCircleIcon,
  ChevronUp,
  ClipboardListIcon,
  Loader2Icon,
  SparklesIcon,
  WrenchIcon,
  XCircleIcon,
} from "lucide-react";
import { useMemo, useState } from "react";

import {
  ChainOfThought,
  ChainOfThoughtContent,
  ChainOfThoughtStep,
} from "@/components/ai-elements/chain-of-thought";
import { Shimmer } from "@/components/ai-elements/shimmer";
import { Button } from "@/components/ui/button";
import { ShineBorder } from "@/components/ui/shine-border";
import { useI18n } from "@/core/i18n/hooks";
import { hasToolCalls } from "@/core/messages/utils";
import {
  streamdownPluginsWithoutRawHtml,
  streamdownWordAnimation,
} from "@/core/streamdown";
import {
  SafeStreamdown,
  toStreamdownComponents,
} from "@/core/streamdown/components";
import { useSubtask } from "@/core/tasks/context";
import { stepsForDisplay } from "@/core/tasks/steps";
import { explainLastToolCall } from "@/core/tools/utils";
import { cn } from "@/lib/utils";

import { CitationLink } from "../citations/citation-link";
import { FlipDisplay } from "../flip-display";

import { MarkdownContent } from "./markdown-content";

export function SubtaskCard({
  className,
  taskId,
  isLoading,
}: {
  className?: string;
  taskId: string;
  isLoading: boolean;
}) {
  const { t } = useI18n();
  const [collapsed, setCollapsed] = useState(true);
  const task = useSubtask(taskId);
  // PR-3 5.1：可见步骤 = stepsForDisplay（tool 恒留 / AI 仅留有文本者 /
  // completed 丢无工具调用的尾部终答——终答由 task.result 呈现，避免双显）。
  const displaySteps = stepsForDisplay(
    task?.steps,
    task?.status ?? "in_progress",
  );
  const icon = useMemo(() => {
    if (!task) {
      return null;
    }
    if (task.status === "completed") {
      return <CheckCircleIcon className="size-3" />;
    } else if (task.status === "failed") {
      return <XCircleIcon className="size-3 text-red-500" />;
    } else if (task.status === "in_progress") {
      return <Loader2Icon className="size-3 animate-spin" />;
    }
  }, [task]);
  // store 尚未填充（流式 task_running 事件未到 / useSyncSubtaskStatus 的
  // useEffect 未跑）——先渲染 null；updateSubtask 写入后组件自动重渲染。
  // （曾是 useSubtask(taskId)! 非空断言 → undefined.status 白屏，2026-07-24 P0）
  if (!task) {
    return null;
  }
  return (
    <ChainOfThought
      className={cn(
        "relative w-full gap-2 rounded-xl border border-indigo-500/10 py-0 dark:border-white/10",
        className,
      )}
      open={!collapsed}
    >
      <div
        className={cn(
          "ambilight z-[-1]",
          task.status === "in_progress" ? "enabled" : "",
        )}
      ></div>
      {task.status === "in_progress" && (
        <>
          <ShineBorder
            borderWidth={1.5}
            shineColor={["#A07CFE", "#FE8FB5", "#FFBE7B"]}
          />
        </>
      )}
      <div className="flex w-full flex-col rounded-lg bg-indigo-50/60 backdrop-blur-sm dark:bg-white/[0.04]">
        <div className="flex w-full items-center justify-between p-0.5">
          <Button
            className="w-full items-start justify-start text-left"
            variant="ghost"
            onClick={() => setCollapsed(!collapsed)}
          >
            <div className="flex w-full items-center justify-between">
              <ChainOfThoughtStep
                className="font-normal"
                label={
                  task.status === "in_progress" ? (
                    <Shimmer duration={3} spread={3}>
                      {task.description}
                    </Shimmer>
                  ) : (
                    task.description
                  )
                }
                icon={<ClipboardListIcon />}
              ></ChainOfThoughtStep>
              <div className="flex items-center gap-1">
                {collapsed && (
                  <div
                    className={cn(
                      "flex items-center gap-1 text-xs font-normal text-slate-600 dark:text-slate-400",
                      task.status === "failed" ? "text-red-500 opacity-67" : "",
                    )}
                  >
                    {icon}
                    <FlipDisplay
                      className="max-w-[420px] truncate pb-1"
                      uniqueKey={task.latestMessage?.id ?? ""}
                    >
                      {task.status === "in_progress" &&
                      task.latestMessage &&
                      hasToolCalls(task.latestMessage)
                        ? explainLastToolCall(task.latestMessage, t)
                        : t.subtasks[task.status]}
                    </FlipDisplay>
                  </div>
                )}
                <ChevronUp
                  className={cn(
                    "text-muted-foreground size-4",
                    !collapsed ? "" : "rotate-180",
                  )}
                />
              </div>
            </div>
          </Button>
        </div>
        <ChainOfThoughtContent className="px-4 pb-4">
          {task.prompt && (
            <ChainOfThoughtStep
              label={
                <SafeStreamdown
                  {...streamdownPluginsWithoutRawHtml}
                  animated={streamdownWordAnimation}
                  components={toStreamdownComponents({ a: CitationLink })}
                >
                  {task.prompt}
                </SafeStreamdown>
              }
            ></ChainOfThoughtStep>
          )}
          {/* PR-3 5.1：子代理步骤时间线（live-only）——AI 推理回合（Sparkles）与
              工具执行（Wrench）按 message_index 交错；进行态末行转圈。不做历史
              回填：刷新后卡片退回只有终态的现状（plan 5.3 知会，非回退）。
              ⚠️ 评审知会：本仓后端 ai_messages 只收 AIMessage（executor.py
              isinstance 守卫），live 链路 Wrench 行暂不可达（休眠分支，后端补
              emit ToolMessage 后即活——见 changelog 知会节）；live 可见行 =
              AI 文本行。 */}
          {displaySteps.map((step, index) => {
            const isLastWhileRunning =
              task.status === "in_progress" &&
              index === displaySteps.length - 1;
            const icon = isLastWhileRunning ? (
              <Loader2Icon className="size-4 animate-spin" />
            ) : step.kind === "tool" ? (
              <WrenchIcon className="size-4" />
            ) : (
              <SparklesIcon className="size-4" />
            );
            return (
              <ChainOfThoughtStep
                key={`${step.message_index}-${index}`}
                label={
                  step.kind === "tool" ? (
                    (step.tool_name ?? t.subtasks[task.status])
                  ) : (
                    <div className="text-muted-foreground line-clamp-3 text-sm">
                      <MarkdownContent content={step.text} isLoading={false} />
                    </div>
                  )
                }
                icon={icon}
              />
            );
          })}
          {/* 评审订正：时间线非空时旧行让位（上游同义形态）——否则展开态出现
              时间线末行转圈 + 旧 tool-call 行转圈的双转圈相邻形态。 */}
          {task.status === "in_progress" &&
            displaySteps.length === 0 &&
            task.latestMessage &&
            hasToolCalls(task.latestMessage) && (
              <ChainOfThoughtStep
                label={t.subtasks.in_progress}
                icon={<Loader2Icon className="size-4 animate-spin" />}
              >
                {explainLastToolCall(task.latestMessage, t)}
              </ChainOfThoughtStep>
            )}
          {task.status === "completed" && (
            <>
              <ChainOfThoughtStep
                label={t.subtasks.completed}
                icon={<CheckCircleIcon className="size-4" />}
              ></ChainOfThoughtStep>
              <ChainOfThoughtStep
                label={
                  task.result ? (
                    <MarkdownContent
                      content={task.result}
                      isLoading={isLoading}
                    />
                  ) : null
                }
              ></ChainOfThoughtStep>
            </>
          )}
          {task.status === "failed" && (
            <ChainOfThoughtStep
              label={<div className="text-red-500">{task.error}</div>}
              icon={<XCircleIcon className="size-4 text-red-500" />}
            ></ChainOfThoughtStep>
          )}
        </ChainOfThoughtContent>
      </div>
    </ChainOfThought>
  );
}
