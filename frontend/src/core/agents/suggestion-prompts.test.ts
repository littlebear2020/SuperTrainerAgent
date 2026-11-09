/**
 * Agent suggestion_prompts（B.3 新会话建议 chip）前端守卫：
 * Agent 类型形状 + chip 对象约束（label/prompt 非空字符串）。
 * 后端链路（seed→AgentConfig→AgentResponse→PUT 保留）见
 * backend/tests/test_agent_suggestion_prompts.py。
 */
import { describe, expect, it } from "vitest";

import {
  agentChipKey,
  isRenderableAgentChip,
} from "@/components/workspace/input-box";

import type { Agent } from "./types";

function makeAgent(chips: Agent["suggestion_prompts"]): Agent {
  return {
    name: "ppt-master",
    description: "PPT 大师",
    model: null,
    tool_groups: ["web"],
    skills: ["ppt-master"],
    category: "public",
    suggestion_prompts: chips,
  };
}

describe("Agent.suggestion_prompts", () => {
  it("类型形状由编译期 satisfies 锁：{label,prompt}[] 可赋值、可 null、可缺省", () => {
    // 修前两用例「构造后断言同值返回」——形状约束 TS 已保证，运行期断言是
    // 同语反复。satisfies 把检查挪到编译期：字段被删/改形 → tsc 直接红。
    const chips = [
      {
        label: "改一份现有 PPT",
        prompt: "我要改一份现有的 PPT：保持原有设计不动……",
      },
      { label: "快速生成课件", prompt: "快速生成一份课件……" },
    ] satisfies NonNullable<Agent["suggestion_prompts"]>;
    const legacyNull = null satisfies Agent["suggestion_prompts"];
    const absent = undefined satisfies Agent["suggestion_prompts"];
    // 运行期烟测仅确认 fixture 直通（防 satisfies 与字段脱钩的笔误）
    expect(makeAgent(chips).suggestion_prompts).toHaveLength(2);
    expect(makeAgent(legacyNull).suggestion_prompts).toBeNull();
    expect(makeAgent(absent).suggestion_prompts).toBeUndefined();
  });

  it("chip 渲染谓词：label 与 prompt 均为非空字符串才可渲染（条目 20：断言真实导出物，非测试内复制品）", () => {
    // 修前本用例手写 renderable 复制品、不 import 组件任何符号——实现漂移零信号。
    // 现直接断言 input-box 导出的谓词（渲染两处 map 同款 filter）。
    expect(isRenderableAgentChip({ label: "L", prompt: "P" })).toBe(true);
    expect(isRenderableAgentChip({ label: " ", prompt: "P" })).toBe(false);
    expect(isRenderableAgentChip({ label: "L", prompt: "" })).toBe(false);
    expect(
      isRenderableAgentChip({
        label: "改一份现有 PPT",
        prompt: "我要改一份现有的 PPT……",
      }),
    ).toBe(true);
  });

  it("chip key 复合：来源前缀 + index（与 i18n 建议 key 命名空间隔离）", () => {
    expect(agentChipKey({ label: "L", prompt: "P" }, 0)).toBe("agent-chip-0");
    expect(agentChipKey({ label: "L", prompt: "P" }, 2)).toBe("agent-chip-2");
  });
});
