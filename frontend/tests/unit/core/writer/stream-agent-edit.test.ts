/**
 * streamAgentEdit message-type filtering tests (issue ④).
 *
 * The 扩写 bug: the co-writer's MCP web search returns a ToolMessage whose
 * `content` is the raw search-API JSON, and `streamAgentEdit` emitted every
 * message's `content` as assistant text — leaking the JSON into the editor.
 * These tests mock the fetch + SSE stream and assert only AI-message text is
 * emitted; ToolMessage / HumanMessage content is dropped.
 */

import { afterEach, beforeEach, expect, test, vi } from "vitest";

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "",
}));

import { detectSearchToolCall, streamAgentEdit } from "@/core/writer/api";
import type { AgentEditEvent } from "@/core/writer/types";

function encode(str: string): Uint8Array {
  return new TextEncoder().encode(str);
}

function sseFrame(event: string, data: string): string {
  return `event: ${event}\ndata: ${data}\n\n`;
}

function makeStream(chunks: Uint8Array[]): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk);
      controller.close();
    },
  });
}

let fetchSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  vi.stubGlobal("document", { cookie: "csrf_token=t" });
});

afterEach(() => {
  fetchSpy?.mockRestore();
  vi.unstubAllGlobals();
});

/** Collect all `text` events from the generator into a single string. */
async function collectText(
  gen: AsyncGenerator<AgentEditEvent>,
): Promise<string> {
  const parts: string[] = [];
  for await (const event of gen) {
    if (event.type === "text") parts.push(event.content);
  }
  return parts.join("");
}

function mockStreamResponse(chunks: Uint8Array[]): Response {
  return new Response(makeStream(chunks), {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });
}

test("emits AI text and drops ToolMessage search JSON", async () => {
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
    { type: "AIMessageChunk", id: "msg-1", content: "扩写后的正文字符串" },
  ]);
  fetchSpy = vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValue(
      mockStreamResponse([
        encode(sseFrame("messages", toolMsg)),
        encode(sseFrame("messages", aiMsg)),
        encode(sseFrame("end", "{}")),
      ]),
    );

  const text = await collectText(
    streamAgentEdit({ instruction: "扩写", markdown: "原文", mode: "expand" }),
  );

  expect(text).toContain("扩写后的正文");
  expect(text).not.toContain("searchParameters");
  expect(text).not.toContain("webpages");
  expect(text).not.toContain("leaked-result");
});

test("drops HumanMessage content, keeps AI text", async () => {
  const humanMsg = JSON.stringify([
    { type: "HumanMessage", id: "human-1", content: "用户输入不应出现" },
  ]);
  const aiMsg = JSON.stringify([
    { type: "AIMessageChunk", id: "msg-1", content: "润色后的文本" },
  ]);
  fetchSpy = vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValue(
      mockStreamResponse([
        encode(sseFrame("messages", humanMsg)),
        encode(sseFrame("messages", aiMsg)),
        encode(sseFrame("end", "{}")),
      ]),
    );

  const text = await collectText(
    streamAgentEdit({ instruction: "润色", markdown: "原文", mode: "polish" }),
  );

  expect(text).toContain("润色后的文本");
  expect(text).not.toContain("用户输入不应出现");
});

// ── Fix 2.3: detect search tool calls so the UI can surface 「正在联网搜索」 ──

test("detectSearchToolCall detects metaso / 360 / web_search tool calls", () => {
  expect(
    detectSearchToolCall({ tool_calls: [{ name: "metaso__web_search_pro" }] }),
  ).toBe("metaso__web_search_pro");
  expect(
    detectSearchToolCall({
      tool_calls: [{ name: "so_360_search_mcp__search" }],
    }),
  ).toBe("so_360_search_mcp__search");
  expect(detectSearchToolCall({ tool_calls: [{ name: "web_search" }] })).toBe(
    "web_search",
  );
  expect(
    detectSearchToolCall({ tool_calls: [{ name: "tavily_search" }] }),
  ).toBe("tavily_search");
});

test("detectSearchToolCall reads tool_call_chunks + additional_kwargs too", () => {
  expect(
    detectSearchToolCall({ tool_call_chunks: [{ name: "metaso__search" }] }),
  ).toBe("metaso__search");
  expect(
    detectSearchToolCall({
      additional_kwargs: { tool_calls: [{ name: "jina_search" }] },
    }),
  ).toBe("jina_search");
});

test("detectSearchToolCall returns null for non-search tools / clarifications", () => {
  expect(
    detectSearchToolCall({ tool_calls: [{ name: "read_file" }] }),
  ).toBeNull();
  expect(
    detectSearchToolCall({ tool_calls: [{ name: "ask_clarification" }] }),
  ).toBeNull();
  expect(
    detectSearchToolCall({ tool_calls: [{ name: "write_file" }] }),
  ).toBeNull();
  expect(detectSearchToolCall({})).toBeNull();
  expect(detectSearchToolCall(null)).toBeNull();
});

test("streamAgentEdit emits a status event when the agent calls a search tool", async () => {
  // AI message chunk carrying a search tool call (no text). Fix 2.3: surface it.
  const searchMsg = JSON.stringify([
    {
      id: "a1",
      type: "aimessagechunk",
      tool_calls: [{ name: "metaso__web_search_pro", args: {} }],
    },
  ]);
  const textMsg = JSON.stringify([
    { id: "a1", type: "aimessagechunk", content: "根据搜索结果…" },
  ]);
  fetchSpy = vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValue(
      mockStreamResponse([
        encode(sseFrame("messages", searchMsg)),
        encode(sseFrame("messages", textMsg)),
        encode(sseFrame("end", "{}")),
      ]),
    );
  const events: AgentEditEvent[] = [];
  for await (const ev of streamAgentEdit({
    instruction: "x",
    markdown: "y",
    mode: "expand",
  })) {
    events.push(ev);
  }
  const statuses = events.filter((e) => e.type === "status");
  // streamAgentEdit also emits an initial status ("正在全文扩写…"); assert the
  // search-specific status is among them.
  expect(statuses.some((s) => s.content.includes("搜索"))).toBe(true);
  // text still emitted
  expect(
    events.some((e) => e.type === "text" && e.content.includes("根据搜索结果")),
  ).toBe(true);
});
