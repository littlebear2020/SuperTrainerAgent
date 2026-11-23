import type { Message } from "@langchain/langgraph-sdk";
import { describe, expect, it } from "vitest";

import {
  accumulateUsage,
  EMPTY_TURN_USAGE,
  summarizeTurnUsage,
  type TokenUsage,
  type TurnUsage,
} from "@/core/messages/usage";
import {
  getMessageGroups,
  hasContent,
  hasToolCalls,
  type ThreadMessageGroup,
} from "@/core/messages/utils";

type UsageMetadata = {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
};

function human(id: string): Message {
  return { id, type: "human", content: `request ${id}` } as Message;
}

function ai(
  id: string,
  content: string,
  usageMetadata?: UsageMetadata,
): Message {
  return {
    id,
    type: "ai",
    content,
    ...(usageMetadata ? { usage_metadata: usageMetadata } : {}),
  } as Message;
}

function toolAi(
  id: string,
  toolCallId: string,
  usageMetadata?: UsageMetadata,
  name = "bash",
): Message {
  return {
    id,
    type: "ai",
    content: "",
    tool_calls: [
      {
        id: toolCallId,
        name,
        args:
          name === "present_files"
            ? { filepaths: ["/mnt/data/result.txt"] }
            : { command: "true" },
      },
    ],
    ...(usageMetadata ? { usage_metadata: usageMetadata } : {}),
  } as Message;
}

function toolResult(
  id: string,
  toolCallId: string,
  name = "bash",
  content = '{"status":"done"}',
): Message {
  return {
    id,
    type: "tool",
    name,
    tool_call_id: toolCallId,
    content,
  } as Message;
}

function group(
  id: string,
  type: ThreadMessageGroup["type"],
  messages: Message[],
): ThreadMessageGroup {
  return { id, type, messages } as ThreadMessageGroup;
}

function makeSeventeenRowFixture() {
  const messages: Message[] = [human("h-1")];
  for (let index = 1; index <= 15; index++) {
    messages.push(
      toolAi(`lc_run--${index}`, `call-${index}`, {
        input_tokens: index,
        output_tokens: index * 2,
        total_tokens: index * 3,
      }),
      toolResult(`tool-${index}`, `call-${index}`),
    );
  }
  messages.push(
    toolAi(
      "present",
      "present-call",
      {
        input_tokens: 100,
        output_tokens: 200,
        total_tokens: 300,
      },
      "present_files",
    ),
    ai("final", "answer", {
      input_tokens: 1000,
      output_tokens: 2000,
      total_tokens: 3000,
    }),
  );
  return { messages, groups: getMessageGroups(messages) };
}

function legacyTurnRows(groups: ThreadMessageGroup[]): Message[][] {
  const turns: Message[][] = [];
  for (const current of groups) {
    if (current.type === "human") {
      turns.push([]);
      continue;
    }
    if (turns.length === 0) {
      turns.push([]);
    }
    const first = current.messages[0];
    if (
      current.type === "assistant:clarification" &&
      (!first || !hasContent(first))
    ) {
      continue;
    }
    const rows = current.messages.filter((message) => {
      if (message.type !== "ai") {
        return false;
      }
      if (current.type === "assistant:processing") {
        return hasToolCalls(message) ? true : !hasContent(message);
      }
      return true;
    });
    turns[turns.length - 1]!.push(...rows);
  }
  return turns;
}

function legacySummary(messages: Message[]): TurnUsage {
  const usage: TokenUsage = {
    inputTokens: 0,
    outputTokens: 0,
    totalTokens: 0,
  };
  let hasUsage = false;
  let missingCount = 0;
  for (const message of messages) {
    const raw = (message as Message & { usage_metadata?: UsageMetadata })
      .usage_metadata;
    if (!raw) {
      missingCount++;
      continue;
    }
    hasUsage = true;
    usage.inputTokens += raw.input_tokens;
    usage.outputTokens += raw.output_tokens;
    usage.totalTokens += raw.total_tokens;
  }
  return { usage: hasUsage ? usage : null, missingCount };
}

function expectConservation(
  groups: ThreadMessageGroup[],
  actual: ReadonlyMap<number, TurnUsage>,
) {
  const oldRows = legacyTurnRows(groups);
  const expected = oldRows
    .filter((turn) => turn.length > 0)
    .map((turn) => legacySummary(turn));
  expect([...actual.values()]).toEqual(expected);
}

describe("summarizeTurnUsage", () => {
  it("exports one frozen empty ReadonlyMap for gated renders", () => {
    const empty: ReadonlyMap<number, TurnUsage> = EMPTY_TURN_USAGE;
    expect(empty.size).toBe(0);
    expect(Object.isFrozen(empty)).toBe(true);
  });

  it("matches all 17 legacy rows and accumulateUsage in the offline fixture", () => {
    const { messages, groups } = makeSeventeenRowFixture();
    const expected: TurnUsage = {
      usage: { inputTokens: 1220, outputTokens: 2440, totalTokens: 3660 },
      missingCount: 0,
    };

    expect(groups.map((current) => current.type)).toEqual([
      "human",
      "assistant:processing",
      "assistant:present-files",
      "assistant",
    ]);
    const oldTurns = legacyTurnRows(groups);
    expect(oldTurns).toHaveLength(1);
    expect(oldTurns[0]).toHaveLength(17);
    expect(legacySummary(oldTurns[0]!)).toEqual(expected);

    expect(accumulateUsage(messages)).toEqual(expected.usage);
    const summary = summarizeTurnUsage(groups);
    expect(summary).toEqual(new Map([[3, expected]]));
    expectConservation(groups, summary);
  });

  it("counts a reasoning and content message once across processing and assistant groups", () => {
    const shared = ai("shared-object", "answer", {
      input_tokens: 7,
      output_tokens: 11,
      total_tokens: 18,
    });
    shared.additional_kwargs = { reasoning_content: "reasoning" };
    const groups = getMessageGroups([human("h-1"), shared]);

    expect(groups.map((current) => current.type)).toEqual([
      "human",
      "assistant:processing",
      "assistant",
    ]);
    expect(groups[1]!.messages[0]).toBe(shared);
    expect(groups[2]!.messages[0]).toBe(shared);
    expect(legacyTurnRows(groups)[0]).toHaveLength(1);

    const summary = summarizeTurnUsage(groups);
    expect(summary).toEqual(
      new Map([
        [
          2,
          {
            usage: { inputTokens: 7, outputTokens: 11, totalTokens: 18 },
            missingCount: 0,
          },
        ],
      ]),
    );
    expectConservation(groups, summary);
  });

  it("counts different message objects with the same id separately", () => {
    const first = ai("lc_run--same", "first", {
      input_tokens: 10,
      output_tokens: 20,
      total_tokens: 30,
    });
    const second = ai("lc_run--same", "second", {
      input_tokens: 20,
      output_tokens: 40,
      total_tokens: 60,
    });
    const groups = getMessageGroups([human("h-1"), first, second]);

    expect(groups[1]!.messages[0]).not.toBe(groups[2]!.messages[0]);
    expect(legacyTurnRows(groups)[0]).toHaveLength(2);
    expect(accumulateUsage([first, second])).toEqual({
      inputTokens: 10,
      outputTokens: 20,
      totalTokens: 30,
    });
    const summary = summarizeTurnUsage(groups);
    expect(summary.get(2)).toEqual({
      usage: { inputTokens: 30, outputTokens: 60, totalTokens: 90 },
      missingCount: 0,
    });
    expectConservation(groups, summary);
  });

  it("does not count an AI message that never entered a rendered group", () => {
    const ungrouped = ai("ungrouped", "", {
      input_tokens: 999,
      output_tokens: 999,
      total_tokens: 1998,
    });
    const visible = ai("visible", "answer", {
      input_tokens: 4,
      output_tokens: 5,
      total_tokens: 9,
    });
    const messages = [human("h-1"), ungrouped, visible];
    const groups = getMessageGroups(messages);

    expect(groups.flatMap((current) => current.messages)).not.toContain(
      ungrouped,
    );
    const summary = summarizeTurnUsage(groups);
    expect(summary).toEqual(
      new Map([
        [
          1,
          {
            usage: { inputTokens: 4, outputTokens: 5, totalTokens: 9 },
            missingCount: 0,
          },
        ],
      ]),
    );
    expectConservation(groups, summary);
  });

  it("returns null usage and counts every AI message when all usage is missing", () => {
    const groups = getMessageGroups([
      human("h-1"),
      toolAi("a-1", "call-1"),
      toolResult("t-1", "call-1"),
      toolAi("a-2", "call-2"),
      toolResult("t-2", "call-2"),
    ]);
    const summary = summarizeTurnUsage(groups);

    expect(summary).toEqual(new Map([[1, { usage: null, missingCount: 2 }]]));
    expectConservation(groups, summary);
  });

  it("sums returned usage and counts only missing messages in a partial turn", () => {
    const groups = getMessageGroups([
      human("h-1"),
      toolAi("a-1", "call-1", {
        input_tokens: 5,
        output_tokens: 7,
        total_tokens: 12,
      }),
      toolAi("missing", "call-2"),
      toolAi("zero", "call-3", {
        input_tokens: 0,
        output_tokens: 0,
        total_tokens: 0,
      }),
    ]);
    const summary = summarizeTurnUsage(groups);

    expect(summary.get(1)).toEqual({
      usage: { inputTokens: 5, outputTokens: 7, totalTokens: 12 },
      missingCount: 1,
    });
    expectConservation(groups, summary);
  });

  it("keeps an all-zero usage result instead of treating zero as missing", () => {
    const groups = getMessageGroups([
      human("h-1"),
      ai("zero", "answer", {
        input_tokens: 0,
        output_tokens: 0,
        total_tokens: 0,
      }),
    ]);
    const summary = summarizeTurnUsage(groups);

    expect(summary.get(1)).toEqual({
      usage: { inputTokens: 0, outputTokens: 0, totalTokens: 0 },
      missingCount: 0,
    });
    expectConservation(groups, summary);
  });

  it("keeps two visible human turns in separate entries", () => {
    const groups = getMessageGroups([
      human("h-1"),
      ai("a-1", "first", {
        input_tokens: 1,
        output_tokens: 2,
        total_tokens: 3,
      }),
      human("h-2"),
      ai("a-2", "second", {
        input_tokens: 10,
        output_tokens: 20,
        total_tokens: 30,
      }),
    ]);
    const summary = summarizeTurnUsage(groups);

    expect(summary).toEqual(
      new Map([
        [
          1,
          {
            usage: { inputTokens: 1, outputTokens: 2, totalTokens: 3 },
            missingCount: 0,
          },
        ],
        [
          3,
          {
            usage: { inputTokens: 10, outputTokens: 20, totalTokens: 30 },
            missingCount: 0,
          },
        ],
      ]),
    );
    expectConservation(groups, summary);
  });

  it("summarizes an implicit first turn before the first human group", () => {
    const groups = getMessageGroups([
      ai("opening", "opening", {
        input_tokens: 2,
        output_tokens: 3,
        total_tokens: 5,
      }),
      human("h-1"),
      ai("answer", "answer", {
        input_tokens: 4,
        output_tokens: 6,
        total_tokens: 10,
      }),
    ]);
    const summary = summarizeTurnUsage(groups);

    expect(summary).toEqual(
      new Map([
        [
          0,
          {
            usage: { inputTokens: 2, outputTokens: 3, totalTokens: 5 },
            missingCount: 0,
          },
        ],
        [
          2,
          {
            usage: { inputTokens: 4, outputTokens: 6, totalTokens: 10 },
            missingCount: 0,
          },
        ],
      ]),
    );
    expectConservation(groups, summary);
  });

  it("omits human-only turns before and after a turn with AI messages", () => {
    const groups = getMessageGroups([
      human("empty-before"),
      human("with-ai"),
      ai("answer", "answer", {
        input_tokens: 8,
        output_tokens: 13,
        total_tokens: 21,
      }),
      human("empty-after"),
    ]);
    const summary = summarizeTurnUsage(groups);

    expect(summary).toEqual(
      new Map([
        [
          2,
          {
            usage: { inputTokens: 8, outputTokens: 13, totalTokens: 21 },
            missingCount: 0,
          },
        ],
      ]),
    );
    expectConservation(groups, summary);
  });

  it("uses original group indices and skips trailing empty clarification groups", () => {
    const groups = [
      group("h-1", "human", [human("h-1")]),
      group("processing", "assistant:processing", [
        toolAi("a-1", "call-1", {
          input_tokens: 1,
          output_tokens: 2,
          total_tokens: 3,
        }),
      ]),
      group("empty-middle", "assistant:clarification", []),
      group("final", "assistant", [
        ai("final", "answer", {
          input_tokens: 10,
          output_tokens: 20,
          total_tokens: 30,
        }),
      ]),
      group("empty-tail", "assistant:clarification", [
        toolResult(
          "clarification",
          "clarification-call",
          "ask_clarification",
          "",
        ),
      ]),
    ];
    const summary = summarizeTurnUsage(groups);

    expect([...summary.keys()]).toEqual([3]);
    expect(summary.get(3)).toEqual({
      usage: { inputTokens: 11, outputTokens: 22, totalTokens: 33 },
      missingCount: 0,
    });
    expect(summary.has(4)).toBe(false);
    expectConservation(groups, summary);
  });
});
