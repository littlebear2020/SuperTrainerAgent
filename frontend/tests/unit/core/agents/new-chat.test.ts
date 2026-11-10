import { describe, expect, it } from "vitest";

import {
  AGENT_NEW_CHAT_GUIDE_IDS,
  isAgentNewChatGuideId,
} from "@/core/agents/new-chat";
import { resolveAgentName } from "@/core/agents/retired-mapping";
import { enUS, zhCN } from "@/core/i18n/locales";

const EXPECTED_CASE_IDS = {
  "course-video": [
    "knowledge-explainer",
    "product-tutorial",
    "concept-animation",
    "script-to-video",
  ],
  "ppt-master": ["from-scratch", "roundtrip", "beautify", "brand-template"],
  "creative-studio": [
    "knowledge-card",
    "course-poster",
    "infographic",
    "series-card",
  ],
  "co-writer": ["article", "case", "script", "podcast"],
} as const;

const EXPECTED_ZH_TITLES = {
  "course-video": "把知识讲成一堂视频课",
  "ppt-master": "从资料到可编辑的 PPT",
  "creative-studio": "让知识成为好看的图片",
  "co-writer": "把想法写成好内容",
} as const;

describe("Agent new-chat guide content", () => {
  it("targets exactly the four approved agents and their retired aliases", () => {
    expect(AGENT_NEW_CHAT_GUIDE_IDS).toEqual([
      "course-video",
      "ppt-master",
      "creative-studio",
      "co-writer",
    ]);
    expect(isAgentNewChatGuideId(resolveAgentName("card-poster"))).toBe(true);
    expect(isAgentNewChatGuideId(resolveAgentName("course-ppt"))).toBe(true);
    expect(isAgentNewChatGuideId("test-agent")).toBe(false);
  });

  it.each([
    ["zh-CN", zhCN],
    ["en-US", enUS],
  ])("keeps %s guide content complete and aligned", (_locale, messages) => {
    for (const agentName of AGENT_NEW_CHAT_GUIDE_IDS) {
      const guide = messages.agents.newChatGuides[agentName];
      expect(guide.displayName.trim()).not.toBe("");
      expect(guide.title.trim()).not.toBe("");
      expect(guide.description.trim()).not.toBe("");
      expect(guide.tips).toHaveLength(3);
      expect(guide.tips.every((tip) => tip.trim().length > 0)).toBe(true);
      expect(guide.cases.map((item) => item.id)).toEqual(
        EXPECTED_CASE_IDS[agentName],
      );
      for (const item of guide.cases) {
        expect(item.title.trim()).not.toBe("");
        expect(item.description.trim()).not.toBe("");
        expect(item.prompt.trim()).not.toBe("");
      }
    }
  });

  it("keeps the exact Chinese hero titles", () => {
    for (const agentName of AGENT_NEW_CHAT_GUIDE_IDS) {
      expect(zhCN.agents.newChatGuides[agentName].title).toBe(
        EXPECTED_ZH_TITLES[agentName],
      );
    }
  });

  it.each([
    ["zh-CN", zhCN],
    ["en-US", enUS],
  ])("preserves Co-writer routing markers in %s", (_locale, messages) => {
    for (const item of messages.agents.newChatGuides["co-writer"].cases) {
      expect(item.prompt).toContain(`[类型: ${item.id}]`);
      expect(item.prompt).toContain("[从零创作]");
    }
  });

  it.each([
    ["zh-CN", zhCN],
    ["en-US", enUS],
  ])("does not expose internal skill names in %s", (_locale, messages) => {
    for (const agentName of AGENT_NEW_CHAT_GUIDE_IDS) {
      for (const item of messages.agents.newChatGuides[agentName].cases) {
        const copy = `${item.title}\n${item.description}\n${item.prompt}`;
        expect(copy).not.toContain("auto-video-flow");
        expect(copy).not.toContain("deep-research");
        expect(copy).not.toContain("/skill");
        expect(copy).not.toContain("ppt-master");
      }
    }
  });

  it("preserves the two existing PPT workflows", () => {
    const [fromScratch, roundtrip] =
      zhCN.agents.newChatGuides["ppt-master"].cases;
    expect(fromScratch?.prompt).toContain("快速生成");
    expect(fromScratch?.prompt).toContain("跳过策略确认");
    expect(roundtrip?.prompt).toContain("现有");
    expect(roundtrip?.prompt).toContain("保持原有设计");

    const [enFromScratch, enRoundtrip] =
      enUS.agents.newChatGuides["ppt-master"].cases;
    expect(enFromScratch?.prompt).toMatch(/quickly|fast/i);
    expect(enFromScratch?.prompt).toMatch(/skip.*confirmation/i);
    expect(enRoundtrip?.prompt).toMatch(/existing/i);
    expect(enRoundtrip?.prompt).toMatch(/keep|preserve/i);
    expect(enRoundtrip?.prompt).toMatch(/design/i);
  });
});
