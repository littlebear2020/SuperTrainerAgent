import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

import { describe, expect, it } from "vitest";

import {
  showcaseFor,
  type ShowcaseAgent,
  type ShowcaseCase,
  type VisualCase,
} from "@/core/agents/showcase";

const publicRoot = join(process.cwd(), "public");
const agentIds = [
  "course-video",
  "ppt-master",
  "podcast-studio",
  "creative-studio",
] as const;

const copy = (zh: string, en: string) => ({ zh, en });

type VisualExpectation = {
  id: string;
  category: VisualCase["category"];
  type: string;
  title: { zh: string; en: string };
  tag: { zh: string; en: string };
  description: { zh: string; en: string };
  formatLabel: { zh: string; en: string };
  fields: string[];
  spec: string;
  preview: string;
  original: string;
  vector?: string;
  downloadName: string;
  width: number;
  height: number;
};

const visualExpectations: VisualExpectation[] = [
  {
    id: "visual-poster-agent",
    category: "poster",
    type: "课程海报",
    title: copy("AI Agent 实战训练营", "AI Agent Bootcamp"),
    tag: copy("课程海报 · 训练营招生", "Course poster · Bootcamp recruitment"),
    description: copy(
      "把学习成果、实战项目、适合人群和报名方式集中在一张竖版海报中。",
      "Present learning outcomes, hands-on projects, target learners, and enrollment details in one portrait poster.",
    ),
    formatLabel: copy("PNG 图片", "PNG image"),
    fields: [
      "使用场景：【请填写，如社群招募】",
      "目标受众与基础：【请填写】",
      "核心学习成果：【请填写】",
      "三个实战项目：【请填写】",
      "课程周期与服务：【请填写】",
      "行动号召：【请填写】",
    ],
    spec: "输出 1080×1920 PNG。直接编写自包含 HTML+CSS+SVG，并使用 /opt/chrome-headless-shell/chrome-headless-shell --headless=new --disable-gpu --no-sandbox --screenshot=OUTPUT_PATH.png --window-size=1080,1920 file:///INPUT_PATH.html 截图；不调用 course-poster 固定模板，不套 Systemic Pulse 默认配色或固定区块。深空黑背景、冷青网络拓扑、琥珀强调；依次呈现小眉题、大标题、成果副标题、Agent 工作流、三个项目、受众与课程设置、CTA；不使用无来源数据。",
    preview: "/agent-showcase/visual/previews/poster-agent-bootcamp.webp",
    original: "/agent-showcase/visual/originals/poster-agent-bootcamp.png",
    downloadName: "visual-poster-agent.png",
    width: 1080,
    height: 1920,
  },
  {
    id: "visual-poster-manager",
    category: "poster",
    type: "课程海报",
    title: copy("新任管理者 90 天加速营", "90-Day New Manager Accelerator"),
    tag: copy("课程海报 · 企业内训", "Course poster · Corporate training"),
    description: copy(
      "用 90 天成长路径说明课程安排，帮助培训负责人展示企业内训项目。",
      "Explain the program through a 90-day learning path and help training leaders present an internal management course.",
    ),
    formatLabel: copy("PNG 图片", "PNG image"),
    fields: [
      "使用场景：【请填写，如企业内训】",
      "目标受众：【请填写】",
      "核心学习成果：【请填写】",
      "三个阶段与内容：【请填写】",
      "工作坊与情境演练：【请填写】",
      "行动号召：【请填写】",
    ],
    spec: "输出 1080×1920 PNG。直接编写自包含 HTML+CSS+SVG，并使用 /opt/chrome-headless-shell/chrome-headless-shell --headless=new --disable-gpu --no-sandbox --screenshot=OUTPUT_PATH.png --window-size=1080,1920 file:///INPUT_PATH.html 截图；不调用 course-poster 固定模板，不套 Systemic Pulse 默认配色或固定区块。采用暖白底、亮橙、钴蓝与黑色大字的瑞士编辑网格；依次呈现课程标题、大号周期标识、适合人群、三阶段时间轴、工作坊与情境演练、CTA；阶段名称与周期使用用户填写的内容，示例里的 30/60/90 只说明位置，不写进新海报；不使用装饰性伪数据图表。",
    preview: "/agent-showcase/visual/previews/poster-manager-90-days.webp",
    original: "/agent-showcase/visual/originals/poster-manager-90-days.png",
    downloadName: "visual-poster-manager.png",
    width: 1080,
    height: 1920,
  },
  {
    id: "visual-card-rag",
    category: "card",
    type: "知识图解",
    title: copy("RAG：让模型先查再答", "RAG: Retrieve Before Answering"),
    tag: copy(
      "知识图解 · 技术概念科普",
      "Knowledge graphic · Technical concept explainer",
    ),
    description: copy(
      "用检索、增强、生成三步，讲清 RAG 如何得到有依据的回答。",
      "Explain how RAG produces grounded answers through retrieval, augmentation, and generation.",
    ),
    formatLabel: copy("JPG 图片", "JPG image"),
    fields: [
      "主题：【请填写】",
      "核心知识点：【请填写】",
      "必须准确呈现的文字：【请填写】",
    ],
    spec: "深靛紫底、半透明 3D 玻璃、青绿高光、三幕横向构图与 16:9 画幅；信息结构按用户内容重新组织",
    preview: "/agent-showcase/visual/previews/card-rag.webp",
    original: "/agent-showcase/visual/originals/card-rag.jpg",
    downloadName: "visual-card-rag.jpg",
    width: 2048,
    height: 1152,
  },
  {
    id: "visual-card-prompt",
    category: "card",
    type: "知识图解",
    title: copy("提示词的四个要素", "The Four Elements of a Prompt"),
    tag: copy(
      "知识图解 · 方法模板讲解",
      "Knowledge graphic · Reusable method template",
    ),
    description: copy(
      "用角色、任务、上下文、格式四项，帮助学员写出可复用的提示词。",
      "Help learners write reusable prompts with four elements: role, task, context, and format.",
    ),
    formatLabel: copy("JPG 图片", "JPG image"),
    fields: [
      "主题：【请填写】",
      "核心知识点：【请填写】",
      "必须准确呈现的文字：【请填写】",
    ],
    spec: "深色终端手稿、正文与四段批注、蓝 / 珊瑚 / 绿 / 紫标注，16:9 画幅；手稿内容全部由用户替换",
    preview: "/agent-showcase/visual/previews/card-prompt.webp",
    original: "/agent-showcase/visual/originals/card-prompt.jpg",
    downloadName: "visual-card-prompt.jpg",
    width: 2048,
    height: 1152,
  },
  {
    id: "visual-comic-customer",
    category: "comic",
    type: "知识漫画",
    title: copy(
      "客户投诉处理四步法",
      "Four Steps for Handling Customer Complaints",
    ),
    tag: copy(
      "知识漫画 · 客服情景演练",
      "Knowledge comic · Customer service role-play",
    ),
    description: copy(
      "通过四个连续对话场景，练习倾听、复述、给出方案和确认结果。",
      "Practice listening, paraphrasing, proposing a solution, and confirming the outcome through four connected conversations.",
    ),
    formatLabel: copy("PNG 图片", "PNG image"),
    fields: [
      "主题：【请填写】",
      "角色与四格情节：【请填写】",
      "必须保留的对白：【请填写】",
    ],
    spec: "使用 trainerai-comic 现有 --art ligne-claire --tone warm --layout four-panel --aspect 16:9；不要使用 --style four-panel。严格四格起承转合，统一人物造型与正常比例，表情清晰，包含职场环境和关键道具，对白简洁且逻辑连续。",
    preview: "/agent-showcase/visual/previews/comic-customer-service.webp",
    original: "/agent-showcase/visual/originals/comic-customer-service.png",
    downloadName: "visual-comic-customer.png",
    width: 2048,
    height: 1152,
  },
  {
    id: "visual-comic-gradient",
    category: "comic",
    type: "知识漫画",
    title: copy("梯度下降：下山的路", "Gradient Descent: The Way Downhill"),
    tag: copy(
      "知识漫画 · 抽象概念入门",
      "Knowledge comic · Abstract concept primer",
    ),
    description: copy(
      "用下山比喻解释梯度、学习率和参数更新，降低初学者的理解门槛。",
      "Use a downhill metaphor to explain gradients, learning rate, and parameter updates for beginners.",
    ),
    formatLabel: copy("JPG 图片", "JPG image"),
    fields: [
      "主题：【请填写】",
      "角色与四格情节：【请填写】",
      "必须保留的对白：【请填写】",
    ],
    spec: "使用 trainerai-comic 的 ink-brush 画风，16:9 四格连续叙事、克制留白；不要套用极简 four-panel 预设。比喻需附带必要技术边界，不承诺全局最优。",
    preview: "/agent-showcase/visual/previews/comic-gradient.webp",
    original: "/agent-showcase/visual/originals/comic-gradient.jpg",
    downloadName: "visual-comic-gradient.jpg",
    width: 2048,
    height: 1152,
  },
  {
    id: "visual-infographic-training",
    category: "infographic",
    type: "知识图解",
    title: copy("大模型训练与上线", "LLM Training and Deployment"),
    tag: copy(
      "知识图解 · 技术流程梳理",
      "Knowledge graphic · Technical workflow overview",
    ),
    description: copy(
      "从数据清洗到部署，快速看清大模型训练与上线的关键环节。",
      "See the key stages from data cleaning to deployment in one LLM training and serving overview.",
    ),
    formatLabel: copy("JPG 图片", "JPG image"),
    fields: [
      "主题：【请填写】",
      "核心知识点：【请填写】",
      "必须准确呈现的文字：【请填写】",
    ],
    spec: "冷白数据管线、横向节点、连续箭头和输入 / 输出标注，16:9 画幅；阶段数量与内容由用户决定，简化流程须明确标注。",
    preview: "/agent-showcase/visual/previews/infographic-training.webp",
    original: "/agent-showcase/visual/originals/infographic-training.jpg",
    downloadName: "visual-infographic-training.jpg",
    width: 2048,
    height: 1152,
  },
  {
    id: "visual-infographic-roadmap",
    category: "infographic",
    type: "知识图解",
    title: copy(
      "企业 AI Agent 落地路线图",
      "Enterprise AI Agent Adoption Roadmap",
    ),
    tag: copy(
      "知识图解 · 企业落地规划",
      "Knowledge graphic · Enterprise adoption planning",
    ),
    description: copy(
      "用评估、试点、扩展、治理四阶段，讨论企业 Agent 项目如何推进。",
      "Discuss enterprise Agent adoption through assessment, pilot, scale, and governance.",
    ),
    formatLabel: copy("JPG 图片", "JPG image"),
    fields: [
      "主题：【请填写】",
      "核心知识点：【请填写】",
      "必须准确呈现的文字：【请填写】",
    ],
    spec: "深绿时间线、S 形路径、阶段节点与停止条件，16:9 画幅；阶段与说明由用户重新填写",
    preview: "/agent-showcase/visual/previews/infographic-roadmap.webp",
    original: "/agent-showcase/visual/originals/infographic-roadmap.jpg",
    downloadName: "visual-infographic-roadmap.jpg",
    width: 2048,
    height: 1152,
  },
  {
    id: "visual-diagram-talent",
    category: "diagram",
    type: "结构图",
    title: copy(
      "企业 AI 人才能力模型",
      "Enterprise AI Talent Competency Model",
    ),
    tag: copy("结构图 · 人才能力分层", "Diagram · Talent competency layers"),
    description: copy(
      "把业务场景、AI 协作、学习资源和评价标准组织成一套培养体系。",
      "Organize business scenarios, AI collaboration, learning resources, and assessment into one development framework.",
    ),
    formatLabel: copy("可编辑 SVG", "Editable SVG"),
    fields: [
      "主题：【请填写】",
      "节点与层级：【请填写】",
      "连接关系：【请填写】",
    ],
    spec: "使用 trainerai-diagram 手写自包含横版 SVG；石墨暗色底、四层组织与蓝 / 绿 / 琥珀 / 紫分组，层级、节点与关系由用户内容决定；根节点不设置固定 width/height，viewBox 根据实际内容和 30px 边距计算，内容超出时扩大 viewBox。",
    preview: "/agent-showcase/visual/previews/diagram-talent-model.webp",
    original: "/agent-showcase/visual/diagrams/diagram-talent-model.svg",
    vector: "/agent-showcase/visual/diagrams/diagram-talent-model.svg",
    downloadName: "visual-diagram-talent.svg",
    width: 1600,
    height: 1000,
  },
  {
    id: "visual-diagram-addie",
    category: "diagram",
    type: "结构图",
    title: copy("ADDIE 课程开发流程", "ADDIE Course Development Process"),
    tag: copy("结构图 · 课程开发设计", "Diagram · Course development design"),
    description: copy(
      "展示从分析到评估的课程开发闭环，以及每个阶段需要交付的成果。",
      "Show the course development loop from analysis to evaluation and the deliverables expected at each stage.",
    ),
    formatLabel: copy("可编辑 SVG", "Editable SVG"),
    fields: [
      "主题：【请填写】",
      "节点与层级：【请填写】",
      "连接关系：【请填写】",
    ],
    spec: "使用 trainerai-diagram 手写自包含横版 SVG；钴蓝暗色网格、五阶段流程、阶段产物与评估回流，节点与拓扑由用户内容决定；根节点不设置固定 width/height，viewBox 根据实际内容和 30px 边距计算，内容超出时扩大 viewBox。",
    preview: "/agent-showcase/visual/previews/diagram-addie-flow.webp",
    original: "/agent-showcase/visual/diagrams/diagram-addie-flow.svg",
    vector: "/agent-showcase/visual/diagrams/diagram-addie-flow.svg",
    downloadName: "visual-diagram-addie.svg",
    width: 1600,
    height: 1000,
  },
];

function creativeVisualCases(): VisualCase[] {
  return agent("creative-studio").cases.filter(
    (item): item is VisualCase => item.kind === "visual",
  );
}

function expectedVisualPrompt(expected: VisualExpectation): string {
  return [
    `[模式: ${expected.category === "diagram" ? "结构图" : "视觉创作"}]`,
    `请按参考风格制作一张${expected.type}。`,
    "",
    "【我的内容】",
    ...expected.fields,
    "",
    "【风格与版式】",
    expected.spec,
    "",
    "仅参考视觉风格，不复制案例标题、知识点和文案。如果我的内容仍为空，请先向我索取，不要使用案例内容补齐。",
  ].join("\n");
}

function agent(id: (typeof agentIds)[number]): ShowcaseAgent {
  const value = showcaseFor(id);
  if (!value) throw new Error(`Missing showcase for ${id}`);
  return value;
}

function filesUnder(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    return entry.isDirectory() ? filesUnder(path) : [path];
  });
}

function caseAssets(item: ShowcaseCase): string[] {
  switch (item.kind) {
    case "video":
      return [item.poster, item.video];
    case "visual":
      return [
        item.preview,
        item.original,
        ...(item.vector ? [item.vector] : []),
      ];
    case "deck":
      return [...item.slides, item.pptx];
    case "podcast":
      return [item.audio];
  }
}

function expectLocalized(value: unknown): void {
  if (!value || typeof value !== "object") return;
  if (Array.isArray(value)) {
    value.forEach(expectLocalized);
    return;
  }
  if ("zh" in value || "en" in value) {
    const localized = value as { zh: unknown; en: unknown };
    for (const entry of [localized.zh, localized.en]) {
      if (Array.isArray(entry)) {
        expect(entry.length).toBeGreaterThan(0);
        for (const line of entry) {
          expect(line).toEqual(expect.any(String));
          expect(line.trim()).not.toBe("");
        }
      } else {
        expect(entry).toEqual(expect.any(String));
        if (typeof entry !== "string") {
          throw new Error("Localized text must be a string or string array");
        }
        expect(entry.trim()).not.toBe("");
      }
    }
    return;
  }
  Object.values(value).forEach(expectLocalized);
}

describe("Agent showcase data and assets", () => {
  it("resolves only the four showcase agents with the planned case inventory", () => {
    expect(agentIds.map((id) => agent(id).cases.length)).toEqual([3, 3, 4, 10]);
    expect(agentIds.map((id) => agent(id).cases[0]?.kind)).toEqual([
      "video",
      "deck",
      "podcast",
      "visual",
    ]);
    expect(showcaseFor("co-writer")).toBeNull();
    expect(showcaseFor("unknown-agent")).toBeNull();

    for (const id of agentIds) {
      const value = agent(id);
      expect(value.id).toBe(id);
      expect(value.heroLine.zh).toHaveLength(2);
      expect(value.heroLine.en).toHaveLength(2);
      expect(new Set(value.cases.map((item) => item.id)).size).toBe(
        value.cases.length,
      );
      expectLocalized(value);
      for (const item of value.cases) {
        expect(item.prompt.trim()).not.toBe("");
        expect(item.prompt).toMatch(/[一-鿿]/);
      }
    }
  });

  it("matches all ten approved visual cases, prompts, formats and asset contracts", () => {
    const visuals = creativeVisualCases();
    expect(visuals.map((item) => item.id)).toEqual(
      visualExpectations.map((item) => item.id),
    );
    expect(agent("creative-studio").note).toEqual(
      copy(
        "10 个案例覆盖课程海报、知识图解、知识漫画与结构图，用于招生推广、概念讲解、情景演练、流程梳理和可编辑结构图；实际作品按用户内容选择生图、精确排版或 SVG。",
        "Ten examples across course posters, knowledge graphics, knowledge comics, and diagrams, covering recruitment, concept explainers, role-play, workflow overviews, and editable diagrams; each output uses image generation, precise layout, or SVG as appropriate for the user's content.",
      ),
    );

    for (const [index, expected] of visualExpectations.entries()) {
      const item = visuals[index]!;
      const where = expected.id;
      expect(item.category, where).toBe(expected.category);
      expect(item.title, where).toEqual(expected.title);
      expect(item.tag, where).toEqual(expected.tag);
      expect(item.description, where).toEqual(expected.description);
      expect(item.formatLabel, where).toEqual(expected.formatLabel);
      expect(item.recipeNote, where).toEqual(
        copy(
          "保留：示例的画幅、构图、配色与版式。替换：主题、文案和知识点。内容齐全时 agent 会直接出图；缺内容会先向你索取。",
          "Kept: the example's aspect ratio, composition, palette, and layout. Replace: the topic, copy, and knowledge points. With complete content the agent generates directly; it asks first when something is missing.",
        ),
      );
      expect(item.prompt, where).toBe(expectedVisualPrompt(expected));
      expect(item.preview, where).toBe(expected.preview);
      expect(item.original, where).toBe(expected.original);
      expect(item.vector, where).toBe(expected.vector);
      expect(item.downloadName, where).toBe(expected.downloadName);
      expect(item.width, where).toBe(expected.width);
      expect(item.height, where).toBe(expected.height);

      const extension = expected.formatLabel.zh.startsWith("PNG")
        ? "png"
        : expected.formatLabel.zh.startsWith("JPG")
          ? "jpg"
          : "svg";
      expect(item.original.endsWith(`.${extension}`), where).toBe(true);
      expect(item.downloadName.endsWith(`.${extension}`), where).toBe(true);
      expect(item.preview.endsWith(".webp"), where).toBe(true);
      if (extension === "svg") expect(item.vector, where).toBe(item.original);
    }
  });

  it("ships both replacement diagrams as self-contained scalable SVG files", () => {
    for (const name of ["diagram-talent-model", "diagram-addie-flow"]) {
      const svg = readFileSync(
        join(publicRoot, "agent-showcase/visual/diagrams", `${name}.svg`),
        "utf8",
      );
      const root = /<svg\b[^>]*>/.exec(svg)?.[0];
      expect(root, name).toBeDefined();
      expect(root, name).toContain('viewBox="0 0 1600 1000"');
      expect(root, name).not.toMatch(/\s(?:width|height)=/);
      expect(svg.match(/https?:\/\/[^"\s)]+/g), name).toEqual([
        "http://www.w3.org/2000/svg",
      ]);
      expect(svg, name).not.toMatch(/<image\b/i);
      expect(svg, name).not.toMatch(/@import/i);
    }
  });

  it("keeps every referenced asset present, referenced and within budget", () => {
    const references = new Set(
      agentIds.flatMap((id) => {
        const value = agent(id);
        return [value.character, ...value.cases.flatMap(caseAssets)];
      }),
    );
    const assetRoot = join(publicRoot, "agent-showcase");
    const actual = filesUnder(assetRoot)
      .filter((path) => !path.endsWith("README.md"))
      .map((path) => {
        const url = `/${relative(publicRoot, path).split("\\").join("/")}`;
        const reference = references.has(url);
        expect(reference, `Unreferenced asset: ${url}`).toBe(true);
        return url;
      })
      .sort();

    expect([...references].sort()).toEqual(actual);
    expect(references.size).toBe(58);
    expect(
      actual.filter((path) => path.startsWith("/agent-showcase/hero/")),
    ).toHaveLength(4);
    expect(actual.filter((path) => path.endsWith(".mp3"))).toHaveLength(4);
    expect(
      actual.filter((path) =>
        path.startsWith("/agent-showcase/visual/previews/"),
      ),
    ).toHaveLength(10);
    expect(
      actual.filter((path) => path.startsWith("/agent-showcase/ppt/previews/")),
    ).toHaveLength(21);

    let totalBytes = 0;
    for (const url of references) {
      expect(url).toMatch(/^\/agent-showcase\//);
      expect(url).not.toContain("..");
      expect(url).not.toMatch(/data:|base64|bundle\.html/i);
      totalBytes += statSync(join(publicRoot, url)).size;
    }
    expect(totalBytes).toBeLessThanOrEqual(20_000_000);

    for (const item of agent("ppt-master").cases) {
      if (item.kind !== "deck") throw new Error("Expected deck cases");
      expect(item.slides).toHaveLength(7);
      expect(item.pageCount).toBe(7);
      expect(statSync(join(publicRoot, item.pptx)).size).toBe(item.pptxBytes);
    }
  });

  it("gives every showcase agent a dedicated localized composer placeholder", () => {
    const expected = {
      "course-video": {
        zh: "粘贴讲稿，或输入课程主题；也可以上传 docx、pdf、md 文稿",
        en: "Paste a script or topic; upload DOCX, PDF, or MD.",
      },
      "ppt-master": {
        zh: "输入 PPT 主题，或上传文档、现有 PPT",
        en: "Enter a topic, or upload a document or existing PPT.",
      },
      "podcast-studio": {
        zh: "输入主题、粘贴公开网页链接，或上传文档",
        en: "Enter a topic, paste a public URL, or upload a document.",
      },
      "creative-studio": {
        zh: "输入课程主题、核心知识点和想要的风格",
        en: "Enter a topic, key points, and desired style.",
      },
    } as const;

    for (const id of agentIds) {
      const placeholder = agent(id).inputPlaceholder;
      expect(placeholder, id).toEqual(expected[id]);
      // Agent-specific copy must not regress to the global composer sentence.
      expect(placeholder.zh, id).not.toContain("今天我能为你做些什么");
      expect(placeholder.en, id).not.toContain("How can I assist you today");
    }
  });

  it("ships hero art with a lossy alpha channel (corner alpha covered by E2E)", () => {
    // Node has no WebP decoder; the four-corner alpha-0 / no-white-card checks
    // run in the browser (tests/e2e/agent-new-chat-guide.spec.ts). Here we only
    // guard the binary contract: every hero must be an alpha WebP, not an
    // opaque rectangle re-exported as .webp.
    for (const id of agentIds) {
      const path = join(publicRoot, agent(id).character);
      const bytes = readFileSync(path);
      expect(bytes.subarray(0, 4).toString(), id).toBe("RIFF");
      expect(bytes.subarray(8, 12).toString(), id).toBe("WEBP");
      expect(bytes.includes(Buffer.from("VP8X")), id).toBe(true);
      expect(bytes.includes(Buffer.from("ALPH")), id).toBe(true);
      expect(statSync(path).size, id).toBeLessThanOrEqual(150_000);
    }
  });

  it("keeps the course-video review step logic explicit", () => {
    const step = agent("course-video").steps[1]!;
    expect(step.description.zh).toContain("若你提供讲稿");
    expect(step.description.zh).toContain("若只给主题");
    expect(step.description.zh).toContain("不改写不扩写");
    expect(step.description.en).toContain("Supplied scripts stay verbatim");
    expect(step.description.en).toContain("topic-only briefs");
  });

  it("preserves the agent workflow markers and confirmation boundaries", () => {
    for (const item of agent("course-video").cases) {
      expect(item.prompt).toContain("auto-video-flow");
      expect(item.prompt).toMatch(/确认/);
    }
    expect(agent("course-video").cases[0]?.prompt).toContain("严格依据我上传");
    expect(agent("course-video").cases[1]?.prompt).toContain("严格依据我上传");
    expect(agent("course-video").cases[2]?.prompt).toContain("来源");

    const deckPrompts = agent("ppt-master").cases.map((item) => item.prompt);
    expect(deckPrompts[0]).toContain("Quick");
    expect(deckPrompts[0]).toContain("Stage 1/2");
    expect(deckPrompts[1]).toContain("Default");
    expect(deckPrompts[1]).toMatch(/先.*确认.*结构.*方案/);
    expect(deckPrompts[2]).toContain("Edit Native");
    expect(deckPrompts[2]).toMatch(/保留原.*设计/);
    expect(deckPrompts[2]).toMatch(/未编辑.*不重建|未修改的对象保持原样/);

    expect(
      agent("podcast-studio").cases.map((item) => item.prompt.split("\n")[0]),
    ).toEqual([
      "[模式: read_original]",
      "[模式: news_single]",
      "[模式: interview_two]",
      "[模式: debate_two]",
    ]);

    for (const item of agent("creative-studio").cases) {
      if (item.kind !== "visual") throw new Error("Expected visual cases");
      expect(item.prompt.split("\n")[0]).toBe(
        item.category === "diagram" ? "[模式: 结构图]" : "[模式: 视觉创作]",
      );
    }
  });

  it("turns every case prompt into a reusable template without case facts", () => {
    const sourceLabels = agentIds.flatMap((id) =>
      agent(id).cases.flatMap((item) =>
        item.kind === "video" || item.kind === "podcast"
          ? (item.sources ?? []).flatMap((source) => [
              source.label.zh,
              source.label.en,
            ])
          : [],
      ),
    );
    const sourceNames = ["InfoQ", "经济参考报", "KubeCon", "新华社", "Mozilla"];

    for (const id of agentIds) {
      for (const item of agent(id).cases) {
        const where = `${id}/${item.id}`;
        for (const title of [item.title.zh, item.title.en]) {
          expect(item.prompt, `${where} leaks its own title`).not.toContain(
            title,
          );
        }
        expect(
          item.prompt,
          `${where} keeps sample durations and page counts out`,
        ).not.toMatch(/\d+(?:\.\d+)?\s*(?:秒|页|seconds?|slides?|pages?)/i);
        expect(item.prompt, `${where} has no URL`).not.toMatch(/https?:\/\//);
        expect(item.prompt, `${where} has no case media path`).not.toContain(
          "/agent-showcase/",
        );
        for (const label of [...sourceLabels, ...sourceNames]) {
          expect(item.prompt, `${where} leaks source ${label}`).not.toContain(
            label,
          );
        }
        if (item.kind === "deck") {
          expect(item.prompt, `${where} leaks the case brand`).not.toContain(
            "360",
          );
        }
        if (item.kind === "video") {
          // Sample lengths belong to the case, never to the new task.
          expect(item.prompt, `${where} leaks a sample length`).not.toMatch(
            /\b(?:32|29|39)\b/,
          );
        }
        if (item.kind === "podcast") {
          // The display transcript stays a case fact: none of its lines and
          // none of its figures or years reach the new task.
          expect(item.prompt, `${where} leaks the case year`).not.toContain(
            "2026",
          );
          for (const turn of item.transcript) {
            expect(
              item.prompt,
              `${where} leaks a transcript line`,
            ).not.toContain(turn.text.slice(0, 8));
          }
        }
      }
    }
  });

  it("gives every case a fill-only action label within the card budget", () => {
    for (const id of agentIds) {
      for (const item of agent(id).cases) {
        expect(item.actionLabel, `${id}/${item.id}`).toBeDefined();
        expect(item.actionLabel?.zh.trim(), `${id}/${item.id}`).not.toBe("");
        expect(item.actionLabel?.en.trim(), `${id}/${item.id}`).not.toBe("");
        if (item.kind === "video" || item.kind === "deck") {
          // Video and deck cards share their bottom row with other controls (a
          // truncatable meta column, or two actions): at 1280 a label wider
          // than the generic five-character one truncates the meta line or
          // wraps the deck action row, so it has a hard length budget.
          expect(item.actionLabel?.zh.length).toBeLessThanOrEqual(5);
          expect(item.actionLabel?.en.length).toBeLessThanOrEqual(15);
        }
      }
    }
  });

  it("uses request placeholders the composer can recognize", () => {
    // The composer selects the first fill-in placeholder of the inserted
    // segment: a 【请…】 / 【可选填写…】 token. Section headers share the
    // brackets, so every token must be either a known header or a placeholder.
    const headers = [
      "【我的内容】",
      "【风格与版式】",
      "【必需材料】",
      "【视觉与配音】",
      "【需填写】",
      "【可选】",
      "【原文来源】",
      "【固定音色】",
    ];
    const placeholder = /^【(?:请[^【】]*|可选填写[^【】]*)】$/;

    for (const id of agentIds) {
      for (const item of agent(id).cases) {
        const where = `${id}/${item.id}`;
        // A fixed-script video takes an uploaded script, so it may have no
        // text placeholder at all; every token it does use must be known.
        const tokens = item.prompt.match(/【[^【】\n]*】/g) ?? [];
        for (const token of tokens) {
          expect(
            headers.includes(token) || placeholder.test(token),
            `${where} uses an unknown token ${token}`,
          ).toBe(true);
        }
      }
    }
  });

  it("keeps mode lines off decks and videos", () => {
    for (const item of [
      ...agent("course-video").cases,
      ...agent("ppt-master").cases,
    ]) {
      expect(item.prompt.split("\n")[0]).not.toMatch(/^\[模式/);
    }
  });

  it("uses HTTPS for every external source", () => {
    const sources = agentIds.flatMap((id) =>
      agent(id).cases.flatMap((item) => {
        if (item.kind === "video" || item.kind === "podcast") {
          return item.sources ?? [];
        }
        return [];
      }),
    );
    expect(sources.length).toBeGreaterThan(0);
    for (const source of sources) {
      expect(new URL(source.url).protocol, source.url).toBe("https:");
    }
  });
});
