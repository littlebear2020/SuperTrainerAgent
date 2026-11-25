import type { Message, Run } from "@langchain/langgraph-sdk";
import { describe, expect, it } from "vitest";

import {
  deriveSubtasks,
  deriveSubtasksByRun,
} from "@/core/tasks/subtask-status-sync";

const aiTask = (id: string, tcId: string): Message =>
  ({
    id,
    type: "ai",
    content: "",
    tool_calls: [
      {
        id: tcId,
        name: "task",
        args: {
          subagent_type: "general-purpose",
          description: "d",
          prompt: "p",
        },
      },
    ],
  }) as unknown as Message;

const toolMsg = (id: string, tcId: string, content: string): Message =>
  ({
    id,
    type: "tool",
    name: "task",
    tool_call_id: tcId,
    content,
  }) as unknown as Message;

describe("deriveSubtasks", () => {
  it("marks completed from a success result", () => {
    const out = deriveSubtasks([
      aiTask("a1", "tc1"),
      toolMsg("t1", "tc1", "Task Succeeded. Result: done"),
    ]);
    expect(out).toHaveLength(1);
    expect(out[0]!.status).toBe("completed");
    expect(out[0]!.result).toBe("done");
  });

  it("marks failed from an Error: wrapper", () => {
    const out = deriveSubtasks([
      aiTask("a1", "tc1"),
      toolMsg("t1", "tc1", "Error: Tool 'task' failed: boom"),
    ]);
    expect(out).toHaveLength(1);
    expect(out[0]!.status).toBe("failed");
  });

  it("leaves in_progress when no result yet", () => {
    const out = deriveSubtasks([aiTask("a1", "tc1")]);
    expect(out).toHaveLength(1);
    expect(out[0]!.status).toBe("in_progress");
  });

  it("in_progress derivation carries no result/error keys (no clobber race)", () => {
    // No tool-result message yet: derived status is in_progress. The patch must
    // NOT include result/error keys, else applySubtaskPatch's spread would
    // overwrite a result already set by a task_completed streaming event in the
    // brief window before the tool-result message arrives in thread.messages.
    const out = deriveSubtasks([aiTask("a1", "tc1")]);
    expect(out).toHaveLength(1);
    expect(out[0]!.status).toBe("in_progress");
    expect("result" in out[0]!).toBe(false);
    expect("error" in out[0]!).toBe(false);
  });

  it("ignores non-task tool calls", () => {
    const ai = {
      id: "a1",
      type: "ai",
      content: "",
      tool_calls: [{ id: "tc1", name: "bash", args: { command: "ls" } }],
    } as unknown as Message;
    expect(deriveSubtasks([ai])).toHaveLength(0);
  });
});

describe("deriveSubtasks 按所属 run 的终态判定（PR3）", () => {
  const clarification = (id: string, tcId: string): Message =>
    ({
      id,
      type: "tool",
      name: "ask_clarification",
      tool_call_id: tcId,
      content: "需要确认",
    }) as unknown as Message;

  it("无 result：success → completed，error / timeout → failed，不虚构错误详情", () => {
    for (const [runStatus, expected] of [
      ["success", "completed"],
      ["error", "failed"],
      ["timeout", "failed"],
    ] as const) {
      const out = deriveSubtasks([aiTask("a1", "tc1")], { runStatus });
      expect(out[0]!.status).toBe(expected);
      // runs API 没有 error 字段：状态级判定不带 result / error
      expect("error" in out[0]!).toBe(false);
      expect("result" in out[0]!).toBe(false);
    }
  });

  it("无 result：interrupted（用户停止）→ cancelled，不是 failed", () => {
    const out = deriveSubtasks([aiTask("a1", "tc1")], {
      runStatus: "interrupted",
    });
    expect(out[0]!.status).toBe("cancelled");
  });

  it("无 result：pending / running → in_progress", () => {
    for (const runStatus of ["pending", "running"] as const) {
      const out = deriveSubtasks([aiTask("a1", "tc1")], { runStatus });
      expect(out[0]!.status).toBe("in_progress");
    }
  });

  it("有 tool result 时始终按原 parser，不被 run 终态改写", () => {
    const out = deriveSubtasks(
      [aiTask("a1", "tc1"), toolMsg("t1", "tc1", "Task Succeeded. Result: ok")],
      { runStatus: "error" },
    );
    expect(out[0]!.status).toBe("completed");
    expect(out[0]!.result).toBe("ok");
  });

  it("澄清不是 interrupted：success + 未回答的 ask_clarification，无 result 的任务保持 in_progress、有 result 的保持原状态", () => {
    const out = deriveSubtasks(
      [
        aiTask("a1", "tc1"),
        toolMsg("t1", "tc1", "Task Succeeded. Result: done"),
        aiTask("a2", "tc2"),
        clarification("c1", "ask-1"),
      ],
      { runStatus: "success" },
    );
    expect(out.map((task) => [task.id, task.status])).toEqual([
      ["tc1", "completed"],
      ["tc2", "in_progress"],
    ]);
  });

  it("澄清挂起只看澄清之后有没有用户的回答：澄清之前就已完成的任务不被拖成 in_progress", () => {
    // 同一 run：任务有 result → completed；之后才挂起澄清（goto=END）
    const answered = deriveSubtasks(
      [
        aiTask("a1", "tc1"),
        toolMsg("t1", "tc1", "Task Succeeded. Result: done"),
        clarification("c1", "ask-1"),
      ],
      { runStatus: "success" },
    );
    expect(answered[0]!.status).toBe("completed");
    // 澄清之后只有别的工具消息、没有用户的回答：澄清并未被回答（并行工具调用的结果
    // 晚于澄清写入），run 仍停在等回答上，没有 result 的任务不算完成
    const unanswered = deriveSubtasks(
      [
        aiTask("a1", "tc1"),
        clarification("c1", "ask-1"),
        toolMsg("t2", "other", "Tool result"),
      ],
      { runStatus: "success" },
    );
    expect(unanswered[0]!.status).toBe("in_progress");
  });

  it("澄清之后已有用户的回答：澄清已被回答，没有 result 的任务按 success 判", () => {
    const humanReply = {
      id: "h-1",
      type: "human",
      content: "好的，选第一个",
    } as unknown as Message;
    const out = deriveSubtasks(
      [aiTask("a1", "tc1"), clarification("c1", "ask-1"), humanReply],
      { runStatus: "success" },
    );
    expect(out[0]!.status).toBe("completed");
  });

  it("有归属但 runsById 里查不到这个 run（列表未刷新 / 已被清理）：不判终态，只登记元数据", () => {
    const out = deriveSubtasksByRun([aiTask("a1", "tc1")], {
      messageRunIds: new Map([["a1", "run-gone"]]),
      runsById: {},
      activeRunId: null,
    });
    expect(out).toEqual([
      {
        id: "tc1",
        subagent_type: "general-purpose",
        description: "d",
        prompt: "p",
      },
    ]);
  });

  it("cancelled 不带 error 字段（取消不是失败）", () => {
    const out = deriveSubtasks(
      [aiTask("a1", "tc1"), toolMsg("t1", "tc1", "Task cancelled by user.")],
      { runStatus: "interrupted" },
    );
    expect(out[0]!.status).toBe("cancelled");
    expect("error" in out[0]!).toBe(false);
  });

  it("归属模式下落在未知 run 的消息：不判终态，只登记元数据", () => {
    // messageRunIds 不完整（分片未到齐 / 消息无 id）：已结束旧 run 的任务不得被判成进行中
    const out = deriveSubtasksByRun([aiTask("a-unknown", "tc-unknown")], {
      messageRunIds: new Map(),
      runsById: { "run-1": { run_id: "run-1", status: "success" } as Run },
      activeRunId: null,
    });
    expect(out).toEqual([
      {
        id: "tc-unknown",
        subagent_type: "general-purpose",
        description: "d",
        prompt: "p",
      },
    ]);
  });

  it("history loading / 断线只约束当前流：旧 run 的终态照常判定", () => {
    // 断线状态下 runsById 的状态是确定的：已结束旧 run 里没有 result 的任务不能一直转圈
    const run = (runId: string, status: string): Run =>
      ({ run_id: runId, status }) as unknown as Run;
    const out = deriveSubtasksByRun(
      [aiTask("a-old", "tc-old"), aiTask("a-live", "tc-live")],
      {
        messageRunIds: new Map([
          ["a-old", "run-1"],
          ["a-live", "run-2"],
        ]),
        runsById: {
          "run-1": run("run-1", "interrupted"),
          "run-2": run("run-2", "running"),
        },
        activeRunId: "run-2",
      },
      { isHistoryLoading: true, isRecoverableDisconnect: true },
    );
    expect(out.map((task) => [task.id, task.status])).toEqual([
      ["tc-old", "cancelled"],
      // 当前流在断线态：不判终态（只登记元数据）
      ["tc-live", undefined],
    ]);
    expect("status" in out[1]!).toBe(false);
  });

  it("history loading / 断线：不判终态——只登记元数据，不下发会覆盖旧终态的 status", () => {
    for (const flag of [
      "isHistoryLoading",
      "isRecoverableDisconnect",
    ] as const) {
      const out = deriveSubtasks([aiTask("a1", "tc1")], {
        runStatus: "interrupted",
        [flag]: true,
      });
      expect(out).toEqual([
        {
          id: "tc1",
          subagent_type: "general-purpose",
          description: "d",
          prompt: "p",
        },
      ]);
      expect("status" in out[0]!).toBe(false);
    }
  });

  it("同一次用户停止只有一种状态：有 result（Task cancelled by user.）与没有 result 都是 cancelled", () => {
    const withResult = deriveSubtasks(
      [aiTask("a1", "tc1"), toolMsg("t1", "tc1", "Task cancelled by user.")],
      { runStatus: "interrupted" },
    );
    const withoutResult = deriveSubtasks([aiTask("a1", "tc2")], {
      runStatus: "interrupted",
    });
    expect(withResult[0]!.status).toBe("cancelled");
    expect(withoutResult[0]!.status).toBe("cancelled");
  });

  it("没给 runStatus（agents/new 等旧调用方）：保持原行为 in_progress", () => {
    const out = deriveSubtasks([aiTask("a1", "tc1")]);
    expect(out[0]!.status).toBe("in_progress");
  });

  it("扁平列表按 run 分别判定：run-1 error / interrupted 的旧任务不被 run-2 改判", () => {
    const run = (runId: string, status: string): Run =>
      ({ run_id: runId, status }) as unknown as Run;
    const messageRunIds = new Map([
      ["a-old", "run-1"],
      ["a-new", "run-2"],
    ]);
    const runsById = {
      "run-1": run("run-1", "error"),
      "run-2": run("run-2", "running"),
    };
    expect(
      deriveSubtasksByRun(
        [aiTask("a-old", "tc-old"), aiTask("a-new", "tc-new")],
        {
          messageRunIds,
          runsById,
        },
      ).map((task) => [task.id, task.status]),
    ).toEqual([
      ["tc-old", "failed"],
      ["tc-new", "in_progress"],
    ]);

    // run-1 interrupted + run-2 success（无澄清）：各自按自己的 run 判定
    expect(
      deriveSubtasksByRun(
        [aiTask("a-old", "tc-old"), aiTask("a-new", "tc-new")],
        {
          messageRunIds,
          runsById: {
            "run-1": run("run-1", "interrupted"),
            "run-2": run("run-2", "success"),
          },
        },
      ).map((task) => [task.id, task.status]),
    ).toEqual([
      ["tc-old", "cancelled"],
      ["tc-new", "completed"],
    ]);
  });

  it("不在 messageRunIds 里的消息属于当前流（activeRunId）：按该 run 的状态判定", () => {
    const out = deriveSubtasksByRun([aiTask("a-live", "tc-live")], {
      messageRunIds: new Map(),
      runsById: {
        "run-live": { run_id: "run-live", status: "running" } as Run,
      },
      activeRunId: "run-live",
    });
    expect(out.map((task) => task.status)).toEqual(["in_progress"]);
  });
});
