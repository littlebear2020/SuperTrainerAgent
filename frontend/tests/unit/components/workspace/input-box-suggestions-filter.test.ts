/**
 * D-3 #8：追问建议取数必须过滤 hidden 消息。
 *
 * 注入的动态上下文（记忆全文）以 human 身份进入消息数组；POST body 只
 * 序列化 role+content，后端无法再区分——前端取数是唯一有效拦截点。
 */
import type { Message } from "@langchain/langgraph-sdk";
import { describe, expect, it } from "vitest";

import { selectRecentForSuggestions } from "@/components/workspace/input-box";

const human = (id: string, content: string, extra = {}): Message =>
  ({ id, type: "human", content, ...extra }) as Message;
const ai = (id: string, content: string, extra = {}): Message =>
  ({ id, type: "ai", content, ...extra }) as Message;

describe("selectRecentForSuggestions", () => {
  it("excludes hidden dynamic-context reminders from the suggestions payload", () => {
    const messages = [
      human("m1", "帮我规划一下学习路线"),
      // 注入的记忆块：hide_from_ui + dynamic_context_reminder 双标记
      human(
        "m1__memory",
        "<memory>\n用户偏好：喜欢简洁回答，常用中文\n</memory>",
        {
          additional_kwargs: {
            hide_from_ui: true,
            dynamic_context_reminder: true,
          },
        },
      ),
      ai("a1", "好的，这是三步计划……"),
    ];

    const recent = selectRecentForSuggestions(messages);
    expect(recent).toEqual([
      { role: "user", content: "帮我规划一下学习路线" },
      { role: "assistant", content: "好的，这是三步计划……" },
    ]);
  });

  it("excludes todo reminders hidden by the hide_from_ui marker", () => {
    const messages = [
      human("u1", "继续做任务"),
      human("todo-1", "Your todo list from earlier is no longer visible…", {
        name: "todo_reminder",
        additional_kwargs: { hide_from_ui: true },
      }),
      ai("a1", "完成"),
    ];

    const recent = selectRecentForSuggestions(messages);
    expect(recent).toHaveLength(2);
    expect(recent.some((m) => m.content.includes("todo list"))).toBe(false);
  });

  it("keeps only the last 6 non-empty turns and drops empty content", () => {
    const messages = [
      human("u0", ""),
      ...Array.from({ length: 8 }, (_, i) =>
        human(`u${i + 1}`, `问题${i + 1}`),
      ),
    ];

    const recent = selectRecentForSuggestions(messages);
    expect(recent).toHaveLength(6);
    expect(recent[0]).toEqual({ role: "user", content: "问题3" });
    expect(recent[5]).toEqual({ role: "user", content: "问题8" });
  });
});
