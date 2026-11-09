import { expect, test } from "vitest";

import {
  accumulateChunk,
  buildScratchPrompt,
  detectTruncation,
  DIRECT_GENERATE_HINT,
  finalizePlaceholderOnError,
  parseScratchSSE,
  shouldForceDirectGenerate,
  SKIP_CLARIFICATION_INSTRUCTION,
  type ScratchMessage,
} from "@/core/writer/use-from-scratch";

// ── Helpers ──────────────────────────────────────────────────────────────

/** Encode a string to Uint8Array. */
function encode(str: string): Uint8Array {
  return new TextEncoder().encode(str);
}

/** Create a ReadableStream that emits chunks then closes. */
function makeStream(chunks: Uint8Array[]): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(chunk);
      }
      controller.close();
    },
  });
}

/** Build an SSE frame string. */
function sseFrame(event: string, data: string): string {
  return `event: ${event}\ndata: ${data}\n\n`;
}

/** Create a ReadableStream that delivers chunks via `pull` then errors on the
 * next pull after all chunks are consumed. This simulates a network error or
 * abort mid-stream (some chunks arrive, then the stream errors). */
function makeErrorAfterChunksStream(
  chunks: Uint8Array[],
  error: Error,
): ReadableStream<Uint8Array> {
  let index = 0;
  return new ReadableStream({
    pull(controller) {
      if (index < chunks.length) {
        controller.enqueue(chunks[index++]);
      } else {
        controller.error(error);
      }
    },
  });
}

// ── Tests ────────────────────────────────────────────────────────────────

test("normal stream with event:end returns assistant text", async () => {
  const longContent = "# 微服务架构\n\n" + "这是正文内容。".repeat(30);
  const msg = JSON.stringify([{ id: "msg-1", content: longContent }]);
  const stream = makeStream([
    encode(sseFrame("messages", msg)),
    encode(sseFrame("end", "{}")),
  ]);

  const result = await parseScratchSSE(stream);

  expect(result).not.toBeNull();
  expect(result!.isClarification).toBe(false);
  expect(result!.text).toContain("微服务架构");
  expect(result!.isFullDoc).toBe(true);
});

test("stream truncated (done=true, no end event) triggers fallback finalize", async () => {
  // Simulate network truncation: messages arrive but no end event, stream just closes
  const msg = JSON.stringify([
    { id: "msg-1", content: "# 标题\n\n这是一段足够长的正文内容".repeat(20) },
  ]);
  const stream = makeStream([encode(sseFrame("messages", msg))]);

  const result = await parseScratchSSE(stream);

  // Fallback should still produce a result
  expect(result).not.toBeNull();
  expect(result!.text).toContain("标题");
});

test("ask_clarification tool call produces isClarification result", async () => {
  const msg = JSON.stringify([
    {
      id: "msg-1",
      content: "",
      tool_calls: [
        {
          name: "ask_clarification",
          args: {
            question: "目标受众是谁？",
            options: ["技术团队", "管理层"],
          },
        },
      ],
    },
  ]);
  const stream = makeStream([
    encode(sseFrame("messages", msg)),
    encode(sseFrame("end", "{}")),
  ]);

  const result = await parseScratchSSE(stream);

  expect(result).not.toBeNull();
  expect(result!.isClarification).toBe(true);
  expect(result!.text).toBe("目标受众是谁？");
  expect(result!.options).toEqual(["技术团队", "管理层"]);
});

test("full document detection: long text + heading → isFullDoc=true", async () => {
  const longText = "# 第一章 微服务入门\n\n".padEnd(250, "正文内容");
  const msg = JSON.stringify([{ id: "msg-1", content: longText }]);
  const stream = makeStream([
    encode(sseFrame("messages", msg)),
    encode(sseFrame("end", "{}")),
  ]);

  const result = await parseScratchSSE(stream);

  expect(result).not.toBeNull();
  expect(result!.isFullDoc).toBe(true);
});

test("non-document text (short, no heading) → isFullDoc=false", async () => {
  const msg = JSON.stringify([{ id: "msg-1", content: "好的，我来帮你。" }]);
  const stream = makeStream([
    encode(sseFrame("messages", msg)),
    encode(sseFrame("end", "{}")),
  ]);

  const result = await parseScratchSSE(stream);

  expect(result).not.toBeNull();
  expect(result!.isFullDoc).toBe(false);
  expect(result!.text).toBe("好的，我来帮你。");
});

test("text containing question keywords is not detected as full doc", async () => {
  const longText = "# 大纲\n\n".padEnd(250, "内容") + "请问这个大纲可以吗？";
  const msg = JSON.stringify([{ id: "msg-1", content: longText }]);
  const stream = makeStream([
    encode(sseFrame("messages", msg)),
    encode(sseFrame("end", "{}")),
  ]);

  const result = await parseScratchSSE(stream);

  expect(result).not.toBeNull();
  // Has heading + length > 200, but contains "请问" → not full doc
  expect(result!.isFullDoc).toBe(false);
});

test("multi-chunk streaming accumulates text correctly", async () => {
  // Simulate streaming: same message id across multiple chunks
  const chunk1 = JSON.stringify([{ id: "msg-1", content: "# 标题\n\n" }]);
  const chunk2 = JSON.stringify([
    { id: "msg-1", content: "第一段内容".repeat(50) },
  ]);
  const stream = makeStream([
    encode(sseFrame("messages", chunk1)),
    encode(sseFrame("messages", chunk2)),
    encode(sseFrame("end", "{}")),
  ]);

  const result = await parseScratchSSE(stream);

  expect(result).not.toBeNull();
  expect(result!.text).toContain("标题");
  expect(result!.text).toContain("第一段内容");
  expect(result!.isFullDoc).toBe(true);
});

test("empty stream returns null", async () => {
  const stream = makeStream([encode(sseFrame("end", "{}"))]);

  const result = await parseScratchSSE(stream);

  expect(result).toBeNull();
});

test("content as array of text parts is parsed", async () => {
  const msg = JSON.stringify([
    {
      id: "msg-1",
      content: [
        { type: "text", text: "# 文档" },
        { type: "text", text: "\n\n正文".repeat(100) },
      ],
    },
  ]);
  const stream = makeStream([
    encode(sseFrame("messages", msg)),
    encode(sseFrame("end", "{}")),
  ]);

  const result = await parseScratchSSE(stream);

  expect(result).not.toBeNull();
  expect(result!.text).toContain("文档");
  expect(result!.isFullDoc).toBe(true);
});

// ── Message-type filtering (issue ④: search-JSON leak) ────────────────────

test("ToolMessage content (raw search JSON) is not leaked into assistant text", async () => {
  // Reproduces the 扩写 bug: the co-writer's MCP search returns a ToolMessage
  // whose content is the raw search-API JSON. The parser must only emit AI
  // message text, skipping Tool/Human/System messages entirely.
  const toolMsg = JSON.stringify([
    {
      type: "ToolMessage",
      id: "tool-1",
      content: JSON.stringify({
        credits: 3,
        searchParameters: {
          q: "能源产业数字化转型案例",
          format: "chat_completions",
        },
        webpages: [{ title: "leaked-result", snippet: "should-not-appear" }],
      }),
    },
  ]);
  const aiMsg = JSON.stringify([
    {
      type: "AIMessageChunk",
      id: "msg-1",
      content: "# 能源转型\n\n" + "正文内容".repeat(40),
    },
  ]);
  const stream = makeStream([
    encode(sseFrame("messages", toolMsg)),
    encode(sseFrame("messages", aiMsg)),
    encode(sseFrame("end", "{}")),
  ]);

  const result = await parseScratchSSE(stream);

  expect(result).not.toBeNull();
  expect(result!.text).toContain("能源转型");
  expect(result!.text).not.toContain("searchParameters");
  expect(result!.text).not.toContain("webpages");
  expect(result!.text).not.toContain("leaked-result");
});

test("HumanMessage content is not leaked into assistant text", async () => {
  const humanMsg = JSON.stringify([
    {
      type: "HumanMessage",
      id: "human-1",
      content: "用户原始输入不应出现在结果里",
    },
  ]);
  const aiMsg = JSON.stringify([
    {
      type: "AIMessageChunk",
      id: "msg-1",
      content: "# 标题\n\n" + "正文".repeat(60),
    },
  ]);
  const stream = makeStream([
    encode(sseFrame("messages", humanMsg)),
    encode(sseFrame("messages", aiMsg)),
    encode(sseFrame("end", "{}")),
  ]);

  const result = await parseScratchSSE(stream);

  expect(result).not.toBeNull();
  expect(result!.text).toContain("标题");
  expect(result!.text).not.toContain("用户原始输入");
});

// ── W14: Progressive rendering + truncation perceptibility (§5.2B + §5.1.2) ─

test("test_progressive_message_updates_per_chunk: onChunk fires per chunk with growing text", async () => {
  // Three chunks for the SAME message id simulate a streaming draft. The
  // onChunk callback must fire after each text chunk with the ACCUMULATED text
  // so the panel re-renders progressively (not spinner-then-bulk at stream-end).
  // §5.2B.
  const chunk1 = JSON.stringify([{ id: "msg-1", content: "# 标题\n\n" }]);
  const chunk2 = JSON.stringify([{ id: "msg-1", content: "第一段内容。" }]);
  const chunk3 = JSON.stringify([{ id: "msg-1", content: "第二段内容。" }]);
  const stream = makeStream([
    encode(sseFrame("messages", chunk1)),
    encode(sseFrame("messages", chunk2)),
    encode(sseFrame("messages", chunk3)),
    encode(sseFrame("end", "{}")),
  ]);

  const seen: string[] = [];
  const result = await parseScratchSSE(stream, (acc) => seen.push(acc));

  // onChunk fired once per text chunk, each time with strictly more text
  expect(seen.length).toBeGreaterThanOrEqual(3);
  for (let i = 1; i < seen.length; i++) {
    const prev = seen[i - 1]!;
    const curr = seen[i]!;
    expect(curr.length).toBeGreaterThan(prev.length);
    // New text is a strict extension of the previous (prefix property)
    expect(curr.startsWith(prev)).toBe(true);
  }
  // Final accumulated text contains all three chunks
  const final = seen[seen.length - 1]!;
  expect(final).toContain("标题");
  expect(final).toContain("第一段内容");
  expect(final).toContain("第二段内容");
  // Result text matches the last onChunk payload (no marker here → unchanged)
  expect(result).not.toBeNull();
  expect(result!.text).toBe(final);
  // accumulateChunk (the pure per-chunk seam) agrees with the accumulation
  expect(accumulateChunk("# 标题\n\n", "第一段内容。")).toBe(
    "# 标题\n\n第一段内容。",
  );
});

test("test_truncation_indicator_when_stream_ends_no_marker: substantial no-marker content → isTruncated", async () => {
  // Stream closes (done=true, no end event) with substantial content but NO
  // [[STAGE:...]] marker → likely token-truncated mid-generation → flagged as
  // truncated so the panel shows a ⚠️ indicator (NOT a silent stall). §5.1.2.
  const partial = "# 微服务架构\n\n" + "正文内容不断生成中".repeat(40);
  const msg = JSON.stringify([{ id: "msg-1", content: partial }]);
  const stream = makeStream([encode(sseFrame("messages", msg))]);

  const result = await parseScratchSSE(stream);

  expect(result).not.toBeNull();
  expect(result!.isClarification).toBe(false);
  expect(result!.isTruncated).toBe(true);
  // Pure helper agrees: no marker + substantial length → truncated
  expect(detectTruncation(null, partial.length)).toBe(true);
});

test("test_no_truncation_indicator_when_signal_present: [[STAGE:DRAFT]] → not truncated", async () => {
  // Stream ends WITH a [[STAGE:DRAFT]] marker → the draft was delivered
  // intentionally (the model emitted the signal) → NOT truncated, even though
  // the content is substantial. Show the draft normally. §5.1.2.
  const draft = "[[STAGE:DRAFT]]\n\n# 标题\n" + "正文内容".repeat(60);
  const msg = JSON.stringify([{ id: "msg-1", content: draft }]);
  const stream = makeStream([
    encode(sseFrame("messages", msg)),
    encode(sseFrame("end", "{}")),
  ]);

  const result = await parseScratchSSE(stream);

  expect(result).not.toBeNull();
  expect(result!.isFullDoc).toBe(true);
  // Signal present → never truncated, even though content is substantial
  expect(result!.isTruncated).toBe(false);
  // Pure helper agrees
  expect(detectTruncation("draft", draft.length)).toBe(false);
});

test("test_no_truncation_indicator_for_short_clarification: ask_clarification → not truncated", async () => {
  // A short clarification (ask_clarification tool call) → no truncation
  // indicator (it's an intentional question, not a truncated draft). §5.1.2.
  const msg = JSON.stringify([
    {
      id: "msg-1",
      content: "",
      tool_calls: [
        {
          name: "ask_clarification",
          args: {
            question: "目标受众是谁？",
            options: ["技术团队", "管理层"],
          },
        },
      ],
    },
  ]);
  const stream = makeStream([
    encode(sseFrame("messages", msg)),
    encode(sseFrame("end", "{}")),
  ]);

  const result = await parseScratchSSE(stream);

  expect(result).not.toBeNull();
  expect(result!.isClarification).toBe(true);
  // Short clarification → no truncation indicator (falsy / absent)
  expect(result!.isTruncated).toBeFalsy();
  // Pure helper agrees: no marker but short length → not truncated
  expect(detectTruncation(null, "目标受众是谁？".length)).toBe(false);
});

// ── Fix (review #1/#2): stream error + onChunk isolation ──────────────────

test("test_stream_error_after_partial: placeholder flagged isTruncated on stream error (Finding #1)", async () => {
  // Simulate a stream that sends partial content then errors mid-stream
  // (e.g. network error or the pre-W16 120s timeout abort). The consumer's
  // catch block (in parseSSEStream) should flag the placeholder as truncated
  // so the recovery UI (⚠️ + "继续生成") shows — NOT an orphaned partial
  // draft with no recovery affordance.
  const partialContent = "# 部分草稿\n\n" + "正文内容".repeat(60);
  const chunk1 = JSON.stringify([{ id: "msg-1", content: partialContent }]);
  const errorStream = makeErrorAfterChunksStream(
    [encode(sseFrame("messages", chunk1))],
    new Error("network error"),
  );

  // parseScratchSSE should throw (the stream errored mid-read)
  await expect(parseScratchSSE(errorStream)).rejects.toThrow("network error");

  // The consumer's catch block uses finalizePlaceholderOnError to flag the
  // placeholder. Verify the pure helper sets isTruncated: true.
  const placeholder: ScratchMessage = {
    id: "a-123",
    role: "assistant",
    content: partialContent,
  };
  const flagged = finalizePlaceholderOnError(placeholder);
  expect(flagged.isTruncated).toBe(true);
  expect(flagged.content).toBe(partialContent);
  expect(flagged.id).toBe("a-123");
  expect(flagged.role).toBe("assistant");
});

test("test_onChunk_error_not_swallowed: consumer error propagates, not mis-classified as malformed JSON (Finding #2)", async () => {
  // onChunk was inside the broad JSON-parse try/catch. If the consumer's
  // setMessages callback threw, it was silently swallowed as "malformed JSON"
  // and remaining messages in the frame were skipped. After fix: onChunk is
  // outside the try/catch, so consumer errors propagate.
  const msg = JSON.stringify([{ id: "msg-1", content: "部分内容" }]);
  const stream = makeStream([
    encode(sseFrame("messages", msg)),
    encode(sseFrame("end", "{}")),
  ]);

  const consumerError = new Error("setMessages failed");
  await expect(
    parseScratchSSE(stream, () => {
      throw consumerError;
    }),
  ).rejects.toThrow("setMessages failed");
});

// ── W15: "直接生成" skippable questions (§5.2C + §5.1.3) ─────────────────────

// Test 1: shouldForceDirectGenerate — general/podcast/script → true (initial
// send includes the direct-generate hint); article/case/course → false (the
// model may ask 1 skippable question). §5.2C.
test("test_should_force_direct_generate: general/podcast/script→true, article/case/course→false", () => {
  expect(shouldForceDirectGenerate("general")).toBe(true);
  expect(shouldForceDirectGenerate("podcast")).toBe(true);
  expect(shouldForceDirectGenerate("script")).toBe(true);
  expect(shouldForceDirectGenerate("article")).toBe(false);
  expect(shouldForceDirectGenerate("case")).toBe(false);
  expect(shouldForceDirectGenerate("course")).toBe(false);
});

// Test 2: initial send prompt — for force-types (general), the prompt includes
// the "直接生成" hint; for non-force-types (article), it doesn't. The
// prompt-build is extracted into buildScratchPrompt so it is unit-testable
// without React/DOM. §5.2C.
test("test_initial_send_includes_direct_hint_for_force_types: general gets hint, article doesn't", () => {
  // general → force direct generate → prompt includes the hint
  const generalPrompt = buildScratchPrompt(
    "微服务架构",
    true,
    "general",
    undefined,
  );
  expect(generalPrompt).toContain("[从零创作]");
  expect(generalPrompt).toContain("[类型: general]");
  expect(generalPrompt).toContain("微服务架构");
  expect(generalPrompt).toContain(DIRECT_GENERATE_HINT);

  // article → not force → prompt does NOT include the hint
  const articlePrompt = buildScratchPrompt(
    "数字化转型",
    true,
    "article",
    undefined,
  );
  expect(articlePrompt).toContain("[从零创作]");
  expect(articlePrompt).toContain("[类型: article]");
  expect(articlePrompt).toContain("数字化转型");
  expect(articlePrompt).not.toContain(DIRECT_GENERATE_HINT);
});

// Test 3: skipClarification sends a skip instruction via the reply/send path.
// skipClarification() calls reply(SKIP_CLARIFICATION_INSTRUCTION), which calls
// sendMessage(text, false). sendMessage builds the prompt via buildScratchPrompt
// and posts it to the backend. The pre-existing handleReply ignores-arg bug
// (handleReply reads replyInput state, ignoring its arg) is bypassed because
// skipClarification calls reply() DIRECTLY (not handleReply). This test asserts
// the skip instruction reaches the backend by verifying buildScratchPrompt wraps
// it correctly — this is the exact prompt sendMessage sends. §5.2C.
test("test_skip_clarification_sends_skip_instruction: skip text reaches backend via reply/sendMessage path", () => {
  // SKIP_CLARIFICATION_INSTRUCTION is the skip text the button sends
  expect(SKIP_CLARIFICATION_INSTRUCTION).toContain("直接");
  expect(SKIP_CLARIFICATION_INSTRUCTION).toContain("生成");

  // When skipClarification() calls reply(SKIP_CLARIFICATION_INSTRUCTION), the
  // prompt built by sendMessage (isFirst=false → reply path) wraps the skip
  // text in <user_input> tags — this is the message content posted to the
  // backend via postThreadRunStream.
  const skipPrompt = buildScratchPrompt(
    SKIP_CLARIFICATION_INSTRUCTION,
    false,
    "article",
    undefined,
  );
  // The skip instruction is wrapped in user_input tags (the reply path)
  expect(skipPrompt).toBe(
    `<user_input>${SKIP_CLARIFICATION_INSTRUCTION}</user_input>`,
  );
  // The skip instruction is NOT lost or truncated — the full text is present
  expect(skipPrompt).toContain(SKIP_CLARIFICATION_INSTRUCTION);
  // The hint is NOT added on the reply path (only on initial send for
  // force-types) — the skip instruction goes through cleanly
  expect(skipPrompt).not.toContain("[从零创作]");
});

// Test 4: the "直接生成" button on the clarification UI sends
// SKIP_CLARIFICATION_INSTRUCTION as its click payload. Rendering the button is
// a UI concern (covered by tsc/eslint + code review), NOT by this file — this
// test only pins the constant contract the button's handler depends on. §5.2C.
test("skip instruction constant is exported and well-formed", () => {
  // The "直接生成" button's onClick sends SKIP_CLARIFICATION_INSTRUCTION.
  // The constant must be a non-empty string containing the "直接生成" semantic
  // so the SOUL/model recognizes it as a skip directive.
  expect(typeof SKIP_CLARIFICATION_INSTRUCTION).toBe("string");
  expect(SKIP_CLARIFICATION_INSTRUCTION.length).toBeGreaterThan(0);
  // Contains the "直接" + "生成" semantic so the SOUL's "honor 直接生成" rule fires
  expect(SKIP_CLARIFICATION_INSTRUCTION).toMatch(/直接.*生成/);
  // Contains "无需" or "不要再提问" semantic so the model stops asking
  expect(SKIP_CLARIFICATION_INSTRUCTION).toMatch(/无需|不要再/);
});

// ── Fix (final-review CITATIONS leak): strip [[CITATIONS:...]] marker ─────

test("CITATIONS marker stripped from from-scratch result text (W22 final-review fix)", async () => {
  // The co-writer's W22 SOUL rule appends [[CITATIONS:<json>]] to ANY
  // search-using response, including from-scratch drafts. Without stripping
  // in parseScratchSSE/buildResult, the raw marker leaks into the user-visible
  // text in the from-scratch panel.
  const body = "# 微服务架构\n\n" + "正文内容".repeat(40);
  const citations = JSON.stringify([
    { title: "来源文章", url: "https://example.com/article", org: "Example" },
  ]);
  const content = `${body}\n\n[[CITATIONS:${citations}]]`;
  const msg = JSON.stringify([{ id: "msg-1", content }]);
  const stream = makeStream([
    encode(sseFrame("messages", msg)),
    encode(sseFrame("end", "{}")),
  ]);

  const result = await parseScratchSSE(stream);

  expect(result).not.toBeNull();
  // The CITATIONS marker must NOT appear in the result text
  expect(result!.text).not.toContain("[[CITATIONS");
  // The body content is preserved (marker + preceding whitespace stripped)
  expect(result!.text).toContain("微服务架构");
  expect(result!.text).toContain("正文内容");
  expect(result!.text).toBe(body);
});

test("P1-5: onFrame fires for non-text frames (tool calls), not just onChunk text", async () => {
  // 工具调用帧（无文本）模拟搜索/推理静默期——旧实现只在该帧有 AI 文本时才重置心跳，
  // 导致 >30s 静默期被误杀。onFrame 应对任意有效帧触发。
  const toolMsg = JSON.stringify([
    {
      id: "t1",
      type: "aimessage",
      tool_calls: [{ name: "web_search", args: { query: "x" } }],
    },
  ]);
  const textMsg = JSON.stringify([
    { id: "a1", type: "aimessage", content: "hello" },
  ]);
  const stream = makeStream([
    encode(sseFrame("messages", toolMsg)),
    encode(sseFrame("messages", textMsg)),
    encode(sseFrame("end", "{}")),
  ]);
  let frameCount = 0;
  let chunkCount = 0;
  await parseScratchSSE(
    stream,
    () => {
      chunkCount++;
    },
    () => {
      frameCount++;
    },
  );
  // 工具帧无文本 → onChunk 不触发，但 onFrame 必须触发
  expect(frameCount).toBeGreaterThanOrEqual(2);
  expect(chunkCount).toBe(1); // 只有文本帧触发 onChunk
});

test("Fix 2.3: parseScratchSSE fires onSearchTool for search tool calls", async () => {
  const searchMsg = JSON.stringify([
    {
      id: "a1",
      type: "aimessagechunk",
      tool_calls: [{ name: "metaso__web_search_pro", args: {} }],
    },
  ]);
  const stream = makeStream([
    encode(sseFrame("messages", searchMsg)),
    encode(sseFrame("end", "{}")),
  ]);
  const searched: string[] = [];
  await parseScratchSSE(stream, undefined, undefined, (name) =>
    searched.push(name),
  );
  expect(searched).toContain("metaso__web_search_pro");
});

// ── B1-T3: buildScratchPrompt uses current docType via refs ──────────────
// The stale-closure bug: startSession/reply/skipClarification used [] deps
// but called sendMessage which depended on [docType, docTitle]. After
// switching docType, the three callbacks still captured the OLD sendMessage
// -> old docType in the prompt. Now sendMessage reads from refs.
// We verify the contract at the buildScratchPrompt level: the prompt must
// reflect whatever docType is passed (the ref's current value).

test("B1-T3: buildScratchPrompt reflects updated docType (ref pattern)", () => {
  // Simulate the ref pattern: docType changes from "general" to "article"
  const oldPrompt = buildScratchPrompt("topic", true, "general", undefined);
  const newPrompt = buildScratchPrompt("topic", true, "article", undefined);

  expect(oldPrompt).toContain("[类型: general]");
  expect(newPrompt).toContain("[类型: article]");
  expect(newPrompt).not.toContain("[类型: general]");
  // The "直接生成" hint only applies to general/podcast/script
  expect(oldPrompt).toContain(DIRECT_GENERATE_HINT);
  expect(newPrompt).not.toContain(DIRECT_GENERATE_HINT);
});
