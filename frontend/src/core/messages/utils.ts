import type { AIMessage, Message } from "@langchain/langgraph-sdk";

import { parseStageSignal } from "@/core/writer/from-scratch-stage";

interface GenericMessageGroup<T = string> {
  type: T;
  id: string | undefined;
  messages: Message[];
}

interface HumanMessageGroup extends GenericMessageGroup<"human"> {}

interface AssistantProcessingGroup extends GenericMessageGroup<"assistant:processing"> {}

interface AssistantMessageGroup extends GenericMessageGroup<"assistant"> {}

interface AssistantPresentFilesGroup extends GenericMessageGroup<"assistant:present-files"> {}

interface AssistantClarificationGroup extends GenericMessageGroup<"assistant:clarification"> {}

interface AssistantSubagentGroup extends GenericMessageGroup<"assistant:subagent"> {}

interface AssistantStoryboardGroup extends GenericMessageGroup<"assistant:storyboard"> {}

type MessageGroup =
  | HumanMessageGroup
  | AssistantProcessingGroup
  | AssistantMessageGroup
  | AssistantPresentFilesGroup
  | AssistantClarificationGroup
  | AssistantSubagentGroup
  | AssistantStoryboardGroup;

// 对外用 ThreadMessageGroup 这个名字：`MessageGroup` 已经被同名**组件**占用
// （components/workspace/messages/message-group.tsx），同文件里两个都 import
// 会撞名。
export type { MessageGroup as ThreadMessageGroup };

export function getMessageGroups(messages: Message[]): MessageGroup[] {
  if (messages.length === 0) {
    return [];
  }

  const groups: MessageGroup[] = [];

  // PR-2 4.1：组 id 主键 = message.id（稳定——历史分页 prepend 后不再全量重
  // 编号，虚拟化的测量缓存与滚动锚定依赖这一点；原 `-g{seq}` 计数器让每次
  // 重分组全量换 id，prepend 即缓存失效+滚动跳动）。LangGraph 可能跨多条消息
  // 复用同一 lc_run--UUID（原计数器的存在理由）——同 id 重复追加 #dup-N 保住
  // React key 的全局唯一性。
  const dupCounts = new Map<string, number>();
  const uniqueId = (messageId: string | undefined) => {
    const base = messageId ?? "msg";
    const n = dupCounts.get(base) ?? 0;
    dupCounts.set(base, n + 1);
    return n === 0 ? base : `${base}#dup-${n}`;
  };

  // Returns the last group if it can still accept tool messages
  // (i.e. it's an in-flight processing, subagent OR storyboard group — a
  // tool result must land in the group whose card parses it; for
  // plan_storyboard that's the storyboard-confirm card, 2026-07-27 fix).
  function lastOpenGroup() {
    const last = groups[groups.length - 1];
    if (
      last &&
      last.type !== "human" &&
      last.type !== "assistant" &&
      last.type !== "assistant:clarification" &&
      last.type !== "assistant:present-files"
    ) {
      return last;
    }
    return null;
  }

  for (const message of messages) {
    if (isHiddenFromUIMessage(message)) {
      continue;
    }

    if (message.name === "todo_reminder") {
      continue;
    }

    if (message.type === "human") {
      groups.push({
        id: uniqueId(message.id),
        type: "human",
        messages: [message],
      });
      continue;
    }

    if (message.type === "tool") {
      if (isClarificationToolMessage(message)) {
        // Add to the preceding processing group to preserve tool-call association,
        // then also open a standalone clarification group for prominent display.
        lastOpenGroup()?.messages.push(message);
        groups.push({
          id: uniqueId(message.id),
          type: "assistant:clarification",
          messages: [message],
        });
      } else {
        const open = lastOpenGroup();
        if (open) {
          open.messages.push(message);
        } else {
          // Transient streaming state: a tool result landed in the rendered
          // list before its AI tool-call message was grouped (or after a
          // terminal group). The persisted thread state is always well-formed —
          // this only appears mid-stream while messages arrive incrementally.
          // Recover by opening a processing group so the result renders instead
          // of being silently dropped (formerly console.error'd + lost).
          groups.push({
            id: uniqueId(message.id),
            type: "assistant:processing",
            messages: [message],
          });
        }
      }
      continue;
    }

    if (message.type === "ai") {
      if (hasPresentFiles(message)) {
        groups.push({
          id: uniqueId(message.id),
          type: "assistant:present-files",
          messages: [message],
        });
      } else if (hasSubagent(message)) {
        groups.push({
          id: uniqueId(message.id),
          type: "assistant:subagent",
          messages: [message],
        });
      } else if (hasStoryboard(message)) {
        // plan_storyboard produces a draft blueprint the user confirms before render.
        // Group it separately so chat renders a storyboard-confirm card (not the
        // generic processing bucket where the result JSON would be invisible).
        groups.push({
          id: uniqueId(message.id),
          type: "assistant:storyboard",
          messages: [message],
        });
      } else if (hasReasoning(message) || hasToolCalls(message)) {
        const lastGroup = groups[groups.length - 1];
        // Accumulate consecutive intermediate AI messages into one processing group.
        if (lastGroup?.type !== "assistant:processing") {
          groups.push({
            id: uniqueId(message.id),
            type: "assistant:processing",
            messages: [message],
          });
        } else {
          lastGroup.messages.push(message);
        }
      }

      // Not an else-if: a message with reasoning + content (but no tool calls) goes
      // into the processing group above AND gets its own assistant bubble here.
      if (hasContent(message) && !hasToolCalls(message)) {
        groups.push({
          id: uniqueId(message.id),
          type: "assistant",
          messages: [message],
        });
      }
    }
  }

  return groups;
}

/**
 * 把「内容没变」的组换回上一次的对象引用，让 memo 化的 MessageGroup 能跳过。
 *
 * 单靠 useMemo 不够：流式期间 `messages` 数组引用必然变，useMemo 每次都会
 * 重算并产出全新 group 对象，memo 比较 props 时逐个不相等 → 全量重渲染。
 *
 * `isCurrentTurnLoading` 为真时**末组永不复用**：SDK 会原地 mutate 消息对象
 * 追加 token，复用旧引用会让流式输出卡住不动。
 *
 * B2：除末组外，中段组（`streamSubgraphs` 下子图 token 可能落到中段的
 * subagent/processing 组）也会被 SDK 原地 mutate。引用相等（`===`）看不出原地改
 * 内容，故 refs 全同后再比一个廉价的内容指纹（content 长度 + tool_calls 数）。
 * 指纹存 WeakMap（按 group 对象键），不污染导出类型。
 */
const _groupFingerprints = new WeakMap<MessageGroup, string>();

function _messageContentFingerprint(message: Message): string {
  // 廉价但能抓「原地追加 token」的签名：内容长度 + tool_calls 数。不做深比较——
  // canReuseMessageGroup 已先比消息数量，这里只需检测任一条消息的内容变化。
  // Message 是联合类型（HumanMessage 没有 tool_calls 字段），cast 成宽松形状以通过 tsc。
  const m = message as { content: unknown; tool_calls?: unknown[] };
  const content = m.content;
  const contentLen =
    typeof content === "string"
      ? content.length
      : Array.isArray(content)
        ? content.length
        : 0;
  const toolCallsLen = Array.isArray(m.tool_calls) ? m.tool_calls.length : 0;
  return `${contentLen}:${toolCallsLen}`;
}

function _groupContentFingerprint(messages: readonly Message[]): string {
  let fp = "";
  for (const m of messages) {
    fp += _messageContentFingerprint(m) + "|";
  }
  return fp;
}

export function reuseMessageGroups(
  previousGroups: readonly MessageGroup[],
  nextGroups: MessageGroup[],
  isCurrentTurnLoading: boolean,
): MessageGroup[] {
  const activeGroupIndex = isCurrentTurnLoading ? nextGroups.length - 1 : -1;
  return nextGroups.map((group, index) => {
    if (index === activeGroupIndex) {
      _groupFingerprints.set(group, _groupContentFingerprint(group.messages));
      return group;
    }
    const previous = previousGroups[index];
    if (canReuseMessageGroup(previous, group)) {
      return previous; // 指纹已在 canReuse 里核对一致，复用旧引用
    }
    _groupFingerprints.set(group, _groupContentFingerprint(group.messages));
    return group;
  });
}

function canReuseMessageGroup(
  previous: MessageGroup | undefined,
  next: MessageGroup,
): previous is MessageGroup {
  if (
    !previous ||
    previous.id !== next.id ||
    previous.type !== next.type ||
    previous.messages.length !== next.messages.length
  ) {
    return false;
  }
  if (
    !previous.messages.every(
      (message, index) => message === next.messages[index],
    )
  ) {
    return false;
  }
  // refs 全同——但 SDK 可能原地 mutate 了消息内容（追加 token）。比上一次渲染时
  // 存下的指纹与当前内容指纹：不一致即内容变了，必须换新引用重渲染（B2）。
  return (
    _groupFingerprints.get(previous) === _groupContentFingerprint(next.messages)
  );
}

export function extractTextFromMessage(message: Message) {
  if (typeof message.content === "string") {
    return (
      splitInlineReasoningFromAIMessage(message)?.content ??
      message.content.trim()
    );
  }
  if (Array.isArray(message.content)) {
    return message.content
      .map((content) => (content.type === "text" ? content.text : ""))
      .join("\n")
      .trim();
  }
  return "";
}

const THINK_TAG_RE = /<think>\s*([\s\S]*?)\s*<\/think>/g;

function splitInlineReasoning(content: string) {
  const reasoningParts: string[] = [];
  const cleaned = content
    .replace(THINK_TAG_RE, (_, reasoning: string) => {
      const normalized = reasoning.trim();
      if (normalized) {
        reasoningParts.push(normalized);
      }
      return "";
    })
    .trim();

  return {
    content: cleaned,
    reasoning: reasoningParts.length > 0 ? reasoningParts.join("\n\n") : null,
  };
}

function splitInlineReasoningFromAIMessage(message: Message) {
  if (message.type !== "ai" || typeof message.content !== "string") {
    return null;
  }
  return splitInlineReasoning(message.content);
}

export function extractContentFromMessage(message: Message) {
  if (typeof message.content === "string") {
    return (
      splitInlineReasoningFromAIMessage(message)?.content ??
      message.content.trim()
    );
  }
  if (Array.isArray(message.content)) {
    return message.content
      .map((content) => {
        switch (content.type) {
          case "text":
            return content.text;
          case "image_url":
            const imageURL = extractURLFromImageURLContent(content.image_url);
            return `![image](${imageURL})`;
          default:
            return "";
        }
      })
      .join("\n")
      .trim();
  }
  return "";
}

export function extractReasoningContentFromMessage(message: Message) {
  if (message.type !== "ai") {
    return null;
  }
  if (
    message.additional_kwargs &&
    "reasoning_content" in message.additional_kwargs
  ) {
    return message.additional_kwargs.reasoning_content as string | null;
  }
  if (Array.isArray(message.content)) {
    const part = message.content[0];
    if (part && "thinking" in part) {
      return part.thinking as string;
    }
  }
  if (typeof message.content === "string") {
    return splitInlineReasoning(message.content).reasoning;
  }
  return null;
}

export function extractURLFromImageURLContent(
  content:
    | string
    | {
        url: string;
      },
) {
  if (typeof content === "string") {
    return content;
  }
  return content.url;
}

export function hasContent(message: Message) {
  if (typeof message.content === "string") {
    return (
      (
        splitInlineReasoningFromAIMessage(message)?.content ??
        message.content.trim()
      ).length > 0
    );
  }
  if (Array.isArray(message.content)) {
    return message.content.length > 0;
  }
  return false;
}

export function hasReasoning(message: Message) {
  if (message.type !== "ai") {
    return false;
  }
  if (typeof message.additional_kwargs?.reasoning_content === "string") {
    return true;
  }
  if (Array.isArray(message.content)) {
    const part = message.content[0];
    // Compatible with the Anthropic gateway
    return (part as unknown as { type: "thinking" })?.type === "thinking";
  }
  if (typeof message.content === "string") {
    return splitInlineReasoning(message.content).reasoning !== null;
  }
  return false;
}

export function hasToolCalls(message: Message) {
  return (
    message.type === "ai" && message.tool_calls && message.tool_calls.length > 0
  );
}

export function hasPresentFiles(message: Message) {
  return (
    message.type === "ai" &&
    message.tool_calls?.some((toolCall) => toolCall.name === "present_files")
  );
}

export function hasStoryboard(message: Message) {
  return (
    message.type === "ai" &&
    message.tool_calls?.some((toolCall) => toolCall.name === "plan_storyboard")
  );
}

/**
 * Extract the draft task_id from a storyboard group's messages.
 * plan_storyboard's tool_result content is a JSON string like
 * {"task_id":"vg_xxx","status":"draft",...}; we parse it out so the
 * StoryboardConfirmCard can fetch the full storyboard via useStoryboard(taskId).
 * Returns null if no task_id is found (e.g. tool hasn't returned yet mid-stream).
 */
export function extractStoryboardTaskId(messages: Message[]): string | null {
  for (const message of messages) {
    if (message.type !== "tool" || message.name !== "plan_storyboard") {
      continue;
    }
    const text = extractTextFromMessage(message);
    try {
      const parsed = JSON.parse(text) as { task_id?: string };
      if (parsed.task_id) {
        return parsed.task_id;
      }
    } catch {
      // result not JSON / not yet returned
    }
  }
  return null;
}

export function isClarificationToolMessage(message: Message) {
  return message.type === "tool" && message.name === "ask_clarification";
}

/**
 * Normalize the `options` argument of an `ask_clarification` tool call into a
 * list of choices the UI can render as buttons.
 *
 * Some models (e.g. Qwen3-Max) serialize array parameters as a JSON string
 * instead of a native array — the backend's ClarificationMiddleware already
 * normalizes this, and the frontend reads the raw tool-call args, so it has to
 * do the same. Anything that is not a non-empty string is dropped.
 */
export function extractClarificationOptions(options: unknown): string[] {
  let list: unknown = options;
  if (typeof list === "string") {
    try {
      list = JSON.parse(list);
    } catch {
      list = [options];
    }
  }
  if (!Array.isArray(list)) {
    return [];
  }
  return list
    .filter((option): option is string => typeof option === "string")
    .map((option) => option.trim())
    .filter(Boolean);
}

export function extractPresentFilesFromMessage(message: Message) {
  if (message.type !== "ai" || !hasPresentFiles(message)) {
    return [];
  }
  const files: string[] = [];
  for (const toolCall of message.tool_calls ?? []) {
    if (
      toolCall.name === "present_files" &&
      Array.isArray(toolCall.args.filepaths)
    ) {
      files.push(...(toolCall.args.filepaths as string[]));
    }
  }
  return files;
}

export function hasSubagent(message: AIMessage) {
  for (const toolCall of message.tool_calls ?? []) {
    if (toolCall.name === "task") {
      return true;
    }
  }
  return false;
}

export function findToolCallResult(toolCallId: string, messages: Message[]) {
  for (const message of messages) {
    if (message.type === "tool" && message.tool_call_id === toolCallId) {
      const content = extractTextFromMessage(message);
      if (content) {
        return content;
      }
    }
  }
  return undefined;
}

export function isHiddenFromUIMessage(message: Message) {
  return (
    message.additional_kwargs?.hide_from_ui === true ||
    message.name === "summary" ||
    message.name === "loop_warning"
  );
}

/**
 * Represents a file stored in message additional_kwargs.files.
 * Used for optimistic UI (uploading state) and structured file metadata.
 */
export interface FileInMessage {
  filename: string;
  size: number; // bytes
  path?: string; // virtual path, may not be set during upload
  status?: "uploading" | "uploaded";
}

/**
 * Strip <uploaded_files> tag from message content.
 * Returns the content with the tag removed.
 */
export function stripUploadedFilesTag(content: string): string {
  return content
    .replace(/<uploaded_files>[\s\S]*?<\/uploaded_files>/g, "")
    .trim();
}

/**
 * Tag names that backend middlewares wrap around internal payloads before
 * letting them ride along inside LangGraph message ``content``.
 *
 * These markers are *not* user copy — they come from:
 *
 * - ``UploadsMiddleware`` → ``<uploaded_files>``
 * - ``DynamicContextMiddleware`` → ``<system-reminder>`` (carrying
 *   ``<memory>`` / ``<current_date>`` inside)
 * - ``TodoListMiddleware`` / ``TerminalResponseMiddleware`` /
 *   ``WorkspaceInventoryMiddleware`` style reminders use the
 *   underscore spellings ``<system_reminder>`` / ``<workspace_files>``
 *   (DEF-03, 2026-09-23: the hyphen-only regex never matched them).
 *
 * The primary export filter is {@link isHiddenFromUIMessage}. This list is
 * the defence-in-depth strip for any message that — by middleware bug,
 * provider quirk, or merge-conflict regression — slips through without
 * its ``hide_from_ui`` flag set.
 */
export const INTERNAL_MARKER_TAGS = [
  "uploaded_files",
  "system-reminder",
  "system_reminder",
  "workspace_files",
  "memory",
  "current_date",
] as const;

const INTERNAL_MARKER_RE = new RegExp(
  `<(${INTERNAL_MARKER_TAGS.join("|")})>[\\s\\S]*?</\\1>`,
  "g",
);

const STAGE_SIGNAL_PREFIXES = ["[[STAGE:OUTLINE]]", "[[STAGE:DRAFT]]"];

/**
 * Strip every known backend-injected marker from message content.
 *
 * Used by the chat export path (privacy regression if a marker leaks) and,
 * since DEF-03 (2026-09-23 plan B4), by the AI-message render path for
 * legacy threads whose checkpoints predate ``hide_from_ui`` on injected
 * reminders. Human messages keep using the narrower
 * {@link stripUploadedFilesTag} + {@link stripInjectedReminderTags} so a
 * user legitimately typing ``<memory>`` into a meta-discussion is not
 * eaten. Co-writer stage signals are likewise internal metadata, not user copy.
 * The default streaming mode withholds an isolated partial stage signal;
 * final mode instead preserves isolated bracket text and removes only a
 * recognizable incomplete stage signal before any following body.
 * Render-path callers must memoize the result (message content unchanged →
 * same stripped string), per the DEF-06 hot-path rule.
 */
export function stripInternalMarkers(
  content: string,
  mode: "streaming" | "final" = "streaming",
): string {
  const stripped = content.replace(INTERNAL_MARKER_RE, "").trim();
  const parsed = parseStageSignal(stripped);
  if (parsed.stage !== null) {
    return parsed.text.trim();
  }
  if (
    mode === "streaming" &&
    STAGE_SIGNAL_PREFIXES.some((prefix) => prefix.startsWith(stripped))
  ) {
    return "";
  }
  if (mode === "final" && stripped.startsWith("[[STAGE:")) {
    const firstLine = stripped.split(/\r?\n/, 1)[0] ?? "";
    if (STAGE_SIGNAL_PREFIXES.some((prefix) => prefix.startsWith(firstLine))) {
      return stripped.slice(firstLine.length).trim();
    }
  }
  return stripped;
}

/**
 * Underscore-variant markers that get injected into *human* message content
 * (TodoListMiddleware reminders, WorkspaceInventoryMiddleware inventory
 * blocks). Legacy threads may carry them without ``hide_from_ui``; strip
 * them at render time. Deliberately excludes ``<memory>`` /
 * ``<current_date>`` — those ride in ``<system-reminder>``-wrapped
 * system context, never bare inside user copy.
 */
const INJECTED_REMINDER_TAGS = ["system_reminder", "workspace_files"] as const;

const INJECTED_REMINDER_RE = new RegExp(
  `<(${INJECTED_REMINDER_TAGS.join("|")})>[\\s\\S]*?</\\1>`,
  "g",
);

/**
 * Strip backend-injected reminder tags from human message content — the
 * render-path counterpart of {@link stripUploadedFilesTag} (DEF-03, B4).
 */
export function stripInjectedReminderTags(content: string): string {
  return content.replace(INJECTED_REMINDER_RE, "").trim();
}

export function parseUploadedFiles(content: string): FileInMessage[] {
  // Match <uploaded_files>...</uploaded_files> tag
  const uploadedFilesRegex = /<uploaded_files>([\s\S]*?)<\/uploaded_files>/;
  // eslint-disable-next-line @typescript-eslint/prefer-regexp-exec
  const match = content.match(uploadedFilesRegex);

  if (!match) {
    return [];
  }

  const uploadedFilesContent = match[1];

  // Check if it's "No files have been uploaded yet."
  if (uploadedFilesContent?.includes("No files have been uploaded yet.")) {
    return [];
  }

  // Check if the backend reported no new files were uploaded in this message
  if (uploadedFilesContent?.includes("(empty)")) {
    return [];
  }

  // Parse file list
  // Format: - filename (size)\n  Path: /path/to/file
  const fileRegex = /- ([^\n(]+)\s*\(([^)]+)\)\s*\n\s*Path:\s*([^\n]+)/g;
  const files: FileInMessage[] = [];
  let fileMatch;

  while ((fileMatch = fileRegex.exec(uploadedFilesContent ?? "")) !== null) {
    files.push({
      filename: fileMatch[1].trim(),
      size: parseInt(fileMatch[2].trim(), 10) ?? 0,
      path: fileMatch[3].trim(),
    });
  }

  return files;
}
