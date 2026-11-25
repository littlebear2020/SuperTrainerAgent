"use client";

import { NotebookPenIcon } from "lucide-react";
import { useEffect, useRef } from "react";

import {
  ChainOfThoughtSearchResult,
  ChainOfThoughtStep,
} from "@/components/ai-elements/chain-of-thought";
import { useArtifacts } from "@/components/workspace/artifacts";
import { createWriteFileRef } from "@/core/artifacts/write-file-ref";
import { useI18n } from "@/core/i18n/hooks";

/** 自动打开前留给用户手选的时间：这期间用户选了别的产物就不抢。 */
const AUTO_OPEN_DELAY_MS = 100;

type WriteFileToolCallProps = {
  id?: string;
  messageId?: string;
  args: Record<string, unknown>;
  /** 流式参数的原始 JSON 串（`tool_call_chunks` 拼接）；缺省 = 历史消息，`args` 已完整。 */
  rawArgs?: string;
  result?: string | Record<string, unknown>;
  isLast?: boolean;
  isLoading?: boolean;
};

/**
 * 参数完整的判定：有原始串（流式中）时以严格的 `JSON.parse` 成功为准——SDK 交付的
 * `args` 是宽松的部分 JSON 解析结果，半截 path 也在里面；没有原始串（历史消息）时
 * `args` 即完整。不完整时返回空对象：不显示 path、不构造引用。
 */
function completeToolArgs(
  args: Record<string, unknown>,
  rawArgs: string | undefined,
): Record<string, unknown> {
  if (rawArgs === undefined)
    return args && typeof args === "object" ? args : {};
  try {
    const parsed: unknown = JSON.parse(rawArgs);
    return parsed !== null &&
      typeof parsed === "object" &&
      !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : {};
  } catch {
    return {};
  }
}

/**
 * write_file / str_replace 的工具步骤（PR4）。独立成组件是为了让 auto-open 的 effect
 * 位于组件顶层（hook 不能写在 ToolCall 的条件分支里）：流式参数一段段到，半截 path
 * 不构造引用、不选中；参数完整后只试一次，用户在这 100ms 内手选了就不抢，卸载不留
 * timer。修前的 timer 是在渲染期创建的——每次渲染都新建、从不清理，半截 path 也会被
 * 拿去构造引用。
 */
export function WriteFileToolCall({
  id,
  messageId,
  args,
  rawArgs,
  result,
  isLast = false,
  isLoading = false,
}: WriteFileToolCallProps) {
  const { t } = useI18n();
  const { setOpen, autoOpen, autoSelect, selectedArtifact, select } =
    useArtifacts();

  const completeArgs = completeToolArgs(args, rawArgs);
  const description =
    typeof completeArgs.description === "string" && completeArgs.description
      ? completeArgs.description
      : t.toolCalls.writeFile;
  const path: string | undefined =
    typeof completeArgs.path === "string" ? completeArgs.path : undefined;
  // PR4：path 作为独立 query 参数编码；缺任一定位字段就不生成引用
  const ref = createWriteFileRef({ path, messageId, toolCallId: id });
  const hasResult = result !== undefined;

  // effect 的依赖只放原始值：select / setOpen 每次渲染可能是新引用，放进依赖会让
  // 计时器被反复清掉重排
  const actionsRef = useRef({ select, setOpen });
  useEffect(() => {
    actionsRef.current = { select, setOpen };
  });
  const selectedRef = useRef(selectedArtifact);
  useEffect(() => {
    selectedRef.current = selectedArtifact;
  }, [selectedArtifact]);
  /**
   * 已经尝试过 auto-open 的 toolCallId：同一个调用只试一次——计时器真正触发就算尝试过，
   * 哪怕这次因为用户手选了别的产物而让位。
   */
  const attemptedRef = useRef<string | undefined>(undefined);

  useEffect(() => {
    if (!ref || !id) return;
    if (!(isLoading && isLast && autoOpen && autoSelect) || hasResult) return;
    if (attemptedRef.current === id) return;
    // 快照取自本次调度那一刻；依赖变化会清掉计时器重新调度、重新取快照，但用户的手选
    // 会同时把 autoSelect 关掉（见 artifacts/context 的 select），条件不再成立、不会重排
    const selectionAtSchedule = selectedRef.current;
    const timer = setTimeout(() => {
      // 计时器真正触发才算尝试过
      attemptedRef.current = id;
      // 用户在这段时间里手选了别的产物：不抢选
      if (selectedRef.current !== selectionAtSchedule) return;
      const { select: selectNow, setOpen: openNow } = actionsRef.current;
      selectNow(ref, true);
      openNow(true);
    }, AUTO_OPEN_DELAY_MS);
    return () => clearTimeout(timer);
  }, [ref, id, isLoading, isLast, autoOpen, autoSelect, hasResult]);

  return (
    <ChainOfThoughtStep
      className="cursor-pointer"
      label={description}
      icon={NotebookPenIcon}
      onClick={() => {
        if (!ref) return;
        select(ref);
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
}
