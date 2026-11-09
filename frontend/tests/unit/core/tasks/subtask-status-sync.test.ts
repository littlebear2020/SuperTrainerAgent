import type { Message } from "@langchain/langgraph-sdk";
import { describe, expect, it } from "vitest";

import { deriveSubtasks } from "@/core/tasks/subtask-status-sync";

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
