export type ShowcaseAgentId =
  | "course-video"
  | "ppt-master"
  | "podcast-studio"
  | "creative-studio";

export type LocalizedText = { zh: string; en: string };

export type ShowcaseSource = { label: LocalizedText; url: string };

type ShowcaseCaseBase = {
  id: string;
  title: LocalizedText;
  tag: LocalizedText;
  description: LocalizedText;
  /** Fill-only CTA label shared by the card and the preview dialog. */
  actionLabel?: LocalizedText;
  /** One-line "kept / fill in" note shown above the dialog CTA. */
  recipeNote?: LocalizedText;
  /** Reusable template; never the case's own brief. */
  prompt: string;
};

export type VideoCase = ShowcaseCaseBase & {
  kind: "video";
  poster: string;
  video: string;
  durationSeconds: number;
  voice: LocalizedText;
  styleLabel: LocalizedText;
  sources?: ShowcaseSource[];
};

export type VisualCase = ShowcaseCaseBase & {
  kind: "visual";
  category: "poster" | "card" | "comic" | "infographic" | "diagram";
  styleLabel: LocalizedText;
  preview: string;
  original: string;
  downloadName: string;
  vector?: string;
  width: number;
  height: number;
};

export type DeckCase = ShowcaseCaseBase & {
  kind: "deck";
  slides: string[];
  pptx: string;
  pptxBytes: number;
  pageCount: number;
  styleLabel: LocalizedText;
  features: LocalizedText[];
};

export type PodcastCase = ShowcaseCaseBase & {
  kind: "podcast";
  audio: string;
  durationSeconds: number;
  voices: LocalizedText[];
  source: LocalizedText;
  sources: ShowcaseSource[];
  transcript: { speaker: LocalizedText; text: string }[];
};

export type ShowcaseCase = VideoCase | VisualCase | DeckCase | PodcastCase;

export type ShowcaseStep = {
  title: LocalizedText;
  description: LocalizedText;
};

export type ShowcaseAgent = {
  id: ShowcaseAgentId;
  inputPlaceholder: LocalizedText;
  heroLine: { zh: [string, string]; en: [string, string] };
  character: string;
  howTitle: LocalizedText;
  howDescription: LocalizedText;
  badge: LocalizedText;
  note: LocalizedText;
  steps: ShowcaseStep[];
  cases: ShowcaseCase[];
};

const text = (zh: string, en: string): LocalizedText => ({ zh, en });
const asset = (relativePath: string) => `/agent-showcase/${relativePath}`;
const slides = (deck: "quick" | "standard" | "template") =>
  Array.from({ length: 7 }, (_, index) =>
    asset(`ppt/previews/${deck}/slide-${index + 1}.webp`),
  );

const infoQ: ShowcaseSource = {
  label: text("InfoQ 2026 趋势报告", "InfoQ 2026 trends report"),
  url: "https://www.infoq.cn/article/7Pecd1CshYIMvCTeBhrm",
};
const economicReference: ShowcaseSource = {
  label: text(
    "经济参考报 / 新华网 2026-11-13",
    "Economic Information Daily / Xinhua, Nov 13, 2026",
  ),
  url: "https://www.news.cn/liangzi/20261113/2388cc955dad4f059806ad95f3564694/c.html",
};
const kubeCon: ShowcaseSource = {
  label: text("KubeCon 2026 观察", "KubeCon 2026 observations"),
  url: "https://stacklok.com/blog/kubecon-2026-wrapped-7-things-we-learned/",
};
const yunzhou = text("云舟2.0（男声）", "Yunzhou 2.0 (male)");
const vivi = text("Vivi2.0（女声）", "Vivi 2.0 (female)");
const liufei = text("刘飞2.0（男声）", "Liu Fei 2.0 (male)");
const createInThisStyle = text("按此风格创作", "Create in this style");

// Visual templates (style-workflow plan appendix A.1): the user's content is
// left as placeholders, only the style, layout and output format are kept.
const visualFields = {
  poster: [
    "课程主题：【请填写】",
    "主标题：【请填写】",
    "副标题与关键信息：【请填写】",
  ],
  knowledge: [
    "主题：【请填写】",
    "核心知识点：【请填写】",
    "必须准确呈现的文字：【请填写】",
  ],
  comic: [
    "主题：【请填写】",
    "角色与四格情节：【请填写】",
    "必须保留的对白：【请填写】",
  ],
  diagram: [
    "主题：【请填写】",
    "节点与层级：【请填写】",
    "连接关系：【请填写】",
  ],
} as const;

const visualTemplate = ({
  type,
  fields,
  style,
  diagram = false,
}: {
  type: string;
  fields: readonly string[];
  style: string;
  diagram?: boolean;
}) =>
  [
    `[模式: ${diagram ? "结构图" : "视觉创作"}]`,
    `请按参考风格制作一张${type}。`,
    "",
    "【我的内容】",
    ...fields,
    "",
    "【风格与版式】",
    style,
    "",
    "仅参考视觉风格，不复制案例标题、知识点和文案。如果我的内容仍为空，请先向我索取，不要使用案例内容补齐。",
  ].join("\n");

// Live smoke (2026-11-24): the creative-studio agent treats "需求确认" as its
// own check — a complete brief goes straight to the image, an incomplete one is
// asked about first. Say so instead of promising a confirmation that may not come.
const recipeFooter = "内容齐全时 agent 会直接出图；缺内容会先向你索取。";
const recipeFooterEn =
  "With complete content the agent generates directly; it asks first when something is missing.";

// Video templates (plan appendix A.2): the user's own script or topic; the case
// lends only its visual style and default voice.
const videoSkill = "整个制作过程请使用 /skill auto-video-flow。";

const fixedScriptTemplate = (visual: LocalizedText, voice: LocalizedText) =>
  `请严格依据我上传的完整口播稿制作 16:9 视频课，讲稿按原文使用，不改写、不扩写，也不要联网补充。

【必需材料】
完整口播稿：请上传 docx、pdf、md 或 txt 文件；未提供完整讲稿前请勿开始制作。

【视觉与配音】
视觉：${visual.zh}（可修改）
默认音色：${voice.zh}（可修改）
字幕：保留
背景音乐：不添加
画面：不生成真人角色或剧情镜头，不虚构真实素材。

请先提供完整制作方案，待我确认后再开始执行。${videoSkill}`;

const researchTemplate = (visual: LocalizedText, voice: LocalizedText) =>
  `请围绕我的主题做一次深度联网调研，先展示调研来源、完整口播稿和分镜，待我确认后再制作 16:9 视频课。

【需填写】
主题：【请填写】
受众：【请填写】
目标时长：【请填写】

【可选】
来源偏好：【可选填写】

【视觉与配音】
视觉：${visual.zh}（可修改）
默认音色：${voice.zh}（可修改）
字幕：保留
背景音乐：不添加
要求：事实必须能对应来源，不补写来源中没有的数据，不虚构真实素材，不生成真人角色或剧情镜头。

${videoSkill}`;

const videoRecipeFooter = "发送后先确认制作方案再执行。";
const videoRecipeFooterEn =
  "The agent confirms the production plan before executing.";
const darkTech = text(
  "深色科技 · 网格与概率条",
  "Dark tech · Grids and probability bars",
);
const warmPaper = text("暖白纸感 · 杂志排版", "Warm paper · Editorial layout");
const deepGreen = text("深绿调研 · 来源卡片", "Deep green · Source cards");

// Deck templates (plan appendix A.3). No leading mode line: ppt-master's mode
// is a conversation mode, so the trigger sentences come from the real prompts.
const deckContentFields = [
  "主题：【请填写】",
  "资料：【请填写要点，或注明你手动添加的资料文件】",
  "受众：【请填写，如零基础学员】",
  "目标：【请填写学完能做到什么】",
  "页数：【请填写建议页数】",
];

const quickDeckTemplate = `走 Quick 流程，跳过 Stage 1/2 策略与方案确认，直接出稿并完成质量检查，交付原生可编辑 PPTX。

【我的内容】
${deckContentFields.join("\n")}

视觉：参考轻量科技、暖纸与钴蓝配色，按我的内容重新设计。

要求：逐页可编辑，不使用整页截图代替原生页面；不要沿用案例主题或文案。`;

const defaultDeckTemplate = `按 Default 标准流程制作一份原生可编辑 PPTX。先与我确认课程结构和设计方案，再逐页生成。

【我的内容】
${deckContentFields.join("\n")}

视觉：参考深色杂志风、琥珀与青配色，按我的内容重新设计。

要求：图表与流程页使用原生可编辑对象，不使用整页截图代替；不要沿用案例主题或文案。`;

const editNativeDeckTemplate = `请走 Edit Native 原生编辑，改造我上传的现有 PPT：先与我确认逐页编辑计划（输出页 → 源页 → 操作），再执行原生编辑。

【必需材料】
源 PPTX：请上传你有权编辑的 .pptx
新内容资料：【请填写要点，或注明你手动添加的资料文件】

【我的内容】
新主题：【请填写】
受众：【请填写】
目标：【请填写】
页数：按新内容决定，不限定页数
保留范围：【请填写需保留的封面、页眉页脚、版式或对象】
改写范围：【请填写需改写或新增的页面与内容】

要求：仅处理我有权编辑的文件；按“保留范围”保留原设计；未编辑对象不重建，未修改的对象保持原样；新增或改写页面沿用源 PPT 的视觉语言，交付可继续改字、换图、调整版式的 PPTX，不用整页截图替代。`;

const deckRecipeFooter = "案例成品不会自动作为你的模板。";
const deckRecipeFooterEn =
  "The finished case deck is never used as your template.";

// Podcast templates (plan appendix A.4). The first line is the skill's routing
// line and stays verbatim; voices are fixed per mode by the skill.
const readOriginalTemplate = `[模式: read_original]
请将我提供的文档或安全公开链接中的原文朗读为播客，不增写、不改写、不总结。

【原文来源】
文档或安全公开链接：【请上传文档或粘贴安全公开链接】

【固定音色】
云舟2.0（男声）

仅朗读我有权使用的原文；如未提供文档或链接，请先向我索取。输出 MP3。`;

const newsSingleTemplate = `[模式: news_single]
请围绕我提供的主题或资料制作一期单人播报，先展示完整口播稿、来源和固定音色，确认后再合成 MP3。

【我的内容】
主题：【请填写】
资料或安全公开链接：【请填写，可留空】

【固定音色】
云舟2.0（男声）

请根据我的内容重新撰写，仅使用我提供或经核验的公开资料，不借用案例文稿。`;

const interviewTemplate = `[模式: interview_two]
请基于我提供的主题或资料制作一期双人访谈，围绕主题展开多轮问答，先确认完整口播稿、来源和固定音色，再合成 MP3。

【我的内容】
主题：【请填写】
资料或安全公开链接：【请填写，可留空】

【固定音色】
云舟2.0（男声，主持）
Vivi2.0（女声，嘉宾）

请根据我的内容重新撰写，不借用案例文稿。`;

const debateTemplate = `[模式: debate_two]
请围绕我提供的辩题或资料制作一期双人辩论，双方分别陈述立场、回应和总结，先确认完整口播稿、来源和固定音色，再合成 MP3。

【我的内容】
辩题：【请填写】
双方立场：【请填写，可留空】
资料或安全公开链接：【请填写，可留空】

【固定音色】
云舟2.0（男声，一方）
Vivi2.0（女声，另一方）

请根据我的内容重新撰写，不借用案例文稿。`;

const podcastRecipeFooter = "案例文稿与来源不会带入新任务。";
const podcastRecipeFooterEn =
  "The case transcript and sources never enter a new task.";

const showcases: Record<ShowcaseAgentId, ShowcaseAgent> = {
  "course-video": {
    id: "course-video",
    inputPlaceholder: text(
      "粘贴讲稿，或输入课程主题；也可以上传 docx、pdf、md 文稿",
      "Paste a script or topic; upload DOCX, PDF, or MD.",
    ),
    heroLine: {
      zh: ["上传讲稿或给出主题，", "生成一节可播放的讲解视频课。"],
      en: ["Upload a script or share a topic.", "Get a playable video lesson."],
    },
    character: asset("hero/course-video.webp"),
    howTitle: text("如何生成一节视频课", "How to create a video lesson"),
    howDescription: text(
      "先核对讲稿和画面，再配音、渲染、交付 MP4。",
      "Review the script and visuals before narration, rendering, and MP4 delivery.",
    ),
    badge: text("产物已核验", "Verified output"),
    note: text(
      "3 个案例均为真实成片，前两个按上传讲稿模式制作，第三个先联网调研再制作，均不含真人角色或剧情镜头。",
      "Three real videos: two from supplied scripts and one from researched sources, with no actors or fictional scenes.",
    ),
    steps: [
      {
        title: text("提供讲稿或主题", "Provide a script or topic"),
        description: text(
          "上传 docx、pdf、md、txt 文稿，或给出主题，并说明风格、音色和时长。",
          "Upload a document or share a topic, visual style, voice, and target duration.",
        ),
      },
      {
        title: text("核对讲稿与分镜", "Review script and storyboard"),
        description: text(
          "若你提供讲稿，按原文使用、不改写不扩写；若只给主题，agent 先写出口播稿与逐场景设计，交你核对。",
          "Supplied scripts stay verbatim; topic-only briefs get a drafted narration and scene plan for your review.",
        ),
      },
      {
        title: text("渲染并交付 MP4", "Render and deliver MP4"),
        description: text(
          "生成配音、画面和字幕，渲染 16:9 MP4，预览后下载。",
          "Generate narration, visuals, and captions, then preview and download the 16:9 MP4.",
        ),
      },
    ],
    cases: [
      {
        id: "video-hallucination",
        kind: "video",
        title: text(
          "大模型为什么会产生幻觉？",
          "Why do large language models hallucinate?",
        ),
        tag: text("上传讲稿 · 深色科技风", "Supplied script · Dark tech"),
        description: text(
          "从“预测下一个词”讲起，解释幻觉的三个来源，以及检索增强、引用约束和结果核验三种工程手段。",
          "Explain three sources of hallucination and three engineering mitigations, starting from next-token prediction.",
        ),
        actionLabel: text("按风格创作", "Use this style"),
        recipeNote: text(
          "保留：深色科技画面、网格与概率条，云舟音色、字幕、无背景音乐（均可改）。填写：上传你自己的完整口播稿；讲稿按原文使用，不改写。" +
            videoRecipeFooter,
          "Kept: dark tech visuals with grids and probability bars, the Yunzhou voice, captions and no background music (all editable). Fill in: upload your own full script; it is used verbatim without rewriting. " +
            videoRecipeFooterEn,
        ),
        poster: asset("videos/poster-v1.webp"),
        video: asset("videos/v1.mp4"),
        durationSeconds: 32.32,
        voice: yunzhou,
        styleLabel: darkTech,
        prompt: fixedScriptTemplate(darkTech, yunzhou),
      },
      {
        id: "video-feynman",
        kind: "video",
        title: text(
          "费曼学习法：把知识讲给外行听",
          "The Feynman technique: explain it to anyone",
        ),
        tag: text(
          "上传讲稿 · 明亮纸感风",
          "Supplied script · Bright editorial",
        ),
        description: text(
          "四个步骤讲清费曼学习法，用暖白纸感、衬线标题和手绘线条呈现。",
          "Four clear steps with warm paper textures, serif headlines, and hand-drawn lines.",
        ),
        actionLabel: text("按风格创作", "Use this style"),
        recipeNote: text(
          "保留：暖白纸感、杂志排版与手绘线条，Vivi 音色、字幕、无背景音乐（均可改）。填写：上传你自己的完整口播稿；讲稿按原文使用，不改写。" +
            videoRecipeFooter,
          "Kept: warm paper textures, editorial layout and hand-drawn lines, the Vivi voice, captions and no music (all editable). Fill in: upload your own full script; it is used verbatim without rewriting. " +
            videoRecipeFooterEn,
        ),
        poster: asset("videos/poster-v2.webp"),
        video: asset("videos/v2.mp4"),
        durationSeconds: 29.162667,
        voice: vivi,
        styleLabel: warmPaper,
        prompt: fixedScriptTemplate(warmPaper, vivi),
      },
      {
        id: "video-agent-research",
        kind: "video",
        title: text(
          "AI Agent 为什么开始接管完整工作流",
          "Why AI agents are taking over complete workflows",
        ),
        tag: text("深度调研 · 调研纪实风", "Deep research · Documentary"),
        description: text(
          "先读公开来源、整理证据链，再用调研结论组织讲稿和画面；数字都能对应到来源。",
          "Build an evidence chain from public sources, then turn it into narration and visuals with traceable figures.",
        ),
        actionLabel: text("调研后创作", "Research first"),
        recipeNote: text(
          "保留：深绿调研纪实风、来源卡片与证据链，刘飞音色、字幕、无背景音乐（均可改）。填写：新主题、受众与目标时长；agent 先联网调研并展示来源、完整口播稿和分镜，确认后再配音。InfoQ、经济参考报、KubeCon 仅为本案例来源。",
          "Kept: the deep-green documentary look, source cards and an evidence chain, the Liu Fei voice, captions and no music (all editable). Fill in: a new topic, audience and target length; the agent researches online and shows sources, the full script and the storyboard before narration. InfoQ, Economic Information Daily and KubeCon were this case's sources only.",
        ),
        poster: asset("videos/poster-v3.webp"),
        video: asset("videos/v3.mp4"),
        durationSeconds: 39.082667,
        voice: liufei,
        styleLabel: deepGreen,
        sources: [infoQ, economicReference, kubeCon],
        prompt: researchTemplate(deepGreen, liufei),
      },
    ],
  },
  "ppt-master": {
    id: "ppt-master",
    inputPlaceholder: text(
      "输入 PPT 主题，或上传文档、现有 PPT",
      "Enter a topic, or upload a document or existing PPT.",
    ),
    heroLine: {
      zh: ["从主题、文档或现有 PPT，", "做出可继续编辑的课件。"],
      en: [
        "Start with a topic, document, or deck.",
        "Get slides you can keep editing.",
      ],
    },
    character: asset("hero/ppt-master.webp"),
    howTitle: text("如何制作或改造 PPT", "How to create or transform a deck"),
    howDescription: text(
      "按需求走快速出稿、标准制作或现有 PPT 改造，交付原生可编辑 PPTX。",
      "Choose quick creation, the standard workflow, or native editing, and receive an editable PPTX.",
    ),
    badge: text("原生可编辑", "Natively editable"),
    note: text(
      "3 个案例均为真实 PPTX，分别是快速出稿、标准流程和现有 PPT 改造。第三份基于用户授权的 360 模板做原生改造。",
      "Three real PPTX files covering quick creation, the standard workflow, and native transformation of an authorized 360 template.",
    ),
    steps: [
      {
        title: text(
          "提供主题、资料或现有 PPT",
          "Provide a topic, sources, or deck",
        ),
        description: text(
          "输入主题，上传 PDF、DOCX、Markdown，或直接提供现有 PPTX。",
          "Share a topic, upload source documents, or provide an existing PPTX.",
        ),
      },
      {
        title: text("确认方案（按需）", "Confirm the approach when needed"),
        description: text(
          "快速出稿直接生成；标准流程先确认整体方案；现有 PPT 先确认页面计划。",
          "Quick mode starts directly; standard and native-edit workflows confirm the plan first.",
        ),
      },
      {
        title: text("生成或编辑页面", "Generate or edit slides"),
        description: text(
          "生成原生页面或编辑现有对象，并检查文字、图表和图片。",
          "Author native slides or edit existing objects, then review text, charts, and images.",
        ),
      },
      {
        title: text("导出 PPTX", "Export PPTX"),
        description: text(
          "交付可继续改字、换图、调整版式的 .pptx，不是整页截图。",
          "Receive a .pptx with editable text, images, and layouts—not flattened screenshots.",
        ),
      },
    ],
    cases: [
      {
        id: "ppt-quick",
        kind: "deck",
        title: text(
          "AI 提示词工程入门",
          "Introduction to AI prompt engineering",
        ),
        tag: text("快速出稿 · 轻量科技风", "Quick creation · Light tech"),
        description: text(
          "7 页，从为什么需要提示词工程到四要素、改写示例、模板和常见误区；跳过策略确认，直接出稿。",
          "Seven slides covering prompt engineering fundamentals, four elements, rewrites, templates, and pitfalls.",
        ),
        actionLabel: text("按此出稿", "Quick draft"),
        recipeNote: text(
          "保留：Quick 流程（跳过 Stage 1/2）与轻量科技、暖纸钴蓝配色。填写：主题或资料；受众、目标、页数可填，也可由 Agent 判断。" +
            deckRecipeFooter,
          "Kept: the Quick workflow (skipping Stage 1/2) and the light-tech warm-paper cobalt palette. Fill in: a topic or source material; audience, goal and page count are optional—the agent decides otherwise. " +
            deckRecipeFooterEn,
        ),
        prompt: quickDeckTemplate,
        slides: slides("quick"),
        pptx: asset("ppt/quick-prompt-v3.pptx"),
        pptxBytes: 236985,
        pageCount: 7,
        styleLabel: text(
          "轻量科技 · 暖纸钴蓝",
          "Light tech · Warm paper and cobalt",
        ),
        features: [
          text("快速出稿", "Quick creation"),
          text("原生可编辑", "Natively editable"),
          text("含改写示例", "Rewrite examples"),
        ],
      },
      {
        id: "ppt-standard",
        kind: "deck",
        title: text("大模型应用开发实战", "Building LLM applications"),
        tag: text(
          "标准流程 · 深色杂志风",
          "Standard workflow · Dark editorial",
        ),
        description: text(
          "7 页，包含课程地图、三层架构、RAG 流程、Agent 循环和评估框架；先确认方案再逐页设计。",
          "Seven slides with a course map, three-layer architecture, RAG flow, agent loop, and evaluation framework.",
        ),
        actionLabel: text("按此规划", "Plan and build"),
        recipeNote: text(
          "保留：Default 两阶段确认与深色杂志、琥珀青配色。填写：主题或资料、受众基础、学习目标与使用场景；页数可由 Agent 推荐。" +
            deckRecipeFooter,
          "Kept: the two Default confirmations and the dark editorial amber-and-teal palette. Fill in: a topic or source material, audience level, learning goals and setting; the agent can recommend the page count. " +
            deckRecipeFooterEn,
        ),
        prompt: defaultDeckTemplate,
        slides: slides("standard"),
        pptx: asset("ppt/standard-llm-app-v3.pptx"),
        pptxBytes: 437726,
        pageCount: 7,
        styleLabel: text(
          "深色杂志 · 琥珀与青",
          "Dark editorial · Amber and teal",
        ),
        features: [
          text("标准流程", "Standard workflow"),
          text("先确认后生成", "Confirm before generation"),
          text("图表与流程页", "Charts and process slides"),
        ],
      },
      {
        id: "ppt-template",
        kind: "deck",
        title: text("生成式 AI 通识课", "Generative AI essentials"),
        tag: text(
          "现有 PPT 改造 · 360 模板",
          "Native deck transformation · 360 template",
        ),
        description: text(
          "7 页，保留 360 原封面、页眉标识与绿色视觉语言，把内容重排成通识课；未修改的封面与背景对象保持原样。",
          "Seven slides rebuilt as an introductory course while preserving the 360 cover, header mark, and green visual language.",
        ),
        actionLabel: text("按此改造", "Edit my PPT"),
        recipeNote: text(
          "保留：Edit Native 的页面计划、源对象保留与原生改造方式。填写：上传你有权编辑的 .pptx、新内容资料，以及保留 / 删除 / 改造范围。案例基于授权的 360 模板制作，这份成品不会自动作为你的模板。",
          "Kept: the Edit Native page plan, source-object preservation and native editing. Fill in: upload a .pptx you may edit, the new source material, and what to keep, remove or rewrite. The case was built on an authorized 360 template; that deck is never used as your template.",
        ),
        prompt: editNativeDeckTemplate,
        slides: slides("template"),
        pptx: asset("ppt/template-360-ai-v4.pptx"),
        pptxBytes: 467022,
        pageCount: 7,
        styleLabel: text(
          "360 模板 · 原生改造",
          "360 template · Native transformation",
        ),
        features: [
          text("现有 PPT 改造", "Existing deck transformation"),
          text("保留原设计", "Original design preserved"),
          text("原生可编辑", "Natively editable"),
        ],
      },
    ],
  },
  "podcast-studio": {
    id: "podcast-studio",
    inputPlaceholder: text(
      "输入主题、粘贴公开网页链接，或上传文档",
      "Enter a topic, paste a public URL, or upload a document.",
    ),
    heroLine: {
      zh: ["从文档、网页或主题调研，", "生成一期可收听的播客音频。"],
      en: [
        "Start with documents, links, or research.",
        "Get a podcast episode you can play.",
      ],
    },
    character: asset("hero/podcast-studio.webp"),
    howTitle: text("如何生成一期播客", "How to produce a podcast episode"),
    howDescription: text(
      "先确定来源和节目形式，再核对口播稿，最后合成 MP3。",
      "Choose sources and a format, review the script, then synthesize an MP3.",
    ),
    badge: text("MP3 可试听", "Playable MP3"),
    note: text(
      "4 期节目均为真实 MP3，使用豆包 TTS 的男声/女声双音色，无背景音乐、无声音克隆。",
      "Four real MP3 episodes using male and female Doubao TTS voices, without background music or voice cloning.",
    ),
    steps: [
      {
        title: text("提供来源或主题", "Provide sources or a topic"),
        description: text(
          "上传文档、粘贴公开网页链接，或只给主题由 agent 做必要调研。",
          "Upload documents, paste public links, or share a topic for focused research.",
        ),
      },
      {
        title: text(
          "确定形式，核对稿件",
          "Choose a format and review the script",
        ),
        description: text(
          "原文朗读只做预检，其他形式先确认口播稿、来源和音色。",
          "Read-original mode runs preflight checks; other formats confirm script, sources, and voices first.",
        ),
      },
      {
        title: text("合成并下载 MP3", "Synthesize and download MP3"),
        description: text(
          "逐段生成语音并合并为一期节目，可在线试听后下载。",
          "Generate and join narration segments, then listen online or download the episode.",
        ),
      },
    ],
    cases: [
      {
        id: "podcast-context",
        kind: "podcast",
        title: text(
          "上下文工程：比提示词更重要的三件事",
          "Context engineering: three things beyond prompts",
        ),
        tag: text("原文朗读 · 单音色", "Read original · Single voice"),
        description: text(
          "按原文朗读，不增写、不改写、不总结；适合把已有笔记直接变成可听内容。",
          "A verbatim reading without additions, rewrites, or summaries, turning existing notes into audio.",
        ),
        actionLabel: text("套用原文朗读", "Read as written"),
        recipeNote: text(
          "保留：原文朗读形式，不增写、不改写、不总结；云舟单音色。填写：上传你有权使用的文档，或粘贴安全公开链接；未提供时 agent 会先向你索取。" +
            podcastRecipeFooter,
          "Kept: the read-original format with no additions, rewrites or summaries, and the single Yunzhou voice. Fill in: upload a document you may use or paste a safe public link; the agent asks first when none is provided. " +
            podcastRecipeFooterEn,
        ),
        prompt: readOriginalTemplate,
        audio: asset("podcast/ep1.mp3"),
        durationSeconds: 48.816,
        voices: [yunzhou],
        source: text(
          "原创技术笔记（未改写原文）",
          "Original technical note · Read without rewriting",
        ),
        sources: [],
        transcript: [
          { speaker: yunzhou, text: "上下文工程：比提示词更重要的三件事。" },
          {
            speaker: yunzhou,
            text: "提示词决定模型怎么回答，上下文决定模型看到了什么。当任务变长、资料变多，后者的影响往往更大。",
          },
          {
            speaker: yunzhou,
            text: "第一件事，是选择。把与当前任务最相关的资料放进上下文，而不是把所有文档一次性塞进去。",
          },
          {
            speaker: yunzhou,
            text: "第二件事，是组织。把背景、约束、示例和输出格式分开写，让模型知道每一段信息的作用。",
          },
          {
            speaker: yunzhou,
            text: "第三件事，是更新。多轮任务里，及时删掉过时信息，补上新的结果，避免旧上下文干扰判断。",
          },
          {
            speaker: yunzhou,
            text: "上下文工程不是写一句更长的提示词，而是设计一个更清晰的信息环境。",
          },
        ],
      },
      {
        id: "podcast-agent2026",
        kind: "podcast",
        title: text(
          "AI Agent 的 2026：从演示到数字员工",
          "AI agents in 2026: from demos to digital workers",
        ),
        tag: text("单人播报 · 深度调研", "Solo briefing · Deep research"),
        description: text(
          "先读 InfoQ、经济参考报和 KubeCon 观察，再写单人播报稿；事实都能追溯到来源。",
          "A researched solo briefing based on InfoQ, Economic Information Daily, and KubeCon observations.",
        ),
        actionLabel: text("套用单人播报", "Solo briefing"),
        recipeNote: text(
          "保留：单人播报形式与云舟单音色。填写：新主题或资料，受众与关注角度可选；agent 先展示完整口播稿、来源和固定音色，确认后再合成。" +
            podcastRecipeFooter,
          "Kept: the solo briefing format and the single Yunzhou voice. Fill in: a new topic or source material; audience and focus are optional. The agent shows the full script, sources and fixed voices before synthesizing. " +
            podcastRecipeFooterEn,
        ),
        prompt: newsSingleTemplate,
        audio: asset("podcast/ep2.mp3"),
        durationSeconds: 87.792,
        voices: [yunzhou],
        source: text(
          "InfoQ · 经济参考报 · KubeCon 2026",
          "InfoQ · Economic Reference · KubeCon 2026",
        ),
        sources: [infoQ, economicReference, kubeCon],
        transcript: [
          {
            speaker: yunzhou,
            text: "2026 年，AI Agent 的关键词，正在从“能聊天”变成“能干活”。",
          },
          {
            speaker: yunzhou,
            text: "InfoQ 发布的 2026 年 AI 与 ML 趋势报告认为，自主 LLM 智能体已经从概念验证，进入早期采用者阶段；RAG 则已经进入企业采用阶段。",
          },
          {
            speaker: yunzhou,
            text: "企业现场的变化更直接。经济参考报 11 月 13 日的报道提到，中兴通讯南京滨江基地已经有八十多名数字员工跑在业务线上，仅供应链场景每天的词元消耗量就超过九十亿。",
          },
          {
            speaker: yunzhou,
            text: "同一篇报道还提到，从年初的开源智能体 OpenClaw，到豆包工作、千问办公、Kimi Work，智能体产品正在密集上线。",
          },
          {
            speaker: yunzhou,
            text: "在 KubeCon 2026 上，行业讨论的重点也变了：Agent 不再只是演示，而是需要被调度、被观测、被控制权限的正式工作负载。",
          },
          {
            speaker: yunzhou,
            text: "把这些信号放在一起，会发现三个条件正在同时成熟：工具调用让 Agent 能操作真实系统，协议连接让它接得上数据，评测与护栏让它敢被放进流程。",
          },
          {
            speaker: yunzhou,
            text: "但规模化仍然有门槛。数据质量、权限边界、评估方法，都还没有标准答案。",
          },
          {
            speaker: yunzhou,
            text: "所以，2026 年的竞争焦点，不是谁的 Agent 更会聊天，而是谁能把 Agent 变成可衡量、可回滚、可复用的业务能力。",
          },
        ],
      },
      {
        id: "podcast-embodied",
        kind: "podcast",
        title: text(
          "人形机器人进厂：还差哪几步？",
          "Humanoid robots enter factories: what remains?",
        ),
        tag: text(
          "双人访谈 · 工厂案例",
          "Two-person interview · Factory cases",
        ),
        description: text(
          "从南昌平板工厂和浙江服装工厂的真实案例，聊到泛化、节拍和成本。",
          "A two-person interview about real factory deployments, generalization, production pace, and cost.",
        ),
        actionLabel: text("套用双人访谈", "Two-host interview"),
        recipeNote: text(
          "保留：双人访谈形式，云舟主持、Vivi 嘉宾，音色按模式固定。填写：新主题或资料，嘉宾背景与重点问题可选；agent 先确认完整口播稿、来源和固定音色，再合成。" +
            podcastRecipeFooter,
          "Kept: the two-host interview with Yunzhou as host and Vivi as guest; voices are fixed per mode. Fill in: a new topic or source material; guest background and key questions are optional. The agent confirms the full script, sources and fixed voices before synthesizing. " +
            podcastRecipeFooterEn,
        ),
        prompt: interviewTemplate,
        audio: asset("podcast/ep3.mp3"),
        durationSeconds: 86.304,
        voices: [yunzhou, vivi],
        source: text("新华社 · 工厂真实案例", "Xinhua · Real factory cases"),
        sources: [
          {
            label: text(
              "新华社 · 南昌平板工厂",
              "Xinhua · Nanchang display factory",
            ),
            url: "https://english.news.cn/20260414/5b2a807212414d68a59b55911bb54427/c.html",
          },
          {
            label: text("新华社 · 服装工厂", "Xinhua · Garment factory"),
            url: "https://english.news.cn/20260724/7f92e0d48a124a98b66dee32c9759c2d/c.html",
          },
        ],
        transcript: [
          {
            speaker: yunzhou,
            text: "2026 年，人形机器人开始进厂。新华社报道，南昌一家平板工厂有四台人形机器人完成了八小时直播班次，做的是下线前的精密质检。这件事为什么值得关注？",
          },
          {
            speaker: vivi,
            text: "因为它不是实验室演示，而是在真实产线上连续工作了八小时。报道里提到，AgiBot 的 Genie G2 要在狭窄空间里完成识别、抓取和质检。",
          },
          {
            speaker: vivi,
            text: "另一个案例在浙江的服装工厂。机器人在做面料分拣，单一品类和面料下，分拣成功率达到了百分之九十七。",
          },
          {
            speaker: yunzhou,
            text: "百分之九十七听起来很高，但离完全替代还有距离。差在哪？",
          },
          {
            speaker: vivi,
            text: "三个地方。第一是泛化，换一个品类、换一种面料，成功率可能下降。第二是节拍，工厂要求稳定节拍，机器人要和传送带、工人、设备协同。第三是成本，包括停机、备件和安全投入。",
          },
          { speaker: yunzhou, text: "那企业现在应该怎么试？" },
          {
            speaker: vivi,
            text: "从单一工序、单一品类、可以人工兜底的场景开始，先算清楚投资回收期。同时把安全围栏、急停和责任边界设计好。人形只是形态，真正的门槛是稳定性和单位经济性。",
          },
          {
            speaker: yunzhou,
            text: "所以，2026 年的人形机器人进厂，更像是一个可验证的开始，而不是终局。",
          },
          { speaker: vivi, text: "对，先跑通一个工序，再谈复制。" },
        ],
      },
      {
        id: "podcast-open-source",
        kind: "podcast",
        title: text(
          "开源大模型会超过闭源大模型吗？",
          "Will open-source models surpass closed-source models?",
        ),
        tag: text(
          "双人辩论 · 正反回应",
          "Two-person debate · Arguments and rebuttals",
        ),
        description: text(
          "引用 Mozilla 报告和公开榜单，讨论“够用且便宜”与“最前沿且高保障”的两条曲线。",
          "A debate grounded in Mozilla research and public benchmarks about affordability versus frontier capability.",
        ),
        actionLabel: text("套用双人辩论", "Two-side debate"),
        recipeNote: text(
          "保留：双人辩论形式（陈述、回应、总结），云舟与 Vivi 各执一方，音色按模式固定。填写：新辩题或资料，双方立场与评判维度可选；agent 先确认完整口播稿、来源和固定音色，再合成。" +
            podcastRecipeFooter,
          "Kept: the two-side debate with statements, responses and a wrap-up, Yunzhou and Vivi on opposite sides; voices are fixed per mode. Fill in: a new motion or source material; both positions and judging criteria are optional. The agent confirms the full script, sources and fixed voices before synthesizing. " +
            podcastRecipeFooterEn,
        ),
        prompt: debateTemplate,
        audio: asset("podcast/ep4.mp3"),
        durationSeconds: 114.24,
        voices: [yunzhou, vivi],
        source: text(
          "Mozilla 报告 · 公开基准观察",
          "Mozilla research · Public benchmark observations",
        ),
        sources: [
          {
            label: text("Mozilla 报告解读", "Mozilla report analysis"),
            url: "https://unwire.hk/2026/09/18/china-open-source-ai-models-narrow-gap-us-mozilla-report/column/",
          },
          {
            label: text("Mozilla 报告原文（PDF）", "Mozilla report (PDF)"),
            url: "https://stateofopensource.ai/state-of-open-source-ai-2026.pdf",
          },
        ],
        transcript: [
          {
            speaker: yunzhou,
            text: "2026 年，开源和闭源的差距还在缩小。Mozilla 的《State of Open Source AI》报告估算，顶尖开源模型落后闭源前沿大约四点四个月，使用成本只有对方的百分之三十到六十。今天辩题：开源大模型会超过闭源大模型吗？我方认为，会在大多数企业场景里超过。",
          },
          {
            speaker: vivi,
            text: "我方认为不会全面超过。第一，前沿能力仍然由闭源模型定义，训练算力、数据和强化学习资源的差距还在。第二，开源模型的追赶依赖社区和蒸馏，存在时间差。第三，企业买单的是稳定性、合规和支持，开源不等于免费。",
          },
          {
            speaker: yunzhou,
            text: "但“超过”不一定是全面超越，而是在大多数企业场景里够用且更便宜。Mozilla 报告提到，开源模型的能力差距已经缩到几个百分点，成本却低得多。",
          },
          {
            speaker: yunzhou,
            text: "在部分智能体基准上，开源模型已经接近甚至个别任务反超；但不同榜单的口径差异很大，不能用一个分数下结论。采购决策看的是够用、便宜、可控。",
          },
          {
            speaker: vivi,
            text: "成本优势我同意，但“超过”要看上限。闭源可以持续投入更大的训练集群，开源受限于权重发布节奏。而且，榜单分数不等于生产可靠性，长任务、工具调用、安全对齐都要重新评估。",
          },
          {
            speaker: yunzhou,
            text: "所以更准确的说法是：开源会在“够用加便宜”的中间市场超过闭源，闭源守住“最前沿加高保障”的上限。两条曲线会长期并存。",
          },
          {
            speaker: vivi,
            text: "我接受这个修正。开源会缩小差距，甚至在部分场景领先，但不会全面超过。",
          },
          {
            speaker: yunzhou,
            text: "结论：不是谁取代谁，而是企业按场景选择。开源赢在成本和可控，闭源赢在前沿和保障。",
          },
        ],
      },
    ],
  },
  "creative-studio": {
    id: "creative-studio",
    inputPlaceholder: text(
      "输入课程主题、核心知识点和想要的风格",
      "Enter a topic, key points, and desired style.",
    ),
    heroLine: {
      zh: ["把课程内容做成可用于", "教学与传播的视觉作品。"],
      en: [
        "Turn course content into visuals",
        "made for teaching and sharing.",
      ],
    },
    character: asset("hero/creative-studio.webp"),
    howTitle: text("如何生成视觉作品", "How to create visual assets"),
    howDescription: text(
      "先理解用途和内容，再决定生成或排版方式，最后审校导出。",
      "Understand the purpose and content, choose generation or layout, then review and export.",
    ),
    badge: text("产物已核验", "Verified output"),
    note: text(
      "10 个案例覆盖 4 类作品：课程海报与知识漫画各 2 种风格，知识图解 4 种，结构图 2 种；海报、知识图解与结构图按真实成品类型分别排版，全部使用冷色系（深蓝/石墨/靛蓝/青绿/珊瑚），不用奶油纸感；漫画为生图产物。",
      "Ten designs across four categories: two poster and two comic styles, four knowledge graphics and two diagrams, laid out by their real artifact type (poster, graphic, diagram) in a cool palette of deep blue, graphite, indigo, teal, and coral—no cream grounds; the comics are generated art.",
    ),
    steps: [
      {
        title: text("说明用途和内容", "Explain the purpose and content"),
        description: text(
          "告诉 agent 要做什么、用在哪里、核心信息是什么。",
          "Share what you need, where it will be used, and the key information.",
        ),
      },
      {
        title: text("确认信息与风格", "Confirm content and style"),
        description: text(
          "提炼信息层级，确认尺寸、风格和是否批量；精确文字先整理文案。",
          "Set the information hierarchy, dimensions, style, and quantity; finalize exact copy first.",
        ),
      },
      {
        title: text("生成、审校并导出", "Generate, review, and export"),
        description: text(
          "采用精确排版、生图或结构图方式，检查文字和版式后导出。",
          "Use precise layout, image generation, or SVG, then review text and composition before export.",
        ),
      },
    ],
    cases: [
      {
        id: "visual-poster-agent",
        kind: "visual",
        category: "poster",
        title: text("AI Agent 实战训练营", "AI Agent bootcamp"),
        tag: text("课程海报 · 深空科技风", "Course poster · Deep-space tech"),
        description: text(
          "神经网络球体作为主视觉，适合课程封面和社群传播。",
          "A luminous neural-network sphere for course covers and community promotion.",
        ),
        actionLabel: createInThisStyle,
        recipeNote: text(
          "保留：深空科技风、9:16 竖版与细线框排版。填写：课程主题、主标题、副标题与关键信息；主视觉按你的主题重新设计。" +
            recipeFooter,
          "Kept: the deep-space tech look, 9:16 portrait and thin-frame layout. Fill in: course topic, headline, subtitle and key details; the key visual is redesigned for your topic. " +
            recipeFooterEn,
        ),
        styleLabel: text("深空科技风", "Deep-space tech"),
        prompt: visualTemplate({
          type: "课程海报",
          fields: visualFields.poster,
          style:
            "深空科技风，深蓝黑背景、青蓝光线、中文大标题与细线框，9:16 竖版；用 trainerai-imagine 生图，不走 course-poster 固定模板；主视觉根据课程主题重新设计",
        }),
        preview: asset("visual/previews/poster-agent-dark.webp"),
        original: asset("visual/originals/poster-agent-dark-v2.jpg"),
        downloadName: "visual-poster-agent.jpg",
        width: 1152,
        height: 2048,
      },
      {
        id: "visual-poster-llm",
        kind: "visual",
        category: "poster",
        title: text("大模型应用开发", "LLM application development"),
        tag: text("课程海报 · 深蓝瑞士风", "Course poster · Deep-blue Swiss"),
        description: text(
          "深蓝底、荧光绿与朱红的几何色块，以及克制的瑞士网格排版。",
          "Deep-blue ground, lime and vermilion blocks, and a restrained Swiss grid.",
        ),
        actionLabel: createInThisStyle,
        recipeNote: text(
          "保留：深蓝底、瑞士网格与荧光绿 / 朱红色块，9:16 竖版。填写：课程主题、主标题、副标题与关键信息；主视觉和文案按你的内容重新设计。" +
            recipeFooter,
          "Kept: deep-blue ground, Swiss grid and lime / vermilion blocks in 9:16. Fill in: course topic, headline, subtitle and key details; the key visual and copy are redesigned for your content. " +
            recipeFooterEn,
        ),
        styleLabel: text("深蓝瑞士风", "Deep-blue Swiss"),
        prompt: visualTemplate({
          type: "课程海报",
          fields: visualFields.poster,
          style:
            "深蓝底、瑞士网格、荧光绿与朱红几何色块，9:16 竖版；用 trainerai-imagine 生图，不走 course-poster 固定模板；主视觉和文案根据课程内容重新设计",
        }),
        preview: asset("visual/previews/poster-llm-deep-blue.webp"),
        original: asset("visual/originals/poster-llm-deep-blue.jpg"),
        downloadName: "visual-poster-llm.jpg",
        width: 1152,
        height: 2048,
      },
      {
        id: "visual-card-rag",
        kind: "visual",
        category: "card",
        title: text("RAG：让模型先查再答", "RAG: retrieve before answering"),
        tag: text(
          "知识图解 · 3D 玻璃三幕",
          "Knowledge graphics · 3D glass triptych",
        ),
        description: text(
          "三块半透明玻璃模块由数据流连接，把检索、增强、生成讲成一条链路。",
          "Three translucent glass modules linked by a data stream, tracing retrieval, augmentation, and generation.",
        ),
        actionLabel: createInThisStyle,
        recipeNote: text(
          "保留：深靛紫底、半透明 3D 玻璃与青绿高光，16:9 三幕横向构图。填写：主题、核心知识点与必须准确呈现的文字；信息结构按你的内容重新组织。" +
            recipeFooter,
          "Kept: deep indigo, translucent 3D glass and teal highlights in a 16:9 three-act layout. Fill in: topic, key points and text that must be exact; the information structure is reorganized for your content. " +
            recipeFooterEn,
        ),
        styleLabel: text("3D 玻璃叙事", "3D glass story"),
        prompt: visualTemplate({
          type: "知识图解",
          fields: visualFields.knowledge,
          style:
            "深靛紫底、半透明 3D 玻璃、青绿高光、三幕横向构图与 16:9 画幅；信息结构按用户内容重新组织",
        }),
        preview: asset("visual/previews/card-rag.webp"),
        original: asset("visual/originals/card-rag.jpg"),
        downloadName: "visual-card-rag.jpg",
        width: 2048,
        height: 1152,
      },
      {
        id: "visual-card-prompt",
        kind: "visual",
        category: "card",
        title: text("提示词的四个要素", "The four elements of a prompt"),
        tag: text(
          "知识图解 · Prompt 手稿",
          "Knowledge graphics · Prompt transcript",
        ),
        description: text(
          "深色 prompt 手稿与四段批注，把角色、任务、上下文、格式写成可执行的占位符。",
          "A dark prompt transcript with four margin notes, turning role, task, context, and format into executable placeholders.",
        ),
        actionLabel: createInThisStyle,
        recipeNote: text(
          "保留：深色终端手稿、正文加四段批注与四色标注，16:9。填写：主题、核心知识点与必须准确呈现的文字；手稿内容全部换成你的。" +
            recipeFooter,
          "Kept: the dark terminal transcript with four margin notes in four colors, 16:9. Fill in: topic, key points and text that must be exact; the transcript content is entirely yours. " +
            recipeFooterEn,
        ),
        styleLabel: text("Prompt 手稿", "Prompt transcript"),
        prompt: visualTemplate({
          type: "知识图解",
          fields: visualFields.knowledge,
          style:
            "深色终端手稿、正文与四段批注、蓝 / 珊瑚 / 绿 / 紫标注，16:9 画幅；手稿内容全部由用户替换",
        }),
        preview: asset("visual/previews/card-prompt.webp"),
        original: asset("visual/originals/card-prompt.jpg"),
        downloadName: "visual-card-prompt.jpg",
        width: 2048,
        height: 1152,
      },
      {
        id: "visual-comic-agent",
        kind: "visual",
        category: "comic",
        title: text("什么是 AI Agent", "What is an AI agent?"),
        tag: text("知识漫画 · 日系漫画", "Knowledge comic · Japanese manga"),
        description: text(
          "四格漫画，用学生和机器人解释定目标、调用工具和检查结果。",
          "A student and robot explain goals, tool use, and checking results in four panels.",
        ),
        actionLabel: createInThisStyle,
        recipeNote: text(
          "保留：16:9 四格日系漫画与清晰的阅读顺序。填写：主题、角色与四格情节、必须保留的对白；角色、情节与对白按你的主题重新创作。" +
            recipeFooter,
          "Kept: a 16:9 four-panel manga with a clear reading order. Fill in: topic, characters and four-panel plot, and lines to keep; characters, plot and dialogue are created for your topic. " +
            recipeFooterEn,
        ),
        styleLabel: text("日系漫画", "Japanese manga"),
        prompt: visualTemplate({
          type: "知识漫画",
          fields: visualFields.comic,
          style:
            "16:9 四格日系漫画、清晰阅读顺序和角色一致性；角色、情节与对白均根据用户主题重新创作",
        }),
        preview: asset("visual/previews/comic-agent.webp"),
        original: asset("visual/originals/comic-agent.jpg"),
        downloadName: "visual-comic-agent.jpg",
        width: 2048,
        height: 1152,
      },
      {
        id: "visual-comic-gradient",
        kind: "visual",
        category: "comic",
        title: text("梯度下降：下山的路", "Gradient descent: the way downhill"),
        tag: text("知识漫画 · 靛蓝水墨", "Knowledge comic · Indigo ink"),
        description: text(
          "用靛蓝水墨的四格画面，解释看方向、走一步、重复。",
          "Four indigo-ink panels explain choosing a direction, taking a step, and repeating.",
        ),
        actionLabel: createInThisStyle,
        recipeNote: text(
          "保留：16:9 四格靛蓝水墨、克制留白与连续叙事。填写：主题、角色与四格情节、必须保留的对白；角色与比喻按你的主题重新设计。" +
            recipeFooter,
          "Kept: a 16:9 four-panel indigo ink comic with restrained whitespace and a continuous story. Fill in: topic, characters and four-panel plot, and lines to keep; characters and metaphors are designed for your topic. " +
            recipeFooterEn,
        ),
        styleLabel: text("靛蓝水墨", "Indigo ink"),
        prompt: visualTemplate({
          type: "知识漫画",
          fields: visualFields.comic,
          style:
            "16:9 四格靛蓝水墨、克制留白和连续叙事；角色与比喻根据用户主题重新设计",
        }),
        preview: asset("visual/previews/comic-gradient.webp"),
        original: asset("visual/originals/comic-gradient.jpg"),
        downloadName: "visual-comic-gradient.jpg",
        width: 2048,
        height: 1152,
      },
      {
        id: "visual-infographic-training",
        kind: "visual",
        category: "infographic",
        title: text("大模型训练全流程", "The complete LLM training pipeline"),
        tag: text("知识图解 · 数据管线", "Knowledge graphics · Data pipeline"),
        description: text(
          "五个阶段排成一条横向管线，标注 INPUT / OUTPUT 与连续箭头；从数据清洗到推理部署一张图看完。",
          "Five stages on one horizontal pipeline with INPUT / OUTPUT labels and continuous arrows, from data cleaning to deployment.",
        ),
        actionLabel: createInThisStyle,
        recipeNote: text(
          "保留：冷白数据管线、横向节点与连续箭头，16:9。填写：主题、核心知识点与必须准确呈现的文字；阶段数量与内容由你决定。" +
            recipeFooter,
          "Kept: the cool-white data pipeline with horizontal nodes and continuous arrows, 16:9. Fill in: topic, key points and text that must be exact; you decide the stages and content. " +
            recipeFooterEn,
        ),
        styleLabel: text("数据管线", "Data pipeline"),
        prompt: visualTemplate({
          type: "知识图解",
          fields: visualFields.knowledge,
          style:
            "冷白数据管线、横向节点、连续箭头和输入 / 输出标注，16:9 画幅；阶段数量与内容由用户决定",
        }),
        preview: asset("visual/previews/infographic-training.webp"),
        original: asset("visual/originals/infographic-training.jpg"),
        downloadName: "visual-infographic-training.jpg",
        width: 2048,
        height: 1152,
      },
      {
        id: "visual-infographic-roadmap",
        kind: "visual",
        category: "infographic",
        title: text(
          "企业 AI Agent 落地路线图",
          "Enterprise AI agent adoption roadmap",
        ),
        tag: text(
          "知识图解 · 深绿路线图",
          "Knowledge graphics · Emerald roadmap",
        ),
        description: text(
          "深绿时间线上，一条 S 形路径串起评估、试点、扩展、治理四个阶段与停止条件。",
          "An S-curve on a deep-green timeline links assessment, pilot, scale, and governance, with stop conditions.",
        ),
        actionLabel: createInThisStyle,
        recipeNote: text(
          "保留：深绿时间线、S 形路径与阶段节点，16:9。填写：主题、核心知识点与必须准确呈现的文字；阶段与说明由你重新填写。" +
            recipeFooter,
          "Kept: the deep-green timeline with an S-curve and stage nodes, 16:9. Fill in: topic, key points and text that must be exact; you rewrite the stages and captions. " +
            recipeFooterEn,
        ),
        styleLabel: text("深绿路线图", "Emerald roadmap"),
        prompt: visualTemplate({
          type: "知识图解",
          fields: visualFields.knowledge,
          style:
            "深绿时间线、S 形路径、阶段节点与停止条件，16:9 画幅；阶段与说明由用户重新填写",
        }),
        preview: asset("visual/previews/infographic-roadmap.webp"),
        original: asset("visual/originals/infographic-roadmap.jpg"),
        downloadName: "visual-infographic-roadmap.jpg",
        width: 2048,
        height: 1152,
      },
      {
        id: "visual-diagram-stack",
        kind: "visual",
        category: "diagram",
        title: text("AI Agent 技术栈", "The AI agent technology stack"),
        tag: text(
          "知识结构图 · 石墨结构图",
          "Knowledge diagram · Graphite structure",
        ),
        description: text(
          "按应用层、编排层、模型层、基础设施四层组织，适合课程导论和方案讲解。",
          "Four layers from applications to infrastructure for introductions and solution reviews.",
        ),
        actionLabel: createInThisStyle,
        recipeNote: text(
          "保留：石墨黑底、分层组织与蓝 / 青 / 琥珀 / 黄绿配色，输出可编辑 SVG。填写：主题、节点与层级、连接关系；层级与拓扑由你的内容决定。" +
            recipeFooter,
          "Kept: graphite black, layered groups and the blue / teal / amber / lime palette, delivered as an editable SVG. Fill in: topic, nodes and layers, and connections; the layers and topology follow your content. " +
            recipeFooterEn,
        ),
        styleLabel: text("石墨结构图", "Graphite diagram"),
        prompt: visualTemplate({
          type: "结构图",
          fields: visualFields.diagram,
          diagram: true,
          style:
            "石墨黑底、分层组织、蓝 / 青 / 琥珀 / 黄绿配色，输出可编辑 SVG；层级、节点与关系由用户内容决定",
        }),
        preview: asset("visual/previews/diagram-agent-stack.webp"),
        original: asset("visual/diagrams/diagram-agent-stack.svg"),
        vector: asset("visual/diagrams/diagram-agent-stack.svg"),
        downloadName: "visual-diagram-stack.svg",
        width: 1600,
        height: 1000,
      },
      {
        id: "visual-diagram-rag",
        kind: "visual",
        category: "diagram",
        title: text("RAG 系统架构", "RAG system architecture"),
        tag: text(
          "知识结构图 · 冷白蓝图",
          "Knowledge diagram · Cool blueprint",
        ),
        description: text(
          "文档源、解析切分、向量库、检索器、生成模型与评估层，一张图理清链路。",
          "Sources, parsing, vector store, retrieval, generation, and evaluation in one diagram.",
        ),
        actionLabel: createInThisStyle,
        recipeNote: text(
          "保留：靛蓝 / 珊瑚 / 青绿分组与清晰的连接关系，底色沿用技能默认暗色主题，输出可编辑 SVG。填写：主题、节点与层级、连接关系；节点和拓扑由你的内容决定。" +
            recipeFooter,
          "Kept: indigo / coral / teal groups with clear connections on the skill's default dark theme, delivered as an editable SVG. Fill in: topic, nodes and layers, and connections; the nodes and topology follow your content. " +
            recipeFooterEn,
        ),
        styleLabel: text("冷白蓝图", "Cool blueprint"),
        prompt: visualTemplate({
          type: "结构图",
          fields: visualFields.diagram,
          diagram: true,
          style:
            "靛蓝 / 珊瑚 / 青绿分组和清晰连接关系，底色沿用技能默认暗色主题，输出可编辑 SVG；节点和拓扑由用户内容决定",
        }),
        preview: asset("visual/previews/diagram-rag-architecture.webp"),
        original: asset("visual/diagrams/diagram-rag-architecture.svg"),
        vector: asset("visual/diagrams/diagram-rag-architecture.svg"),
        downloadName: "visual-diagram-rag.svg",
        width: 1600,
        height: 1000,
      },
    ],
  },
};

export function showcaseFor(agentName: string): ShowcaseAgent | null {
  return showcases[agentName as ShowcaseAgentId] ?? null;
}
