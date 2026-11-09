/**
 * writer SSE 解析共享件（2026-10-06 近重复合并批）。
 *
 * 修前 streamAssist（api.ts）、streamAgentEdit（api.ts）、parseScratchSSE
 * （use-from-scratch.ts）三处各持一份「reader → \n\n 分帧（B1-T7）→ event/data
 * 行解析」循环，其中 agentEdit 与 scratch 的 16 行非 AI 消息黑名单和消息文本
 * 提取逐字双份。收敛至此：
 * - sseFrames：帧拆分 reader（\n\n 分界，兼容含换行的 JSON payload）
 * - parseSseEvent：event:/data: 行解析（同帧多行后者覆盖）
 * - isNonAiMessage：非 AI 消息黑名单（黑名单而非白名单——无 type 的 AI chunk 仍渲染）
 * - extractMessageText：string / {type:"text"}[] / string[] 内容提取
 */

/** SSE 帧拆分：yield 每帧的原始行数组；流耗尽或提前退出时 cancel reader。 */
export async function* sseFrames(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<string[]> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";
      for (const frame of frames) yield frame.split("\n");
    }
  } finally {
    // 提前退出（消费者 return/break）时释放连接；mock reader 可能缺 cancel/返回非 Promise
    try {
      void Promise.resolve(reader.cancel?.()).catch(() => undefined);
    } catch {
      /* noop */
    }
  }
}

/** event:/data: 行解析（无 event 或无 data 返回 null；同名字段后者覆盖）。 */
export function parseSseEvent(lines: string[]): {
  event: string;
  data: string;
} | null {
  let eventType = "";
  let eventData = "";
  for (const line of lines) {
    if (line.startsWith("event: ")) eventType = line.slice(7).trim();
    else if (line.startsWith("data: ")) eventData = line.slice(6);
  }
  if (!eventType || !eventData) return null;
  return { event: eventType, data: eventData };
}

/**
 * 非 AI 消息黑名单：ToolMessage 带原始搜索 API JSON（泄漏进扩写/从零创作输出，
 * issue ④），human/system 同理不入 AI 文本。判 type/_type 与 role 双口径，
 * 外加 tool_call_id（仅 ToolMessage 携带）。黑名单而非白名单——无 type 的
 * AI chunk 仍要渲染。
 */
export function isNonAiMessage(msg: Record<string, unknown>): boolean {
  // 非 string 的 type/role 视同缺席（原 String() 兜底 "[object Object]" 也命不中黑名单）
  const typeField = msg.type ?? msg._type;
  const msgType = typeof typeField === "string" ? typeField.toLowerCase() : "";
  const roleField = msg.role;
  const msgRole = typeof roleField === "string" ? roleField.toLowerCase() : "";
  return (
    msgType === "toolmessage" ||
    msgType === "tool" ||
    msgType === "humanmessage" ||
    msgType === "human" ||
    msgType === "systemmessage" ||
    msgType === "system" ||
    msgType === "functionmessage" ||
    msgType === "function" ||
    msgRole === "tool" ||
    msgRole === "user" ||
    msgRole === "system" ||
    msg.tool_call_id != null
  );
}

/** 消息文本提取：string 内容 / [{type:"text",text}] / 混排 string。 */
export function extractMessageText(msg: { content?: unknown }): string {
  const content = msg.content;
  if (typeof content === "string") {
    return content;
  }
  let text = "";
  if (Array.isArray(content)) {
    for (const part of content) {
      if (typeof part === "object" && part?.type === "text") {
        text += part.text ?? "";
      } else if (typeof part === "string") {
        text += part;
      }
    }
  }
  return text;
}
