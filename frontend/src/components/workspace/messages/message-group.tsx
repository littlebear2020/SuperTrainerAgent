import type { Message } from "@langchain/langgraph-sdk";
import {
  BookOpenTextIcon,
  ChevronUp,
  FolderOpenIcon,
  GlobeIcon,
  LightbulbIcon,
  ListTodoIcon,
  MessageCircleQuestionMarkIcon,
  NotebookPenIcon,
  SearchIcon,
  SquareTerminalIcon,
  WrenchIcon,
} from "lucide-react";
import { memo, useMemo, useState } from "react";

import {
  ChainOfThought,
  ChainOfThoughtContent,
  ChainOfThoughtSearchResult,
  ChainOfThoughtSearchResults,
  ChainOfThoughtStep,
} from "@/components/ai-elements/chain-of-thought";
import { CodeBlock } from "@/components/ai-elements/code-block";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/core/i18n/hooks";
import { formatToolDetail } from "@/core/messages/tool-detail-preview";
import {
  extractClarificationOptions,
  extractReasoningContentFromMessage,
  findToolCallResult,
} from "@/core/messages/utils";
import { extractTitleFromMarkdown } from "@/core/utils/markdown";
import { sanitizeCitationUrl } from "@/core/utils/url";
import { env } from "@/env";
import { cn } from "@/lib/utils";

import { useArtifacts } from "../artifacts";
import { FlipDisplay } from "../flip-display";
import { Tooltip } from "../tooltip";

import { ClarificationOptions } from "./clarification-options";
import { MarkdownContent } from "./markdown-content";
import { ToolCallDetails } from "./tool-call-details";

interface MessageGroupProps {
  className?: string;
  messages: Message[];
  isLoading?: boolean;
}

function MessageGroupComponent({
  className,
  messages,
  isLoading = false,
}: MessageGroupProps) {
  const { t } = useI18n();
  const [showAbove, setShowAbove] = useState(
    env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true",
  );
  const [showLastThinking, setShowLastThinking] = useState(
    env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true",
  );
  const steps = useMemo(() => convertToSteps(messages), [messages]);
  const lastToolCallStep = useMemo(() => {
    const filteredSteps = steps.filter((step) => step.type === "toolCall");
    return filteredSteps[filteredSteps.length - 1];
  }, [steps]);
  const aboveLastToolCallSteps = useMemo(() => {
    if (lastToolCallStep) {
      const index = steps.indexOf(lastToolCallStep);
      return steps.slice(0, index);
    }
    return [];
  }, [lastToolCallStep, steps]);
  const lastReasoningStep = useMemo(() => {
    if (lastToolCallStep) {
      const index = steps.indexOf(lastToolCallStep);
      return steps.slice(index + 1).find((step) => step.type === "reasoning");
    } else {
      const filteredSteps = steps.filter((step) => step.type === "reasoning");
      return filteredSteps[filteredSteps.length - 1];
    }
  }, [lastToolCallStep, steps]);
  return (
    <ChainOfThought
      className={cn("w-full gap-2 rounded-lg border p-0.5", className)}
      open={true}
    >
      {aboveLastToolCallSteps.length > 0 && (
        <Button
          key="above"
          className="w-full items-start justify-start text-left"
          variant="ghost"
          onClick={() => setShowAbove(!showAbove)}
        >
          <ChainOfThoughtStep
            label={
              <span className="opacity-60">
                {showAbove
                  ? t.toolCalls.lessSteps
                  : t.toolCalls.moreSteps(aboveLastToolCallSteps.length)}
              </span>
            }
            icon={
              <ChevronUp
                className={cn(
                  "size-4 opacity-60 transition-transform duration-200",
                  showAbove ? "rotate-180" : "",
                )}
              />
            }
          ></ChainOfThoughtStep>
        </Button>
      )}
      {lastToolCallStep && (
        <ChainOfThoughtContent className="px-4 pb-2">
          {showAbove &&
            aboveLastToolCallSteps.map((step) =>
              step.type === "reasoning" ? (
                <ChainOfThoughtStep
                  key={step.id}
                  label={
                    <MarkdownContent
                      content={step.reasoning ?? ""}
                      isLoading={isLoading}
                    />
                  }
                ></ChainOfThoughtStep>
              ) : (
                <ToolCall key={step.id} {...step} isLoading={isLoading} />
              ),
            )}
          {lastToolCallStep && (
            <FlipDisplay uniqueKey={lastToolCallStep.id ?? ""}>
              <ToolCall
                key={lastToolCallStep.id}
                {...lastToolCallStep}
                isLast={true}
                isLoading={isLoading}
              />
            </FlipDisplay>
          )}
        </ChainOfThoughtContent>
      )}
      {lastReasoningStep && (
        <>
          <Button
            key={lastReasoningStep.id}
            className="w-full items-start justify-start text-left"
            variant="ghost"
            onClick={() => setShowLastThinking(!showLastThinking)}
          >
            <div className="flex w-full items-center justify-between">
              <ChainOfThoughtStep
                className="font-normal"
                label={t.common.thinking}
                icon={LightbulbIcon}
              ></ChainOfThoughtStep>
              <div>
                <ChevronUp
                  className={cn(
                    "text-muted-foreground size-4",
                    showLastThinking ? "" : "rotate-180",
                  )}
                />
              </div>
            </div>
          </Button>
          {showLastThinking && (
            <ChainOfThoughtContent className="px-4 pb-2">
              <ChainOfThoughtStep
                key={lastReasoningStep.id}
                label={
                  <MarkdownContent
                    content={lastReasoningStep.reasoning ?? ""}
                    isLoading={isLoading}
                  />
                }
              ></ChainOfThoughtStep>
            </ChainOfThoughtContent>
          )}
        </>
      )}
    </ChainOfThought>
  );
}

// memo 化：长会话里父级每来一个流式 chunk 都会重渲染整条消息列表，
// 不加 memo 则每个 MessageGroup（哪怕内容没变的旧组）都全量重算 steps/重渲染。
// 配合 core/messages/utils.ts 的 reuseMessageGroups（内容未变的组复用旧引用），
// memo 的浅比较才能命中、跳过旧组渲染；流式中末组不复用，确保新 token 正常显示。
function messageGroupPropsEqual(
  prev: Readonly<MessageGroupProps>,
  next: Readonly<MessageGroupProps>,
): boolean {
  // 只按引用相等判断。流式期「显示最新 token」由 reuseMessageGroups 给末组换新引用
  // 保证（messages 引用变了 → memo 自然重渲染），不靠比较器按 isLoading 强制重渲染。
  // 后者会让所有组在加载期都重渲染，触发 FlipDisplay 的 framer-motion 测量死循环
  // （Maximum update depth，真机打开老任务必现——见 message-group-memo 用例）。
  return prev.className === next.className && prev.messages === next.messages;
}

export const MessageGroup = memo(MessageGroupComponent, messageGroupPropsEqual);

function ToolCall({
  id,
  messageId,
  name,
  args,
  result,
  resultMessage,
  isLast = false,
  isLoading = false,
}: {
  id?: string;
  messageId?: string;
  name: string;
  args: Record<string, unknown>;
  result?: string | Record<string, unknown>;
  /** PR-2 4.5：tool 消息本体（Details 面板取 content 与 error 态）。 */
  resultMessage?: Extract<Message, { type: "tool" }>;
  isLast?: boolean;
  isLoading?: boolean;
}) {
  const { t } = useI18n();
  const { setOpen, autoOpen, autoSelect, selectedArtifact, select } =
    useArtifacts();

  if (name === "web_search") {
    let label: React.ReactNode = t.toolCalls.searchForRelatedInfo;
    if (typeof args.query === "string") {
      label = t.toolCalls.searchOnWebFor(args.query);
    }
    return (
      <ChainOfThoughtStep key={id} label={label} icon={SearchIcon}>
        {Array.isArray(result) && (
          <ChainOfThoughtSearchResults>
            {result.map((item) => {
              // SEC-M18: tool results bypass the markdown pipeline — a
              // poisoned result could carry a javascript: URL.
              const safeUrl = sanitizeCitationUrl(item.url ?? "");
              return (
                <ChainOfThoughtSearchResult key={item.url}>
                  {safeUrl ? (
                    <a href={safeUrl} target="_blank" rel="noopener noreferrer">
                      {item.title}
                    </a>
                  ) : (
                    <span className="text-muted-foreground">{item.title}</span>
                  )}
                </ChainOfThoughtSearchResult>
              );
            })}
          </ChainOfThoughtSearchResults>
        )}
      </ChainOfThoughtStep>
    );
  } else if (name === "image_search") {
    let label: React.ReactNode = t.toolCalls.searchForRelatedImages;
    if (typeof args.query === "string") {
      label = t.toolCalls.searchForRelatedImagesFor(args.query);
    }
    const results = (
      result as {
        results: {
          source_url: string;
          thumbnail_url: string;
          image_url: string;
          title: string;
        }[];
      }
    )?.results;
    return (
      <ChainOfThoughtStep key={id} label={label} icon={SearchIcon}>
        {Array.isArray(results) && (
          <ChainOfThoughtSearchResults>
            {Array.isArray(results) &&
              results.map((item) => {
                // SEC-M18: tool-result URLs bypass the markdown pipeline —
                // reject non-http(s) schemes; img src additionally requires
                // an explicit http(s) prefix (no protocol-relative).
                const safeSource = sanitizeCitationUrl(item.source_url ?? "");
                const safeThumb = sanitizeCitationUrl(item.thumbnail_url ?? "");
                return (
                  <Tooltip key={item.image_url} content={item.title}>
                    {safeSource ? (
                      <a
                        className="size-24 overflow-hidden rounded-lg object-cover"
                        href={safeSource}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        <div className="bg-accent size-24">
                          {safeThumb?.startsWith("http") ? (
                            <img
                              loading="lazy"
                              className="size-full object-cover"
                              src={safeThumb}
                              alt={item.title}
                              width={100}
                              height={100}
                            />
                          ) : null}
                        </div>
                      </a>
                    ) : (
                      <span className="text-muted-foreground line-clamp-4 size-24 p-1 text-xs">
                        {item.title}
                      </span>
                    )}
                  </Tooltip>
                );
              })}
          </ChainOfThoughtSearchResults>
        )}
      </ChainOfThoughtStep>
    );
  } else if (name === "web_fetch") {
    const url = (args as { url: string })?.url;
    let title = url;
    if (typeof result === "string") {
      const potentialTitle = extractTitleFromMarkdown(result);
      if (potentialTitle && potentialTitle.toLowerCase() !== "untitled") {
        title = potentialTitle;
      }
    }
    // SEC-M18: args.url is untrusted tool input — reject non-http(s) schemes.
    const safeUrl = url ? sanitizeCitationUrl(url) : null;
    return (
      <ChainOfThoughtStep
        key={id}
        label={t.toolCalls.viewWebPage}
        icon={GlobeIcon}
      >
        <ChainOfThoughtSearchResult>
          {url &&
            (safeUrl ? (
              <a
                href={safeUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="cursor-pointer"
              >
                {title}
              </a>
            ) : (
              <span className="text-muted-foreground">{title}</span>
            ))}
        </ChainOfThoughtSearchResult>
      </ChainOfThoughtStep>
    );
  } else if (name === "ls") {
    let description: string | undefined = (args as { description: string })
      ?.description;
    if (!description) {
      description = t.toolCalls.listFolder;
    }
    const path: string | undefined = (args as { path: string })?.path;
    return (
      <ChainOfThoughtStep key={id} label={description} icon={FolderOpenIcon}>
        {path && (
          <ChainOfThoughtSearchResult className="cursor-pointer">
            {path}
          </ChainOfThoughtSearchResult>
        )}
      </ChainOfThoughtStep>
    );
  } else if (name === "read_file") {
    let description: string | undefined = (args as { description: string })
      ?.description;
    if (!description) {
      description = t.toolCalls.readFile;
    }
    const { path } = args as { path: string; content: string };
    return (
      <ChainOfThoughtStep key={id} label={description} icon={BookOpenTextIcon}>
        {path && (
          <ChainOfThoughtSearchResult className="cursor-pointer">
            {path}
          </ChainOfThoughtSearchResult>
        )}
      </ChainOfThoughtStep>
    );
  } else if (name === "write_file" || name === "str_replace") {
    let description: string | undefined = (args as { description: string })
      ?.description;
    if (!description) {
      description = t.toolCalls.writeFile;
    }
    const path: string | undefined = (args as { path: string })?.path;
    if (isLoading && isLast && autoOpen && autoSelect && path && !result) {
      setTimeout(() => {
        const url = new URL(
          `write-file:${path}?message_id=${messageId}&tool_call_id=${id}`,
        ).toString();
        if (selectedArtifact === url) {
          return;
        }
        select(url, true);
        setOpen(true);
      }, 100);
    }

    return (
      <ChainOfThoughtStep
        key={id}
        className="cursor-pointer"
        label={description}
        icon={NotebookPenIcon}
        onClick={() => {
          select(
            new URL(
              `write-file:${path}?message_id=${messageId}&tool_call_id=${id}`,
            ).toString(),
          );
          setOpen(true);
        }}
      >
        {path && (
          <ChainOfThoughtSearchResult className="cursor-pointer">
            {path}
          </ChainOfThoughtSearchResult>
        )}
      </ChainOfThoughtStep>
    );
  } else if (name === "bash") {
    let description: string | undefined = (args as { description: string })
      ?.description;
    if (!description) {
      description = t.toolCalls.executeCommand;
    }
    const command: string | undefined = (args as { command: string })?.command;
    // PR-2 4.5：大 command 复用 formatToolDetail 截断（12000 字符上限 + 截断
    // 标记），防超长命令把步骤卡撑爆；短命令原文直出（text === command）。
    const commandPreview = command ? formatToolDetail(command).text : "";
    return (
      <ChainOfThoughtStep
        key={id}
        label={description}
        icon={SquareTerminalIcon}
      >
        {command && (
          <CodeBlock
            className="mx-0 cursor-pointer border-none px-0"
            showLineNumbers={false}
            language="bash"
            code={commandPreview}
          />
        )}
        <ToolCallDetails
          name={name}
          callId={id}
          args={args}
          resultMessage={resultMessage}
        />
      </ChainOfThoughtStep>
    );
  } else if (name === "ask_clarification") {
    // 后端已在 tool call args 里下发 options，渲染成可点选项（P1-12）。
    // 流式中提交会被输入框转成「停止」，故此时禁用。
    const options = extractClarificationOptions(
      (args as { options?: unknown })?.options,
    );
    return (
      <ChainOfThoughtStep
        key={id}
        label={t.toolCalls.needYourHelp}
        icon={MessageCircleQuestionMarkIcon}
      >
        {options.length > 0 && (
          <ClarificationOptions options={options} disabled={isLoading} />
        )}
      </ChainOfThoughtStep>
    );
  } else if (name === "write_todos") {
    return (
      <ChainOfThoughtStep
        key={id}
        label={t.toolCalls.writeTodos}
        icon={ListTodoIcon}
      ></ChainOfThoughtStep>
    );
  } else {
    const description: string | undefined = (args as { description: string })
      ?.description;
    // PR-2 4.5（D4）：通用工具步骤挂 Details 面板——常显、默认收起；报障时
    // 用户点开即可截图工具名/调用 ID/输入/结果本体。
    return (
      <ChainOfThoughtStep
        key={id}
        label={description ?? t.toolCalls.useTool(name)}
        icon={WrenchIcon}
      >
        <ToolCallDetails
          name={name}
          callId={id}
          args={args}
          resultMessage={resultMessage}
        />
      </ChainOfThoughtStep>
    );
  }
}

interface GenericCoTStep<T extends string = string> {
  id?: string;
  messageId?: string;
  type: T;
}

interface CoTReasoningStep extends GenericCoTStep<"reasoning"> {
  reasoning: string | null;
}

interface CoTToolCallStep extends GenericCoTStep<"toolCall"> {
  name: string;
  args: Record<string, unknown>;
  result?: string;
  /** PR-2 4.5：tool 消息本体索引（resultMessages），供 Details 面板消费。 */
  resultMessage?: Extract<Message, { type: "tool" }>;
}

type CoTStep = CoTReasoningStep | CoTToolCallStep;

function convertToSteps(messages: Message[]): CoTStep[] {
  const steps: CoTStep[] = [];
  // PR-2 4.5：tool_call_id → tool 消息本体索引（Details 面板取 content/status）。
  const toolMessagesByCallId = new Map<
    string,
    Extract<Message, { type: "tool" }>
  >();
  for (const message of messages) {
    if (message.type === "tool" && message.tool_call_id) {
      toolMessagesByCallId.set(message.tool_call_id, message);
    }
  }
  for (const message of messages) {
    if (message.type === "ai") {
      const reasoning = extractReasoningContentFromMessage(message);
      if (reasoning) {
        const step: CoTReasoningStep = {
          id: message.id,
          messageId: message.id,
          type: "reasoning",
          reasoning,
        };
        steps.push(step);
      }
      for (const tool_call of message.tool_calls ?? []) {
        if (tool_call.name === "task") {
          continue;
        }
        const step: CoTToolCallStep = {
          id: tool_call.id,
          messageId: message.id,
          type: "toolCall",
          name: tool_call.name,
          args: tool_call.args,
        };
        const toolCallId = tool_call.id;
        if (toolCallId) {
          const toolCallResult = findToolCallResult(toolCallId, messages);
          if (toolCallResult) {
            try {
              const json = JSON.parse(toolCallResult);
              step.result = json;
            } catch {
              step.result = toolCallResult;
            }
          }
          step.resultMessage = toolMessagesByCallId.get(toolCallId);
        }
        steps.push(step);
      }
    }
  }
  return steps;
}
