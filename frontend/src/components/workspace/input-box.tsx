"use client";

import type { ChatStatus } from "ai";
import {
  CheckIcon,
  FileTextIcon,
  GraduationCapIcon,
  LayoutGridIcon,
  LightbulbIcon,
  Loader2Icon,
  PaperclipIcon,
  PlusIcon,
  SearchIcon,
  SparklesIcon,
  RocketIcon,
  VideoIcon,
  WandSparklesIcon,
  ZapIcon,
} from "lucide-react";
import { useSearchParams } from "next/navigation";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentProps,
} from "react";
import { toast } from "sonner";

import {
  PromptInput,
  PromptInputActionMenu,
  PromptInputActionMenuContent,
  PromptInputActionMenuItem,
  PromptInputActionMenuTrigger,
  PromptInputAttachment,
  PromptInputAttachments,
  PromptInputBody,
  PromptInputButton,
  PromptInputFooter,
  PromptInputSubmit,
  PromptInputTextarea,
  PromptInputTools,
  usePromptInputAttachments,
  usePromptInputController,
  type PromptInputMessage,
} from "@/components/ai-elements/prompt-input";
import { ConfettiButton } from "@/components/ui/confetti-button";
import {
  DropdownMenuGroup,
  DropdownMenuLabel,
  DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";
import { useI18n } from "@/core/i18n/hooks";
import { useModels } from "@/core/models/hooks";
import { useSkills } from "@/core/skills/hooks";
import { useGlassStyle } from "@/core/theme/glass";
import type { AgentThreadContext } from "@/core/threads";
import { streamAssist } from "@/core/writer/api";
import { useMounted } from "@/hooks/use-mounted";
import { cn } from "@/lib/utils";

import {
  ModelSelector,
  ModelSelectorContent,
  ModelSelectorInput,
  ModelSelectorItem,
  ModelSelectorList,
  ModelSelectorName,
  ModelSelectorTrigger,
} from "../ai-elements/model-selector";
import { Suggestion, Suggestions } from "../ai-elements/suggestion";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "../ui/dropdown-menu";

import {
  type InputMode,
  reasoningEffortForMode,
  resolveMode,
} from "./input-mode";
import { ModeHoverGuide } from "./mode-hover-guide";
import { Tooltip } from "./tooltip";

/** 前端语义分类：将 Skill 按功能领域分组 */
const SKILL_GROUPS: Record<string, string[]> = {
  video: ["video-clip", "video-dl", "auto-video-flow", "corporate-video"],
  // 2026-09-11 course-ppt 退役：pptx/ppt-generation 随壳删除（ppt-master 承接）
  document: ["docx", "pdf", "xlsx"],
  research: ["deep-research", "consulting-analysis", "data-analysis"],
  design: [
    "canvas-design",
    "chart-visualization",
    "course-poster",
    "knowledge-card-auto-generator",
    "trainerai-imagine",
    "trainerai-diagram",
    // 2026-09-09: trainerai-cover-image / trainerai-article-illustrator 合并而来
    "trainerai-article-visuals",
    "trainerai-infographic",
    "trainerai-image-cards",
    "trainerai-comic",
  ],
  course: ["course-video"],
};

/** 分类中文标签 */
const SKILL_GROUP_LABELS: Record<string, string> = {
  all: "全部",
  video: "视频制作",
  document: "文档处理",
  research: "研究分析",
  design: "设计创作",
  course: "课程专属",
  other: "其他",
};

/** 分类图标 */
const SKILL_GROUP_ICONS: Record<
  string,
  React.ComponentType<{ className?: string }>
> = {
  all: LayoutGridIcon,
  video: VideoIcon,
  document: FileTextIcon,
  research: SearchIcon,
  design: SparklesIcon,
  course: GraduationCapIcon,
  other: LayoutGridIcon,
};

/** 反向索引：skill name → group key */
const SKILL_TO_GROUP = new Map<string, string>();
for (const [group, names] of Object.entries(SKILL_GROUPS)) {
  for (const name of names) {
    SKILL_TO_GROUP.set(name, group);
  }
}

/** agent 建议 chip 形状（AgentConfig.suggestion_prompts 透传）。 */
export type AgentSuggestionChip = { label: string; prompt: string };

/**
 * chip 渲染谓词（条目 20，2026-09-12 缺陷批）：label/prompt 均 trim 非空——
 * 与 handleSuggestionClick 的 `if (!prompt) return` 早退对齐（修前空 label 渲
 * 空按钮、空 prompt 点了无反应）。导出供 suggestion-prompts.test.ts 断言真实
 * 实现（修前测试守卫的是测试内手写复制品）。
 */
export function isRenderableAgentChip(chip: AgentSuggestionChip): boolean {
  return Boolean(chip?.label?.trim() && chip?.prompt?.trim());
}

/**
 * chip key 复合（条目 20）：来源前缀 + index——修前 `key={chip.label}` 与下方
 * i18n 建议 `key={suggestion.suggestion}` 共享 key 命名空间，同文案即撞车。
 */
export function agentChipKey(
  _chip: AgentSuggestionChip,
  index: number,
): string {
  return `agent-chip-${index}`;
}

export function InputBox({
  className,
  disabled,
  isUploading,
  uploadProgress,
  autoFocus,
  status = "ready",
  context,
  extraHeader,
  isNewThread,
  threadId,
  initialValue,
  agentSkills,
  agentSuggestionPrompts,
  showNewThreadSuggestions = true,
  onContextChange,
  onSubmit,
  onStop,
  ...props
}: Omit<ComponentProps<typeof PromptInput>, "onSubmit"> & {
  assistantId?: string | null;
  status?: ChatStatus;
  disabled?: boolean;
  isUploading?: boolean;
  uploadProgress?: number;
  context: Omit<
    AgentThreadContext,
    "thread_id" | "is_plan_mode" | "thinking_enabled" | "subagent_enabled"
  > & {
    mode: "flash" | "thinking" | "pro" | "ultra" | undefined;
    reasoning_effort?: "minimal" | "low" | "medium" | "high";
  };
  extraHeader?: React.ReactNode;
  isNewThread?: boolean;
  threadId: string;
  initialValue?: string;
  agentSkills?: string[] | null;
  agentSuggestionPrompts?: { label: string; prompt: string }[] | null;
  showNewThreadSuggestions?: boolean;
  onContextChange?: (
    context: Omit<
      AgentThreadContext,
      "thread_id" | "is_plan_mode" | "thinking_enabled" | "subagent_enabled"
    > & {
      mode: "flash" | "thinking" | "pro" | "ultra" | undefined;
      reasoning_effort?: "minimal" | "low" | "medium" | "high";
    },
  ) => void;
  onSubmit?: (message: PromptInputMessage) => void;
  onStop?: () => void;
}) {
  const { t } = useI18n();
  const searchParams = useSearchParams();
  const [modelDialogOpen, setModelDialogOpen] = useState(false);
  const isDisabled = disabled === true || isUploading === true;
  const {
    models: allModels,
    error: modelsError,
    refetch: refetchModels,
  } = useModels();
  const models = allModels;
  const { textInput } = usePromptInputController();
  // P2-8 草稿持久化：按线程把输入框文本存进 localStorage，切线程/刷新后恢复。controller
  // 是全局单例，切线程时显式把目标线程的草稿 setInput 进去（无草稿则清空，避免上一个
  // 线程的文本串台）。initialValue prop 是历史死代码（无调用方），无需让路。
  // 未保存的新会话不持久化：即使路由 hook 已分配临时 UUID，isNewThread 仍为 true，
  // 不能把临时 ID 变化当作真实线程切换而清空 handoff。真实线程沿用既有持久化。
  const draftKey =
    !isNewThread && threadId && threadId !== "new" ? `draft:${threadId}` : null;
  const latestDraftRef = useRef(textInput.value);
  latestDraftRef.current = textInput.value;
  const activeDraftKeyRef = useRef(draftKey);
  const draftMountedRef = useRef(false);

  const persistDraft = useCallback((key: string, value: string) => {
    try {
      if (value) {
        window.localStorage.setItem(key, value);
      } else {
        // 空草稿移除 key，不写空串——避免 localStorage 堆积大量空值（B4-4）。
        window.localStorage.removeItem(key);
      }
    } catch {
      // 隐私模式等不可写——静默放弃持久化，不影响输入。
    }
  }, []);

  useEffect(() => {
    const prevKey = activeDraftKeyRef.current;
    // 区分「首次挂载」与「切线程」：前者尊重 provider 的 initialInput / 现有文本，
    // 只在有已存草稿时载入；后者还要在无草稿时清空，避免上一线程文本串台。
    const isSwitch = draftMountedRef.current && prevKey !== draftKey;
    draftMountedRef.current = true;
    // 切走前先把旧线程当前文本落盘（400ms 防抖可能还没触发，避免丢最近输入）。
    if (isSwitch && prevKey) {
      persistDraft(prevKey, latestDraftRef.current);
    }
    activeDraftKeyRef.current = draftKey;
    if (!draftKey) {
      if (isSwitch && prevKey) {
        textInput.setInput("");
      }
      return;
    }
    let saved = "";
    try {
      saved = window.localStorage.getItem(draftKey) ?? "";
    } catch {
      saved = "";
    }
    if (saved) {
      // 有草稿：mount 与 switch 都载入。
      textInput.setInput(saved);
    } else if (isSwitch) {
      // 切到无草稿线程：清空，避免上一线程文本串台。
      textInput.setInput("");
    }
    // mount 且无草稿：不动 input（尊重 initialInput / 现有文本）。
    // setInput 身份随 controller 重 memo 化而变；这里只想在 draftKey 变化时跑。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draftKey, persistDraft]);

  // 防抖落盘：文本变化后 400ms 写入（空则清 key）。
  useEffect(() => {
    if (!draftKey) {
      return;
    }
    const value = textInput.value;
    const handle = window.setTimeout(() => persistDraft(draftKey, value), 400);
    return () => window.clearTimeout(handle);
  }, [draftKey, textInput.value, persistDraft]);

  // 卸载时同步落盘最新草稿——防抖 effect 的 cleanup 只清定时器，离开聊天页时会丢失
  // 最近 400ms 的输入（B4-1）。空 deps → 仅在卸载时跑。
  useEffect(() => {
    return () => {
      const key = activeDraftKeyRef.current;
      if (key) {
        persistDraft(key, latestDraftRef.current);
      }
    };
  }, [persistDraft]);

  const promptRootRef = useRef<HTMLDivElement | null>(null);
  const { glassStyle, isDark } = useGlassStyle();
  const mounted = useMounted();

  // ── 缺陷 2 路径 A（2026-08-27）：/api/models 失败必须可见且可重试 ──
  // loadModels 现在对非 2xx 抛错 → react-query 进 error 态。这里用 ref 保证
  // 每次挂载只弹一次（不刷屏）；点「重试」后允许新一轮失败再次提示。
  const modelsErrorToastShownRef = useRef(false);
  useEffect(() => {
    if (!modelsError || modelsErrorToastShownRef.current) {
      return;
    }
    modelsErrorToastShownRef.current = true;
    toast.error(t.inputBox.modelsLoadFailed, {
      action: {
        label: t.inputBox.retry,
        onClick: () => {
          modelsErrorToastShownRef.current = false;
          void refetchModels();
        },
      },
    });
  }, [modelsError, refetchModels, t]);

  // ── 缺陷 2 路径 B（2026-08-27）：线程绑定的模型已撤册 → 一次性提示 ──
  // 持久化的 model_name 不在列表时，自动纠正 effect 会回退 models[0]（回写本身
  // 就是对失效持久化值的清理）；这里补用户可见性：按 threadId+model_name 去重，
  // 每次挂载只提示一次。
  const delistedToastKeyRef = useRef<string | null>(null);
  useEffect(() => {
    if (models.length === 0 || !context.model_name) {
      return;
    }
    if (models.some((m) => m.name === context.model_name)) {
      return;
    }
    // AgentThreadContext 的 Omit 化会把 model_name 拓宽成 unknown；此处已过
    // truthiness 守卫，底层接口声明是 string | undefined，收窄回 string。
    const delistedName = context.model_name as string;
    const key = `${threadId}:${delistedName}`;
    if (delistedToastKeyRef.current === key) {
      return;
    }
    delistedToastKeyRef.current = key;
    toast.info(t.inputBox.modelDelistedToast.replace("{model}", delistedName));
  }, [context.model_name, models, t, threadId]);

  useEffect(() => {
    if (models.length === 0) {
      return;
    }
    const currentModel = models.find((m) => m.name === context.model_name);
    const fallbackModel = currentModel ?? models[0]!;
    const supportsThinking = fallbackModel.supports_thinking ?? false;
    const nextModelName = fallbackModel.name;
    // 解耦后 mode 归一：仅「思考」档在模型不支持时落 flash；pro/ultra 对所有
    // 模型开放，不再被改写（缺陷 1）。
    const nextMode = resolveMode(context.mode, supportsThinking);

    if (context.model_name === nextModelName && context.mode === nextMode) {
      return;
    }

    onContextChange?.({
      ...context,
      model_name: nextModelName,
      mode: nextMode,
    });
  }, [context, models, onContextChange]);

  const selectedModel = useMemo(() => {
    if (models.length === 0) {
      return undefined;
    }
    return models.find((m) => m.name === context.model_name) ?? models[0];
  }, [context.model_name, models]);

  const resolvedModelName = selectedModel?.name;

  const supportThinking = useMemo(
    () => selectedModel?.supports_thinking ?? false,
    [selectedModel],
  );

  const supportReasoningEffort = useMemo(
    () => selectedModel?.supports_reasoning_effort ?? false,
    [selectedModel],
  );

  const handleModelSelect = useCallback(
    (model_name: string) => {
      const model = models.find((m) => m.name === model_name);
      if (!model) {
        return;
      }
      onContextChange?.({
        ...context,
        model_name,
        mode: resolveMode(context.mode, model.supports_thinking ?? false),
        reasoning_effort: context.reasoning_effort,
      });
      setModelDialogOpen(false);
    },
    [onContextChange, context, models],
  );

  const handleModeSelect = useCallback(
    (mode: InputMode) => {
      onContextChange?.({
        ...context,
        mode: resolveMode(mode, supportThinking),
        reasoning_effort: reasoningEffortForMode(mode),
      });
    },
    [onContextChange, context, supportThinking],
  );

  const handleReasoningEffortSelect = useCallback(
    (effort: "minimal" | "low" | "medium" | "high") => {
      onContextChange?.({
        ...context,
        reasoning_effort: effort,
      });
    },
    [onContextChange, context],
  );

  const handleSubmit = useCallback(
    async (message: PromptInputMessage) => {
      if (status === "streaming") {
        onStop?.();
        return;
      }
      if (!message.text) {
        return;
      }
      // Guard against submitting before the initial model auto-selection
      // effect has flushed thread settings to storage/state.
      if (resolvedModelName && context.model_name !== resolvedModelName) {
        onContextChange?.({
          ...context,
          model_name: resolvedModelName,
          mode: resolveMode(
            context.mode,
            selectedModel?.supports_thinking ?? false,
          ),
        });
        setTimeout(() => onSubmit?.(message), 0);
        return;
      }

      onSubmit?.(message);
    },
    [
      context,
      onContextChange,
      onSubmit,
      onStop,
      resolvedModelName,
      selectedModel?.supports_thinking,
      status,
    ],
  );

  return (
    <div ref={promptRootRef} className="relative flex flex-col gap-4">
      <PromptInput
        className={cn(
          "glass-input-box overflow-visible! rounded-2xl transition-all duration-300 ease-out *:data-[slot='input-group']:rounded-2xl *:data-[slot='input-group']:border-0 *:data-[slot='input-group']:bg-transparent *:data-[slot='input-group']:shadow-none",
          className,
        )}
        style={{ ...glassStyle(), overflow: "visible" }}
        disabled={isDisabled}
        globalDrop
        multiple
        onSubmit={handleSubmit}
        {...props}
      >
        {/* Upload progress bar */}
        {isUploading && (
          <div className="absolute top-0 right-0 left-0 z-20 h-0.5 overflow-hidden rounded-t-2xl bg-white/20">
            <div
              className="h-full bg-gradient-to-r from-[#6366f1] to-[#818cf8] transition-all duration-300 ease-out"
              style={{ width: `${uploadProgress ?? 0}%` }}
            />
          </div>
        )}
        {extraHeader && (
          <div className="absolute top-0 right-0 left-0 z-10">
            <div className="absolute right-0 bottom-0 left-0 flex items-center justify-center">
              {extraHeader}
            </div>
          </div>
        )}
        <PromptInputAttachments>
          {(attachment) => <PromptInputAttachment data={attachment} />}
        </PromptInputAttachments>
        <PromptInputBody className="absolute top-0 right-0 left-0 z-3">
          <PromptInputTextarea
            className={cn(
              "size-full",
              isDark
                ? "text-white placeholder-slate-500"
                : "text-slate-800 placeholder-slate-400",
            )}
            disabled={isDisabled}
            placeholder={t.inputBox.placeholder}
            autoFocus={autoFocus}
          />
        </PromptInputBody>
        <PromptInputFooter className="flex">
          <PromptInputTools>
            {/* TODO: Add more connectors here
          <PromptInputActionMenu>
            <PromptInputActionMenuTrigger className="px-2!" />
            <PromptInputActionMenuContent className="glass-dropdown w-80">
              <PromptInputActionAddAttachments
                label={t.inputBox.addAttachments}
              />
            </PromptInputActionMenuContent>
          </PromptInputActionMenu> */}
            <AddAttachmentsButton className="px-2! hover:bg-indigo-500/10 hover:text-indigo-600 dark:hover:bg-white/10 dark:hover:text-indigo-400" />
            <PolishButton
              className="px-2! hover:bg-indigo-500/10 hover:text-indigo-600 dark:hover:bg-white/10 dark:hover:text-indigo-400"
              disabled={isDisabled || status === "streaming"}
            />
            <PromptInputActionMenu>
              <ModeHoverGuide
                mode={
                  context.mode === "flash" ||
                  context.mode === "thinking" ||
                  context.mode === "pro" ||
                  context.mode === "ultra"
                    ? context.mode
                    : "flash"
                }
              >
                <PromptInputActionMenuTrigger className="gap-1! px-2! hover:bg-indigo-500/10 hover:text-indigo-600 dark:hover:bg-white/10 dark:hover:text-indigo-400">
                  <div>
                    {context.mode === "flash" && <ZapIcon className="size-3" />}
                    {context.mode === "thinking" && (
                      <LightbulbIcon className="size-3" />
                    )}
                    {context.mode === "pro" && (
                      <GraduationCapIcon className="size-3" />
                    )}
                    {context.mode === "ultra" && (
                      <RocketIcon className="size-3 text-[#dabb5e]" />
                    )}
                  </div>
                  <div
                    className={cn(
                      "text-xs font-normal",
                      context.mode === "ultra" ? "golden-text" : "",
                    )}
                  >
                    {(context.mode === "flash" && t.inputBox.flashMode) ||
                      (context.mode === "thinking" &&
                        t.inputBox.reasoningMode) ||
                      (context.mode === "pro" && t.inputBox.proMode) ||
                      (context.mode === "ultra" && t.inputBox.ultraMode)}
                  </div>
                </PromptInputActionMenuTrigger>
              </ModeHoverGuide>
              <PromptInputActionMenuContent className="glass-dropdown w-80">
                <DropdownMenuGroup>
                  <DropdownMenuLabel className="text-muted-foreground text-xs">
                    {t.inputBox.mode}
                  </DropdownMenuLabel>
                  {/* 2026-10-06 拆分批：模式菜单改数据驱动 map（修前 4 份逐字
                      结构复制；ultra 的金色态经 golden 标志保留）。 */}
                  <PromptInputActionMenu>
                    {(
                      [
                        {
                          value: "flash",
                          icon: ZapIcon,
                          label: t.inputBox.flashMode,
                          desc: t.inputBox.flashModeDescription,
                        },
                        {
                          value: "thinking",
                          icon: LightbulbIcon,
                          label: t.inputBox.reasoningMode,
                          desc: supportThinking
                            ? t.inputBox.reasoningModeDescription
                            : t.inputBox.reasoningModeUnavailable,
                          disabled: !supportThinking,
                        },
                        {
                          value: "pro",
                          icon: GraduationCapIcon,
                          label: t.inputBox.proMode,
                          desc: t.inputBox.proModeDescription,
                        },
                        {
                          value: "ultra",
                          icon: RocketIcon,
                          label: t.inputBox.ultraMode,
                          desc: t.inputBox.ultraModeDescription,
                          golden: true,
                        },
                      ] as const
                    ).map((item) => (
                      <PromptInputActionMenuItem
                        key={item.value}
                        disabled={"disabled" in item && item.disabled}
                        className={cn(
                          context.mode === item.value
                            ? "text-accent-foreground"
                            : "text-muted-foreground/65",
                          "disabled" in item &&
                            item.disabled &&
                            "cursor-not-allowed opacity-60",
                        )}
                        onSelect={() => handleModeSelect(item.value)}
                      >
                        <div className="flex flex-col gap-2">
                          <div className="flex items-center gap-1 font-bold">
                            <item.icon
                              className={cn(
                                "mr-2 size-4",
                                context.mode === item.value &&
                                  ("golden" in item && item.golden
                                    ? "text-[#dabb5e]"
                                    : "text-accent-foreground"),
                              )}
                            />
                            {"golden" in item && item.golden ? (
                              <div
                                className={cn(
                                  context.mode === item.value && "golden-text",
                                )}
                              >
                                {item.label}
                              </div>
                            ) : (
                              item.label
                            )}
                          </div>
                          <div className="pl-7 text-xs">{item.desc}</div>
                        </div>
                        {context.mode === item.value ? (
                          <CheckIcon className="ml-auto size-4" />
                        ) : (
                          <div className="ml-auto size-4" />
                        )}
                      </PromptInputActionMenuItem>
                    ))}
                  </PromptInputActionMenu>
                </DropdownMenuGroup>
              </PromptInputActionMenuContent>
            </PromptInputActionMenu>
            {supportReasoningEffort && context.mode !== "flash" && (
              <PromptInputActionMenu>
                <PromptInputActionMenuTrigger className="gap-1! px-2! hover:bg-indigo-500/10 hover:text-indigo-600 dark:hover:bg-white/10 dark:hover:text-indigo-400">
                  <div className="text-xs font-normal">
                    {t.inputBox.reasoningEffort}:
                    {context.reasoning_effort === "minimal" &&
                      " " + t.inputBox.reasoningEffortMinimal}
                    {context.reasoning_effort === "low" &&
                      " " + t.inputBox.reasoningEffortLow}
                    {context.reasoning_effort === "medium" &&
                      " " + t.inputBox.reasoningEffortMedium}
                    {context.reasoning_effort === "high" &&
                      " " + t.inputBox.reasoningEffortHigh}
                  </div>
                </PromptInputActionMenuTrigger>
                <PromptInputActionMenuContent className="glass-dropdown w-70">
                  <DropdownMenuGroup>
                    <DropdownMenuLabel className="text-muted-foreground text-xs">
                      {t.inputBox.reasoningEffort}
                    </DropdownMenuLabel>
                    <PromptInputActionMenu>
                      <PromptInputActionMenuItem
                        className={cn(
                          context.reasoning_effort === "minimal"
                            ? "text-accent-foreground"
                            : "text-muted-foreground/65",
                        )}
                        onSelect={() => handleReasoningEffortSelect("minimal")}
                      >
                        <div className="flex flex-col gap-2">
                          <div className="flex items-center gap-1 font-bold">
                            {t.inputBox.reasoningEffortMinimal}
                          </div>
                          <div className="pl-2 text-xs">
                            {t.inputBox.reasoningEffortMinimalDescription}
                          </div>
                        </div>
                        {context.reasoning_effort === "minimal" ? (
                          <CheckIcon className="ml-auto size-4" />
                        ) : (
                          <div className="ml-auto size-4" />
                        )}
                      </PromptInputActionMenuItem>
                      <PromptInputActionMenuItem
                        className={cn(
                          context.reasoning_effort === "low"
                            ? "text-accent-foreground"
                            : "text-muted-foreground/65",
                        )}
                        onSelect={() => handleReasoningEffortSelect("low")}
                      >
                        <div className="flex flex-col gap-2">
                          <div className="flex items-center gap-1 font-bold">
                            {t.inputBox.reasoningEffortLow}
                          </div>
                          <div className="pl-2 text-xs">
                            {t.inputBox.reasoningEffortLowDescription}
                          </div>
                        </div>
                        {context.reasoning_effort === "low" ? (
                          <CheckIcon className="ml-auto size-4" />
                        ) : (
                          <div className="ml-auto size-4" />
                        )}
                      </PromptInputActionMenuItem>
                      <PromptInputActionMenuItem
                        className={cn(
                          context.reasoning_effort === "medium" ||
                            !context.reasoning_effort
                            ? "text-accent-foreground"
                            : "text-muted-foreground/65",
                        )}
                        onSelect={() => handleReasoningEffortSelect("medium")}
                      >
                        <div className="flex flex-col gap-2">
                          <div className="flex items-center gap-1 font-bold">
                            {t.inputBox.reasoningEffortMedium}
                          </div>
                          <div className="pl-2 text-xs">
                            {t.inputBox.reasoningEffortMediumDescription}
                          </div>
                        </div>
                        {context.reasoning_effort === "medium" ||
                        !context.reasoning_effort ? (
                          <CheckIcon className="ml-auto size-4" />
                        ) : (
                          <div className="ml-auto size-4" />
                        )}
                      </PromptInputActionMenuItem>
                      <PromptInputActionMenuItem
                        className={cn(
                          context.reasoning_effort === "high"
                            ? "text-accent-foreground"
                            : "text-muted-foreground/65",
                        )}
                        onSelect={() => handleReasoningEffortSelect("high")}
                      >
                        <div className="flex flex-col gap-2">
                          <div className="flex items-center gap-1 font-bold">
                            {t.inputBox.reasoningEffortHigh}
                          </div>
                          <div className="pl-2 text-xs">
                            {t.inputBox.reasoningEffortHighDescription}
                          </div>
                        </div>
                        {context.reasoning_effort === "high" ? (
                          <CheckIcon className="ml-auto size-4" />
                        ) : (
                          <div className="ml-auto size-4" />
                        )}
                      </PromptInputActionMenuItem>
                    </PromptInputActionMenu>
                  </DropdownMenuGroup>
                </PromptInputActionMenuContent>
              </PromptInputActionMenu>
            )}
          </PromptInputTools>
          <PromptInputTools>
            <ModelSelector
              open={modelDialogOpen}
              onOpenChange={setModelDialogOpen}
            >
              <ModelSelectorTrigger asChild>
                <PromptInputButton
                  className="hover:bg-indigo-500/10 hover:text-indigo-600 dark:hover:bg-white/10 dark:hover:text-indigo-400"
                  suppressHydrationWarning
                >
                  <div className="flex min-w-0 flex-col items-start text-left">
                    <ModelSelectorName className="text-xs font-normal">
                      {mounted ? selectedModel?.display_name : ""}
                    </ModelSelectorName>
                  </div>
                </PromptInputButton>
              </ModelSelectorTrigger>
              <ModelSelectorContent>
                <ModelSelectorInput placeholder={t.inputBox.searchModels} />
                <ModelSelectorList>
                  {models.map((m) => (
                    <ModelSelectorItem
                      key={m.name}
                      value={m.name}
                      onSelect={() => handleModelSelect(m.name)}
                    >
                      <div className="flex min-w-0 flex-1 flex-col">
                        <ModelSelectorName>{m.display_name}</ModelSelectorName>
                        <span className="text-muted-foreground truncate text-[10px]">
                          {m.model}
                        </span>
                      </div>
                      {m.name === context.model_name ? (
                        <CheckIcon className="ml-auto size-4" />
                      ) : (
                        <div className="ml-auto size-4" />
                      )}
                    </ModelSelectorItem>
                  ))}
                </ModelSelectorList>
              </ModelSelectorContent>
            </ModelSelector>
            <PromptInputSubmit
              className="rounded-full border-none bg-gradient-to-br from-[#6366f1] to-[#4f46e5] text-white shadow-[0_2px_8px_rgba(99,102,241,0.3)] hover:shadow-[0_4px_14px_rgba(99,102,241,0.4)] hover:brightness-110"
              disabled={isDisabled}
              variant="ghost"
              status={status}
            />
          </PromptInputTools>
        </PromptInputFooter>
        {!isNewThread && (
          <div className="absolute right-0 -bottom-[17px] left-0 z-0 h-4"></div>
        )}
      </PromptInput>

      {isNewThread &&
        showNewThreadSuggestions &&
        mounted &&
        searchParams.get("mode") !== "skill" && (
          <div className="absolute top-full right-0 left-0 w-full pt-2">
            <SuggestionList
              agentSkills={agentSkills}
              agentSuggestionPrompts={agentSuggestionPrompts}
            />
          </div>
        )}
    </div>
  );
}

function makeSuggestionClickHandler(
  textInput: ReturnType<typeof usePromptInputController>["textInput"],
) {
  return (prompt: string | undefined) => {
    if (!prompt) return;
    const current = textInput.value;
    const separator = current.trim() ? " " : "";
    const prefix = current.trimEnd() + separator;
    const next = prefix + prompt;
    textInput.setInput(next);
    setTimeout(() => {
      const textarea = document.querySelector<HTMLTextAreaElement>(
        "textarea[name='message']",
      );
      if (textarea) {
        const selStart = next.indexOf("[", prefix.length);
        const selEnd = next.indexOf("]", prefix.length);
        if (selStart !== -1 && selEnd !== -1) {
          textarea.setSelectionRange(selStart, selEnd + 1);
        }
        textarea.focus();
      }
    }, 500);
  };
}

function CategorizedSkillList({ skills: skillNames }: { skills: string[] }) {
  const { skills } = useSkills();
  const { textInput } = usePromptInputController();
  const { glassStyle, isDark } = useGlassStyle();

  const skillMap = useMemo(
    () => new Map(skills.map((s) => [s.name, s])),
    [skills],
  );

  // 计算有哪些分组在当前 skills 中实际存在（隐藏 course 分组）
  const availableGroups = useMemo(() => {
    const groups = new Set<string>();
    for (const name of skillNames) {
      const group = SKILL_TO_GROUP.get(name);
      if (group === "course") continue;
      groups.add(group ?? "other");
    }
    return groups;
  }, [skillNames]);

  // 默认选中第一个有内容的分组
  const firstGroup = useMemo(() => {
    return (
      Object.keys(SKILL_GROUPS).find((g) => availableGroups.has(g)) ?? "other"
    );
  }, [availableGroups]);

  const [activeGroup, setActiveGroup] = useState(firstGroup);

  // 按分类过滤（始终隐藏 course 分组下的技能）
  const filteredSkills = useMemo(() => {
    let result: string[];
    if (activeGroup === "other") {
      result = skillNames.filter((name) => !SKILL_TO_GROUP.has(name));
    } else {
      result = skillNames.filter(
        (name) => SKILL_TO_GROUP.get(name) === activeGroup,
      );
    }
    return result.filter((name) => SKILL_TO_GROUP.get(name) !== "course");
  }, [activeGroup, skillNames]);

  const handleSuggestionClick = useMemo(
    () => makeSuggestionClickHandler(textInput),
    [textInput],
  );

  return (
    <div className="flex w-full flex-col gap-1.5">
      {/* 分类标签行 */}
      <div className="flex flex-wrap items-center gap-1">
        {Object.keys(SKILL_GROUPS)
          .filter((group) => availableGroups.has(group))
          .map((group) => (
            <button
              key={group}
              className={cn(
                "inline-flex h-6 cursor-pointer items-center gap-1 rounded-full px-2.5 text-[11px] font-medium transition-all",
                activeGroup === group
                  ? isDark
                    ? "border border-white/20 bg-white/15 text-white shadow-sm"
                    : "border border-indigo-200 bg-indigo-500 text-white shadow-sm"
                  : isDark
                    ? "border border-white/5 bg-white/5 text-slate-400 hover:bg-white/10 hover:text-white"
                    : "border border-slate-200 bg-slate-50 text-slate-500 hover:bg-slate-100 hover:text-indigo-600",
              )}
              onClick={() => setActiveGroup(group)}
            >
              {(() => {
                const Icon = SKILL_GROUP_ICONS[group];
                return Icon ? <Icon className="h-3 w-3" /> : null;
              })()}
              {SKILL_GROUP_LABELS[group]}
            </button>
          ))}
        <button
          className={cn(
            "inline-flex h-6 cursor-pointer items-center gap-1 rounded-full px-2.5 text-[11px] font-medium transition-all",
            activeGroup === "other"
              ? isDark
                ? "border border-white/20 bg-white/15 text-white shadow-sm"
                : "border border-indigo-200 bg-indigo-500 text-white shadow-sm"
              : isDark
                ? "border border-white/5 bg-white/5 text-slate-400 hover:bg-white/10 hover:text-white"
                : "border border-slate-200 bg-slate-50 text-slate-500 hover:bg-slate-100 hover:text-indigo-600",
          )}
          onClick={() => setActiveGroup("other")}
        >
          {(() => {
            const Icon = SKILL_GROUP_ICONS.other;
            return Icon ? <Icon className="h-3 w-3" /> : null;
          })()}
          {SKILL_GROUP_LABELS.other}
        </button>
      </div>

      {/* Skill 按钮行 */}
      <div className="flex flex-wrap items-start gap-1.5">
        {filteredSkills.map((skillName, index) => {
          const skill = skillMap.get(skillName);
          const btn = (
            <Suggestion
              suggestion={skillName}
              onClick={() => handleSuggestionClick(`/skill ${skillName}`)}
              className={cn(
                isDark
                  ? "border-white/10 bg-white/5 text-slate-300 hover:border-white/20 hover:bg-white/10"
                  : "border-indigo-200/50 bg-indigo-500/5 text-indigo-600 hover:border-indigo-300 hover:bg-indigo-500/10",
              )}
            />
          );
          const inner = !skill?.description?.trim() ? (
            <span>{btn}</span>
          ) : (
            <Tooltip
              content={
                <div className="max-w-[250px] space-y-1 p-1 text-left">
                  <span className="text-sm font-semibold">{skill.name}</span>
                  <p className="text-muted-foreground line-clamp-3 text-xs">
                    {skill.description}
                  </p>
                </div>
              }
            >
              <span>{btn}</span>
            </Tooltip>
          );
          return (
            <span
              key={skillName}
              className="animate-fade-in-up max-w-full opacity-0"
              style={{ animationDelay: `${250 + index * 60}ms` }}
            >
              {inner}
            </span>
          );
        })}
      </div>
    </div>
  );
}

function SuggestionList({
  agentSkills,
  agentSuggestionPrompts,
}: {
  agentSkills?: string[] | null;
  agentSuggestionPrompts?: { label: string; prompt: string }[] | null;
}) {
  const { t } = useI18n();
  const { textInput } = usePromptInputController();
  const { skills } = useSkills();
  const { isDark } = useGlassStyle();
  const skillMap = useMemo(
    () => new Map(skills.map((s) => [s.name, s])),
    [skills],
  );
  const handleSuggestionClick = useMemo(
    () => makeSuggestionClickHandler(textInput),
    [textInput],
  );

  const suggestionCls = cn(
    isDark
      ? "border-white/10 bg-white/5 text-slate-300 hover:border-white/20 hover:bg-white/10"
      : "border-indigo-200/50 bg-indigo-500/5 text-indigo-600 hover:border-indigo-300 hover:bg-indigo-500/10",
  );

  // 当 agent 数据尚未加载（agentSkills === undefined）且 skills 也未加载完成时，不显示任何内容，避免闪烁
  if (agentSkills === undefined && skills.length === 0) {
    return null;
  }

  // 当 agent 没有绑定特定 skills 时，展示分类 Skill 列表
  if ((!agentSkills || agentSkills.length === 0) && skills.length > 0) {
    const enabledSkills = skills.filter((s) => s.enabled).map((s) => s.name);
    if (enabledSkills.length > 0) {
      return <CategorizedSkillList skills={enabledSkills} />;
    }
  }

  // 当 agent 有专属 skills 时，展示原有平铺按钮
  if (agentSkills && agentSkills.length > 0) {
    return (
      <Suggestions className="min-h-16 w-fit items-start">
        <ConfettiButton
          className={cn(
            "cursor-pointer rounded-full px-4 text-xs font-normal",
            suggestionCls,
          )}
          variant="outline"
          size="sm"
          onClick={() => handleSuggestionClick(t.inputBox.surpriseMePrompt)}
        >
          <SparklesIcon className="size-4" /> {t.inputBox.surpriseMe}
        </ConfettiButton>
        {agentSuggestionPrompts
          ?.filter(isRenderableAgentChip)
          .map((chip, i) => (
            <Suggestion
              key={agentChipKey(chip, i)}
              suggestion={chip.label}
              onClick={() => handleSuggestionClick(chip.prompt)}
              className={suggestionCls}
            />
          ))}
        {agentSkills.map((skillName) => {
          const skill = skillMap.get(skillName);
          const btn = (
            <Suggestion
              suggestion={skillName}
              onClick={() => handleSuggestionClick(`/skill ${skillName}`)}
              className={suggestionCls}
            />
          );
          if (!skill?.description?.trim())
            return <span key={skillName}>{btn}</span>;
          return (
            <Tooltip
              key={skillName}
              content={
                <div className="max-w-[250px] space-y-1 p-1 text-left">
                  <span className="text-sm font-semibold">{skill.name}</span>
                  <p className="text-muted-foreground line-clamp-3 text-xs">
                    {skill.description}
                  </p>
                </div>
              }
            >
              <span>{btn}</span>
            </Tooltip>
          );
        })}
      </Suggestions>
    );
  }

  // Default: show generic suggestions
  return (
    <Suggestions className="min-h-16 w-fit items-start">
      <ConfettiButton
        className={cn(
          "cursor-pointer rounded-full px-4 text-xs font-normal",
          suggestionCls,
        )}
        variant="outline"
        size="sm"
        onClick={() => handleSuggestionClick(t.inputBox.surpriseMePrompt)}
      >
        <SparklesIcon className="size-4" /> {t.inputBox.surpriseMe}
      </ConfettiButton>
      {agentSuggestionPrompts?.filter(isRenderableAgentChip).map((chip, i) => (
        <Suggestion
          key={agentChipKey(chip, i)}
          suggestion={chip.label}
          onClick={() => handleSuggestionClick(chip.prompt)}
          className={suggestionCls}
        />
      ))}
      {t.inputBox.suggestions.map((suggestion) => (
        <Suggestion
          key={suggestion.suggestion}
          icon={suggestion.icon}
          suggestion={suggestion.suggestion}
          onClick={() => handleSuggestionClick(suggestion.prompt)}
          className={suggestionCls}
        />
      ))}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Suggestion
            icon={PlusIcon}
            suggestion={t.common.create}
            className={suggestionCls}
          />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start">
          <DropdownMenuGroup>
            {t.inputBox.suggestionsCreate.map((suggestion, index) =>
              "type" in suggestion && suggestion.type === "separator" ? (
                <DropdownMenuSeparator key={index} />
              ) : (
                !("type" in suggestion) && (
                  <DropdownMenuItem
                    key={suggestion.suggestion}
                    onClick={() => handleSuggestionClick(suggestion.prompt)}
                  >
                    {suggestion.icon && <suggestion.icon className="size-4" />}
                    {suggestion.suggestion}
                  </DropdownMenuItem>
                )
              ),
            )}
          </DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>
    </Suggestions>
  );
}

function AddAttachmentsButton({ className }: { className?: string }) {
  const { t } = useI18n();
  const attachments = usePromptInputAttachments();
  return (
    <Tooltip content={t.inputBox.addAttachments}>
      <PromptInputButton
        className={cn("px-2!", className)}
        onClick={() => attachments.openFileDialog()}
      >
        <PaperclipIcon className="size-3" />
      </PromptInputButton>
    </Tooltip>
  );
}

/**
 * 一键润色（P1-11）：把 composer 里的草稿交给 `/api/ai/assist` 改写，结果
 * 逐块流式回填输入框——与 writer 的选区润色共用同一条 SSE 通道
 * （`streamAssist`），后端零改动。
 *
 * 失败必须可见且可恢复：出错时 toast 报错并把输入框回滚成原文，绝不留一段
 * 半成品把用户自己写的东西盖掉（仓库有过 mutation 漏 onError 静默吞错的先例）。
 */
function PolishButton({
  className,
  disabled,
}: {
  className?: string;
  disabled?: boolean;
}) {
  const { t } = useI18n();
  const { textInput } = usePromptInputController();
  const [polishing, setPolishing] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  // 卸载时掐断在飞的流，否则组件没了还在往已卸载的 state 里写。
  useEffect(() => () => abortRef.current?.abort(), []);

  const isEmpty = textInput.value.trim().length === 0;
  const isDisabled = disabled === true || isEmpty || polishing;

  const handlePolish = useCallback(async () => {
    const source = textInput.value;
    if (source.trim().length === 0 || abortRef.current) {
      return;
    }
    const controller = new AbortController();
    abortRef.current = controller;
    setPolishing(true);

    let accumulated = "";
    try {
      for await (const chunk of streamAssist(
        { op: "polish", text: source },
        controller.signal,
      )) {
        if (controller.signal.aborted) return;
        accumulated += chunk;
        textInput.setInput(accumulated);
      }
      if (controller.signal.aborted) return;
      if (accumulated.trim().length === 0) {
        // 空回复不能当成"润色成功"——那等于悄悄清空用户的草稿。
        textInput.setInput(source);
        toast.error(t.inputBox.polishEmpty);
      }
    } catch (err) {
      if (controller.signal.aborted) return;
      textInput.setInput(source);
      toast.error(
        `${t.inputBox.polishFailed}：${err instanceof Error ? err.message : String(err)}`,
      );
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null;
        setPolishing(false);
      }
    }
  }, [t, textInput]);

  return (
    <Tooltip content={polishing ? t.inputBox.polishing : t.inputBox.polish}>
      <PromptInputButton
        aria-label={t.inputBox.polish}
        className={cn("px-2!", className)}
        disabled={isDisabled}
        onClick={() => void handlePolish()}
      >
        {polishing ? (
          <Loader2Icon className="size-3 animate-spin" />
        ) : (
          <WandSparklesIcon className="size-3" />
        )}
      </PromptInputButton>
    </Tooltip>
  );
}
