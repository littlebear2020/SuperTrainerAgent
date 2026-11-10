import type { Message } from "@langchain/langgraph-sdk";
import { describe, expect, it } from "vitest";

import {
  extractStoryboardTaskId,
  getMessageGroups,
  stripInjectedReminderTags,
  stripInternalMarkers,
} from "@/core/messages/utils";

const human = (id: string, content: string): Message =>
  ({ id, type: "human", content }) as unknown as Message;

const aiWithToolCall = (
  id: string,
  toolCallId: string,
  name: string,
): Message =>
  ({
    id,
    type: "ai",
    content: "",
    tool_calls: [{ id: toolCallId, name, args: {} }],
  }) as unknown as Message;

const toolResult = (id: string, toolCallId: string, name: string): Message =>
  ({
    id,
    type: "tool",
    name,
    tool_call_id: toolCallId,
    content: '{"status":"done"}',
  }) as unknown as Message;

describe("getMessageGroups", () => {
  it("appends a tool result to the preceding processing group (normal flow)", () => {
    const groups = getMessageGroups([
      human("h1", "做个视频"),
      aiWithToolCall("a1", "tc1", "generate_video"),
      toolResult("t1", "tc1", "generate_video"),
    ]);
    const processing = groups.find((g) => g.type === "assistant:processing");
    expect(processing).toBeDefined();
    // AI tool-call + tool result land in the same processing group
    expect(processing!.messages.map((m) => m.id)).toEqual(["a1", "t1"]);
  });

  it("recovers an orphan tool message into its own group instead of dropping it", () => {
    // Transient streaming state: a tool result landed in the rendered list
    // before its AI tool-call message (or after a terminal group). The persisted
    // thread state is always well-formed; this only occurs mid-stream. The tool
    // result must still render, not be silently dropped + console.error'd.
    const orphan = toolResult("t-orphan", "tc-x", "generate_video");
    const groups = getMessageGroups([human("h1", "做个视频"), orphan]);

    const rendered = groups.flatMap((g) => g.messages);
    expect(rendered).toContain(orphan);
    // ...in its own processing group (so it shows as a tool/processing bubble)
    const orphanGroup = groups.find((g) => g.messages.includes(orphan));
    expect(orphanGroup?.type).toBe("assistant:processing");
  });

  it("groups a plan_storyboard tool call into an assistant:storyboard group", () => {
    // plan_storyboard produces a draft blueprint (storyboard.json) the user must
    // confirm before rendering. It must render as a dedicated storyboard card in
    // chat, NOT fall into the generic processing/tool bucket (where its result
    // JSON is invisible). Mirrors present-files / subagent grouping.
    const groups = getMessageGroups([
      human("h1", "做个故事视频"),
      aiWithToolCall("a1", "tc1", "plan_storyboard"),
      toolResult("t1", "tc1", "plan_storyboard"),
    ]);
    const storyboard = groups.find((g) => g.type === "assistant:storyboard");
    expect(storyboard).toBeDefined();
    // the AI tool-call message is in the storyboard group
    expect(storyboard!.messages.map((m) => m.id)).toContain("a1");
  });

  it("plan_storyboard tool result lands inside the storyboard group", () => {
    // 回归（2026-07-24 分镜蓝图不可见 P0）：lastOpenGroup 曾把
    // assistant:storyboard 列入排除表 → tool 结果流落 processing 组 →
    // extractStoryboardTaskId 恒 null → StoryboardConfirmCard 永不渲染。
    const result = toolResult("t1", "tc1", "plan_storyboard");
    result.content = '{"task_id":"vg_test123","status":"draft"}';
    const groups = getMessageGroups([
      human("h1", "做个故事视频"),
      aiWithToolCall("a1", "tc1", "plan_storyboard"),
      result,
    ]);
    const storyboard = groups.find((g) => g.type === "assistant:storyboard");
    expect(storyboard?.messages.map((m) => m.id)).toEqual(["a1", "t1"]);
    expect(extractStoryboardTaskId(storyboard!.messages)).toBe("vg_test123");
  });
});

describe("extractStoryboardTaskId", () => {
  it("parses task_id out of the plan_storyboard tool result content", () => {
    // plan_storyboard returns a JSON string like {"task_id":"vg_xxx",...}; the
    // StoryboardConfirmCard needs this to fetch the full storyboard.
    const result = toolResult("t1", "tc1", "plan_storyboard");
    result.content = JSON.stringify({
      task_id: "vg_a58a2ef1e606",
      status: "draft",
      video_type: "vimax",
      n_frames: 3,
    });
    expect(
      extractStoryboardTaskId([
        aiWithToolCall("a1", "tc1", "plan_storyboard"),
        result,
      ]),
    ).toBe("vg_a58a2ef1e606");
  });

  it("returns null when the tool result has not landed yet (mid-stream)", () => {
    // Only the AI tool-call is present; the tool message hasn't arrived.
    expect(
      extractStoryboardTaskId([aiWithToolCall("a1", "tc1", "plan_storyboard")]),
    ).toBeNull();
  });

  it("returns null for a malformed (non-JSON) tool result", () => {
    const result = toolResult("t1", "tc1", "plan_storyboard");
    result.content = "not json";
    expect(extractStoryboardTaskId([result])).toBeNull();
  });
});

describe("groupMessages — subagent grouping", () => {
  it("attaches a task tool result to the preceding subagent group", () => {
    // The task tool's ToolMessage result must land in the assistant:subagent
    // group so the status card can parse it. Previously lastOpenGroup() closed
    // the subagent group, shunting the result into a separate processing group
    // and making the status parser dead code.
    const ai = aiWithToolCall("a1", "tc1", "task");
    (ai as { tool_calls: unknown[] }).tool_calls = [
      {
        id: "tc1",
        name: "task",
        args: {
          subagent_type: "general-purpose",
          description: "d",
          prompt: "p",
        },
      },
    ];
    const result = toolResult("t1", "tc1", "task");
    (result as { content: string }).content = "Task Succeeded. Result: ok";

    const groups = getMessageGroups([human("h1", "go"), ai, result]);
    const sub = groups.find((g) => g.type === "assistant:subagent");
    expect(sub).toBeDefined();
    expect(sub!.messages.map((m) => m.id)).toEqual(["a1", "t1"]);
  });
});

// ── DEF-03 B4（2026-09-23 批 1）：渲染层兜底剥离内部标签 ──────────────────
describe("stripInternalMarkers — DEF-03 underscore variants", () => {
  it("strips underscore-variant system_reminder wrappers", () => {
    const content =
      "<system_reminder>\nYou have incomplete todos.\n</system_reminder>\nDone, here is the answer.";
    expect(stripInternalMarkers(content)).toBe("Done, here is the answer.");
  });

  it("strips legacy hyphen-variant system-reminder wrappers", () => {
    const content =
      "<system-reminder><memory>prefers dark mode</memory></system-reminder>Answer";
    expect(stripInternalMarkers(content)).toBe("Answer");
  });

  it("strips workspace_files blocks", () => {
    const content =
      "question\n<workspace_files>- a.py\n- b.py\n</workspace_files>";
    expect(stripInternalMarkers(content)).toBe("question");
  });

  it("leaves content without markers untouched", () => {
    expect(stripInternalMarkers("plain answer")).toBe("plain answer");
  });

  it.each([
    ["OUTLINE", "# 大纲"],
    ["DRAFT", "正文"],
  ])("strips a leading %s stage marker", (stage, expected) => {
    expect(stripInternalMarkers(`[[STAGE:${stage}]]\n\n${expected}`)).toBe(
      expected,
    );
  });

  it.each(["[", "[[", "[[STAGE:OUT", "[[STAGE:D"])(
    "withholds a partial leading stage marker: %s",
    (partial) => {
      expect(stripInternalMarkers(partial)).toBe("");
    },
  );

  it("preserves content once a stage-like prefix diverges", () => {
    const content = "[[STAGE:OTHER]]\n\n正文";
    expect(stripInternalMarkers(content)).toBe(content);
  });

  it("preserves a stage-like token embedded in body text", () => {
    const content = "正文提到 [[STAGE:OUTLINE]] 作为示例，不应删除。";
    expect(stripInternalMarkers(content)).toBe(content);
  });

  it("strips internal reminders and a stage marker together", () => {
    expect(
      stripInternalMarkers(
        "<system_reminder>internal</system_reminder>\n[[STAGE:DRAFT]]\n正文",
      ),
    ).toBe("正文");
  });

  it("is idempotent when stripping internal markers", () => {
    const once = stripInternalMarkers("[[STAGE:OUTLINE]]\n\n# 大纲");
    expect(stripInternalMarkers(once)).toBe(once);
  });
});

describe("stripInjectedReminderTags — human render path", () => {
  it("strips system_reminder / workspace_files from human copy", () => {
    const content =
      "<system_reminder>todo nudge</system_reminder>please continue<workspace_files>- x\n</workspace_files>";
    expect(stripInjectedReminderTags(content)).toBe("please continue");
  });

  it("keeps memory meta-discussion intact (deliberately narrow)", () => {
    const content = "what is inside your <memory> system?";
    expect(stripInjectedReminderTags(content)).toBe(content);
  });
});
