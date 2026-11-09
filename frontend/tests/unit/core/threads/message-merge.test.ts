import type { Message } from "@langchain/langgraph-sdk";
import { expect, test } from "vitest";

import { mergeMessages } from "@/core/threads/hooks";

test("mergeMessages keeps visible history when hidden live message shares its id", () => {
  const historyHuman = {
    id: "human-1",
    type: "human",
    content: "visible user prompt",
  } as Message;
  const hiddenReminder = {
    id: "human-1",
    type: "human",
    content: "<system-reminder>hidden</system-reminder>",
    additional_kwargs: { hide_from_ui: true },
  } as Message;
  const liveAi = {
    id: "ai-1",
    type: "ai",
    content: "live answer",
  } as Message;

  // Without the hidden-filter fix, historyHuman would be trimmed as overlap
  // because hiddenReminder shares its id. The fix ensures hidden messages
  // don't participate in overlap detection, so historyHuman is preserved.
  const result = mergeMessages([historyHuman], [hiddenReminder, liveAi], []);
  expect(result[0]).toBe(historyHuman);
});

test("mergeMessages lets a visible live message replace overlapping visible history", () => {
  const historyHuman = {
    id: "human-1",
    type: "human",
    content: "old visible prompt",
  } as Message;
  const liveHuman = {
    id: "human-1",
    type: "human",
    content: "updated visible prompt",
  } as Message;

  expect(mergeMessages([historyHuman], [liveHuman], [])).toEqual([liveHuman]);
});

test("mergeMessages does not trim history when only hidden live messages overlap", () => {
  const historyHuman = {
    id: "human-1",
    type: "human",
    content: "visible user prompt",
  } as Message;
  const historyAi = {
    id: "ai-1",
    type: "ai",
    content: "visible ai response",
  } as Message;
  const hiddenLive = {
    id: "ai-1",
    type: "ai",
    content: "system context update",
    additional_kwargs: { hide_from_ui: true },
  } as Message;

  // The hidden live message should not cause historyAi to be trimmed.
  const result = mergeMessages([historyHuman, historyAi], [hiddenLive], []);
  expect(result).toContain(historyHuman);
  expect(result).toContain(historyAi);
});

test("mergeMessages treats summary-named messages as hidden for overlap", () => {
  const historyHuman = {
    id: "human-1",
    type: "human",
    content: "visible user prompt",
  } as Message;
  const summaryLive = {
    id: "human-1",
    type: "human",
    name: "summary",
    content: "Here is a summary of the conversation",
  } as Message;

  // Summary messages (name="summary") are hidden from UI and should not
  // evict visible history messages from overlap detection.
  const result = mergeMessages([historyHuman], [summaryLive], []);
  expect(result).toContain(historyHuman);
});

test("mergeMessages dedupes overlap even when a hidden message sits at history tail", () => {
  // 回归（2026-07-24 消息错乱 P0）：旧连续后缀扫描从末尾回扫，
  // 撞上 hidden 消息 id 不在集合 → 立即 break → 整段 history 前置 →
  // h2 与其 thread 副本 h2' 同时可见（重复+乱序）。
  const h1 = { id: "h1", type: "human", content: "first prompt" } as Message;
  const h2 = { id: "h2", type: "ai", content: "old answer" } as Message;
  const hiddenTail = {
    id: "h3",
    type: "human",
    content: "<system-reminder>x</system-reminder>",
    additional_kwargs: { hide_from_ui: true },
  } as Message;
  const h2Live = { id: "h2", type: "ai", content: "updated answer" } as Message;
  const ai3 = { id: "h4", type: "ai", content: "new reply" } as Message;

  const result = mergeMessages([h1, h2, hiddenTail], [h2Live, ai3], []);
  expect(result).toEqual([h1, hiddenTail, h2Live, ai3]);
  expect(result.filter((m) => m.id === "h2")).toHaveLength(1);
});

test("mergeMessages dedupes when overlap has a gap (regenerated ids mid-run)", () => {
  // 连续性被任一条打破（id 重生）不再整段前置。
  const a = { id: "a", type: "human", content: "a" } as Message;
  const b = { id: "b", type: "ai", content: "b" } as Message;
  const c = { id: "c", type: "ai", content: "c" } as Message;
  const bLive = { id: "b", type: "ai", content: "b-live" } as Message;

  const result = mergeMessages([a, b, c], [bLive], []);
  expect(result).toEqual([a, c, bLive]);
});

// ---------------------------------------------------------------------------
// D-3: dynamic-context `__user` suffix normalization in merge keys
// ---------------------------------------------------------------------------

test("mergeMessages treats human X__user and its base-id copy as one message", () => {
  // DynamicContextMiddleware's ID-swap: the reminder SystemMessage takes the
  // original id X, the real user copy becomes X__user. If history still holds
  // the pre-swap copy (id=X, e.g. a regenerate strip-and-replay edge case)
  // while the live thread carries the committed X__user copy, both must
  // resolve to one UI message instead of double-rendering the user turn.
  const historyHuman = {
    id: "msg-1",
    type: "human",
    content: "the user question",
  } as Message;
  const liveSwappedUser = {
    id: "msg-1__user",
    type: "human",
    content: "the user question",
  } as Message;
  const liveAi = {
    id: "ai-1",
    type: "ai",
    content: "the answer",
  } as Message;

  const result = mergeMessages([historyHuman], [liveSwappedUser, liveAi], []);
  expect(result).toEqual([liveSwappedUser, liveAi]);
  expect(result.filter((m) => m.type === "human")).toHaveLength(1);
});

test("mergeMessages does not normalize the __user suffix on non-human messages", () => {
  // Normalization is human-only by design: the suffix only ever comes from the
  // user-copy swap. An AI/tool message whose id happens to end with __user
  // keeps its full id as the merge key.
  const historyAi = {
    id: "ai-9__user",
    type: "ai",
    content: "old",
  } as Message;
  const liveAiBase = {
    id: "ai-9",
    type: "ai",
    content: "new",
  } as Message;

  const result = mergeMessages([historyAi], [liveAiBase], []);
  expect(result).toEqual([historyAi, liveAiBase]);
});

test("mergeMessages keeps tool_call_id precedence over the __user-suffixed id", () => {
  const historyTool = {
    id: "t1",
    tool_call_id: "call-1",
    type: "tool",
    content: "tool result",
  } as Message;
  const liveTool = {
    id: "call-1__user",
    tool_call_id: "call-1",
    type: "tool",
    content: "tool result (live)",
  } as Message;

  const result = mergeMessages([historyTool], [liveTool], []);
  expect(result).toEqual([liveTool]);
});
