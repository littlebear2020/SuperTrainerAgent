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
  "ppt-master": ["from-scratch", "roundtrip", "beautify", "document-to-course"],
  "creative-studio": [
    "knowledge-card",
    "course-poster",
    "infographic",
    "series-card",
  ],
  "co-writer": ["article", "case", "script", "podcast"],
} as const;

const EXPECTED_ZH_TITLES = {
  "course-video": "把知识与素材做成视频课",
  "ppt-master": "从资料到可编辑的 PPT",
  "creative-studio": "让知识成为好看的图片",
  "co-writer": "把想法写成好内容",
} as const;

const EXPECTED_CO_WRITER_CASES = {
  "zh-CN": [
    {
      id: "article",
      title: "写一篇公众号文章",
      description: "有观点、有案例，也适合手机阅读",
      prompt:
        "[类型: article]\n[从零创作]\n请围绕[主题]写一篇公众号文章，面向[受众]。先给出大纲；需要最新案例或数据时请联网查找可靠来源。",
    },
    {
      id: "case",
      title: "整理商业案例",
      description: "讲清背景、方案、结果与启示",
      prompt:
        "[类型: case]\n[从零创作]\n请围绕[企业/项目]写一篇商业案例，按背景、问题、方案、结果和启示展开；涉及数据时请查找并标注真实来源机构。",
    },
    {
      id: "script",
      title: "创作视频脚本",
      description: "自然口语化，节奏适合直接录制",
      prompt:
        "[类型: script]\n[从零创作]\n请把[主题]写成一段约[时长]的口播视频脚本，面向[受众]，语言自然、有画面感，直接给我可用稿件。",
    },
    {
      id: "podcast",
      title: "准备播客文稿",
      description: "对话感强，适合朗读和音频表达",
      prompt:
        "[类型: podcast]\n[从零创作]\n请围绕[主题]写一篇约[时长]的播客文稿，采用自然对话感，适合直接朗读。",
    },
  ],
  "en-US": [
    {
      id: "article",
      title: "Write an article",
      description: "A clear point of view with engaging examples",
      prompt:
        "[类型: article]\n[从零创作]\nWrite a public-facing article about [topic] for [audience]. Start with an outline and find reliable recent examples or data online when needed.",
    },
    {
      id: "case",
      title: "Develop a business case",
      description: "Cover context, solution, results, and lessons",
      prompt:
        "[类型: case]\n[从零创作]\nWrite a business case about [company/project], covering background, problem, solution, results, and lessons. Find real sources and name the source organization for any data.",
    },
    {
      id: "script",
      title: "Write a video script",
      description: "Natural speech with pacing ready to record",
      prompt:
        "[类型: script]\n[从零创作]\nWrite a [duration] spoken video script about [topic] for [audience]. Use natural, visual language and provide a recording-ready draft directly.",
    },
    {
      id: "podcast",
      title: "Prepare a podcast script",
      description: "Conversational writing designed for listening",
      prompt:
        "[类型: podcast]\n[从零创作]\nWrite a [duration] podcast script about [topic] with a natural conversational style, suitable for reading aloud.",
    },
  ],
} as const;

const SCENARIO_CONTRACTS = [
  {
    agent: "creative-studio",
    id: "knowledge-card",
    locale: "zh-CN",
    title: "生成毛玻璃知识卡",
    required: [/毛玻璃/, /SVG/, /MP4/, /科技蓝、霓虹紫或翡翠绿/],
  },
  {
    agent: "creative-studio",
    id: "knowledge-card",
    locale: "en-US",
    title: "Create glassmorphism knowledge cards",
    required: [
      /glassmorphism/i,
      /SVG/,
      /MP4/,
      /tech blue, neon purple, or emerald green/i,
    ],
  },
  {
    agent: "creative-studio",
    id: "course-poster",
    locale: "zh-CN",
    title: "设计科技风课程海报",
    required: [
      /课程标题/,
      /分享日期/,
      /课程时长/,
      /1080×1920/,
      /PNG/,
      /科技风/,
    ],
  },
  {
    agent: "creative-studio",
    id: "course-poster",
    locale: "en-US",
    title: "Design a tech-style course poster",
    required: [
      /course title/i,
      /sharing date/i,
      /course duration/i,
      /1080×1920/,
      /PNG/,
      /tech-style/i,
    ],
  },
  {
    agent: "creative-studio",
    id: "infographic",
    locale: "zh-CN",
    title: "制作单张中文信息图",
    required: [/一张中文信息图/, /21种布局/, /22种风格/, /infographic\.png/],
  },
  {
    agent: "creative-studio",
    id: "infographic",
    locale: "en-US",
    title: "Create one Chinese infographic",
    required: [
      /one Chinese infographic/i,
      /21 layouts/i,
      /22 styles/i,
      /infographic\.png/,
    ],
  },
  {
    agent: "creative-studio",
    id: "series-card",
    locale: "zh-CN",
    title: "把知识点画成连续漫画",
    required: [
      /连续知识漫画/,
      /默认建议4页/,
      /调整页数/,
      /画风/,
      /角色表/,
      /分镜/,
      /PDF/,
    ],
  },
  {
    agent: "creative-studio",
    id: "series-card",
    locale: "en-US",
    title: "Turn knowledge into a continuous comic",
    required: [
      /continuous knowledge comic/i,
      /four pages by default/i,
      /adjust the page count/i,
      /art style/i,
      /character sheet/i,
      /storyboard/i,
      /PDF/,
    ],
  },
  {
    agent: "course-video",
    id: "knowledge-explainer",
    locale: "zh-CN",
    title: "调研主题做知识科普",
    required: [/调研/, /知识科普/, /讲稿/, /分镜/, /TTS/, /Remotion/, /MP4/],
  },
  {
    agent: "course-video",
    id: "knowledge-explainer",
    locale: "en-US",
    title: "Research a topic into an explainer",
    required: [
      /research/i,
      /explainer/i,
      /script/i,
      /storyboard/i,
      /TTS/,
      /Remotion/,
      /MP4/,
    ],
  },
  {
    agent: "course-video",
    id: "product-tutorial",
    locale: "zh-CN",
    title: "用我的截图做产品讲解",
    required: [/真实截图/, /图文版式/, /TTS/, /Remotion/, /MP4/],
  },
  {
    agent: "course-video",
    id: "product-tutorial",
    locale: "en-US",
    title: "Turn my screenshots into a product walkthrough",
    required: [
      /real screenshots/i,
      /image-and-text layouts/i,
      /TTS/,
      /Remotion/,
      /MP4/,
    ],
  },
  {
    agent: "course-video",
    id: "concept-animation",
    locale: "zh-CN",
    title: "把我的数据做成动态图表",
    required: [/提供.*数据/, /图表/, /动态/, /TTS/, /Remotion/, /MP4/],
  },
  {
    agent: "course-video",
    id: "concept-animation",
    locale: "en-US",
    title: "Turn my data into animated charts",
    required: [
      /provide.*data/i,
      /charts/i,
      /animated/i,
      /TTS/,
      /Remotion/,
      /MP4/,
    ],
  },
  {
    agent: "course-video",
    id: "script-to-video",
    locale: "zh-CN",
    title: "完整讲稿保真转视频",
    required: [
      /完整口播稿/,
      /逐字保留/,
      /不改写、不扩写/,
      /TTS/,
      /Remotion/,
      /MP4/,
    ],
  },
  {
    agent: "course-video",
    id: "script-to-video",
    locale: "en-US",
    title: "Turn my full script into video faithfully",
    required: [
      /complete narration script/i,
      /word for word/i,
      /do not rewrite or expand/i,
      /TTS/,
      /Remotion/,
      /MP4/,
    ],
  },
  {
    agent: "ppt-master",
    id: "from-scratch",
    locale: "zh-CN",
    title: "按主题快速生成课件",
    required: [/快速生成/, /Quick/, /跳过Stage 1\/2/, /质量检查/, /PPTX/],
  },
  {
    agent: "ppt-master",
    id: "from-scratch",
    locale: "en-US",
    title: "Quickly generate a deck from a topic",
    required: [
      /quickly/i,
      /Quick/,
      /skip Stage 1\/2/i,
      /quality checks/i,
      /PPTX/,
    ],
  },
  {
    agent: "ppt-master",
    id: "roundtrip",
    locale: "zh-CN",
    title: "局部修改现有 PPT",
    required: [/Edit Native/, /保持原设计/, /未编辑页不要重建/, /PPTX/],
  },
  {
    agent: "ppt-master",
    id: "roundtrip",
    locale: "en-US",
    title: "Edit selected content in an existing PPT",
    required: [
      /Edit Native/,
      /preserve the original design/i,
      /do not rebuild unedited slides/i,
      /PPTX/,
    ],
  },
  {
    agent: "ppt-master",
    id: "beautify",
    locale: "zh-CN",
    title: "1:1 美化现有 PPT",
    required: [
      /Beautify/,
      /文字、页数和页序1:1不变/,
      /拥挤页只在原页内改善/,
      /按提取数据重建/,
      /PPTX/,
    ],
  },
  {
    agent: "ppt-master",
    id: "beautify",
    locale: "en-US",
    title: "Beautify an existing PPT 1:1",
    required: [
      /Beautify/,
      /text, slide count, and slide order 1:1/i,
      /crowded slides only within the original slide/i,
      /rebuild charts and tables from extracted data/i,
      /PPTX/,
    ],
  },
  {
    agent: "ppt-master",
    id: "document-to-course",
    locale: "zh-CN",
    title: "把文档精编成课程 PPT",
    required: [
      /PDF、DOCX或Markdown/,
      /Default/,
      /忠于资料/,
      /确认课程结构和设计方案/,
      /新的可编辑PPTX/,
    ],
  },
  {
    agent: "ppt-master",
    id: "document-to-course",
    locale: "en-US",
    title: "Turn documents into a course deck",
    required: [
      /PDF, DOCX, or Markdown/i,
      /Default/,
      /faithful to the source material/i,
      /confirm the course structure and design approach/i,
      /new editable PPTX/i,
    ],
  },
] as const;

const FORBIDDEN_BY_CASE = {
  "knowledge-card": /任意风格|自定义品牌|any style|custom brand/i,
  "course-poster": /横版|多尺寸|landscape|multiple sizes/i,
  infographic: /多页信息图|自动拆多张|multi-page|multiple infographics/i,
  "series-card":
    /技能默认4页|一次出无限页|four-panel.*(?:4|four) pages|unlimited pages/i,
  "knowledge-explainer": /实时联网|最新进展|real-time|latest developments/i,
  "product-tutorial":
    /自动录屏|自动截取界面|截图标注|标注箭头|screen.?record|annotat/i,
  "concept-animation":
    /自动查证|自动溯源|标注引用出处|automatically (?:verify|trace)|citation/i,
  "script-to-video": /帮你润色|润色讲稿|polish (?:the )?script/i,
  "from-scratch": /无质量检查|without quality checks|skip quality/i,
  roundtrip: /重排版式|re-layout|redesign/i,
  beautify:
    /重新组织结构|拆分拥挤页|逐字节|reorganize|split crowded|byte-for-byte/i,
  "document-to-course":
    /Quick|Beautify|Create Template|创建模板|快速生成|品牌模板/i,
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

  it.each(SCENARIO_CONTRACTS)(
    "matches the $locale $agent/$id scenario contract",
    (contract) => {
      const messages = contract.locale === "zh-CN" ? zhCN : enUS;
      const item = messages.agents.newChatGuides[contract.agent].cases.find(
        (candidate) => candidate.id === contract.id,
      );
      expect(item?.title).toBe(contract.title);
      const copy = `${item?.title}\n${item?.description}\n${item?.prompt}`;
      for (const pattern of contract.required) {
        expect(copy).toMatch(pattern);
      }
      const forbidden = FORBIDDEN_BY_CASE[contract.id];
      expect(copy).not.toMatch(forbidden);
    },
  );

  it.each([
    ["zh-CN", zhCN, EXPECTED_CO_WRITER_CASES["zh-CN"]],
    ["en-US", enUS, EXPECTED_CO_WRITER_CASES["en-US"]],
  ])(
    "preserves every Co-writer case field in %s",
    (_locale, messages, expected) => {
      expect(messages.agents.newChatGuides["co-writer"].cases).toEqual(
        expected,
      );
    },
  );

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
    expect(fromScratch?.prompt).toContain("跳过Stage 1/2");
    expect(roundtrip?.prompt).toContain("现有");
    expect(roundtrip?.prompt).toContain("保持原设计");

    const [enFromScratch, enRoundtrip] =
      enUS.agents.newChatGuides["ppt-master"].cases;
    expect(enFromScratch?.prompt).toMatch(/quickly|fast/i);
    expect(enFromScratch?.prompt).toMatch(/skip Stage 1\/2/i);
    expect(enRoundtrip?.prompt).toMatch(/existing/i);
    expect(enRoundtrip?.prompt).toMatch(/keep|preserve/i);
    expect(enRoundtrip?.prompt).toMatch(/design/i);
  });
});
