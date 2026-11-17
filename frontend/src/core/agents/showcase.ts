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
  label: LocalizedText;
  heroLine: { zh: [string, string]; en: [string, string] };
  character: string;
  howTitle: LocalizedText;
  howDescription: LocalizedText;
  caseLabel: LocalizedText;
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

const showcases: Record<ShowcaseAgentId, ShowcaseAgent> = {
  "course-video": {
    id: "course-video",
    label: text("课程视频", "COURSE VIDEO"),
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
    caseLabel: text("真实成片", "REAL VIDEOS"),
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
          "agent 给出口播稿和逐场景设计；上传讲稿按原文使用，不改写、不扩写。",
          "Review narration and scene designs. Supplied scripts stay verbatim without rewrites or expansion.",
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
        prompt:
          "请严格依据我上传的《大模型为什么会产生幻觉》讲稿制作视频课程。整体画面采用深色科技风，使用男声配音，正常语速，背景为深蓝黑，不添加背景音乐，但需保留字幕。讲稿中的关键表述要与画面同步呈现，不要生成真人角色或剧情镜头，不要虚构真实素材。请先提供完整制作方案，待我确认后再开始执行。整个制作过程请使用 /skill auto-video-flow。",
        poster: asset("videos/poster-v1.webp"),
        video: asset("videos/v1.mp4"),
        durationSeconds: 32.32,
        voice: yunzhou,
        styleLabel: text(
          "深色科技 · 网格与概率条",
          "Dark tech · Grids and probability bars",
        ),
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
        prompt:
          "请严格依据我上传的《费曼学习法》讲稿制作视频课程。整体画面采用明亮纸感、杂志排版风格，使用女声配音，正常语速，背景为暖白色，不添加背景音乐，但需保留字幕。讲稿中的步骤和关键句要与画面同步呈现，不要生成真人角色或剧情镜头，不要虚构真实素材。请先提供完整制作方案，待我确认后再开始执行。整个制作过程请使用 /skill auto-video-flow。",
        poster: asset("videos/poster-v2.webp"),
        video: asset("videos/v2.mp4"),
        durationSeconds: 29.162667,
        voice: vivi,
        styleLabel: text(
          "暖白纸感 · 杂志排版",
          "Warm paper · Editorial layout",
        ),
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
        prompt:
          "请围绕「AI Agent 为什么开始接管完整工作流」做一次深度联网调研，优先读取 InfoQ、经济参考报、KubeCon 等公开来源，整理成 40 秒左右的知识科普视频。先展示调研来源、讲稿和分镜供我确认，再用 auto-video-flow 制作：深绿色调研纪实风，男声配音，保留字幕，不配背景音乐，不生成真人角色或剧情镜头。事实必须能对应到来源，不要补写来源里没有的数据或虚构真实素材。",
        poster: asset("videos/poster-v3.webp"),
        video: asset("videos/v3.mp4"),
        durationSeconds: 39.082667,
        voice: text("刘飞2.0（男声）", "Liu Fei 2.0 (male)"),
        styleLabel: text("深绿调研 · 来源卡片", "Deep green · Source cards"),
        sources: [infoQ, economicReference, kubeCon],
      },
    ],
  },
  "ppt-master": {
    id: "ppt-master",
    label: text("PPT 制作", "PPT CREATION"),
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
    caseLabel: text("真实 PPTX", "REAL PPTX FILES"),
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
        prompt:
          "快速生成一份 7 页课件《AI 提示词工程入门》，面向零基础学员，轻量科技风。走 Quick 流程，跳过 Stage 1/2 策略与方案确认，直接出稿，保持逐页可编辑并完成质量检查。",
        slides: slides("quick"),
        pptx: asset("ppt/quick-prompt-v2.pptx"),
        pptxBytes: 200540,
        pageCount: 7,
        styleLabel: text(
          "轻量科技 · 白底靛蓝",
          "Light tech · White and indigo",
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
        prompt:
          "按 Default 标准流程制作一份 7 页课件《大模型应用开发实战》，深色杂志风。先与我确认课程结构和设计方案，再逐页生成；要有课程地图、三层架构、RAG 流程、Agent 循环和评估页，交付原生可编辑 PPTX。",
        slides: slides("standard"),
        pptx: asset("ppt/standard-llm-app-v2.pptx"),
        pptxBytes: 308973,
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
          "7 页，保留原封面、页眉页脚和整体版式，把内容改写成通识课；未修改对象保持原样。",
          "Seven slides rewritten as an introductory course while preserving the original cover, furniture, layout, and untouched objects.",
        ),
        prompt:
          "我会上传 360 模板《模板.pptx》。请走 Edit Native 原生编辑，保留原封面、页眉页脚和整体版式设计，把内容改写成 7 页《生成式 AI 通识课》；未编辑对象不重建，未修改的对象保持原样，新增或改写页面沿用模板视觉语言，交付可编辑 PPTX。",
        slides: slides("template"),
        pptx: asset("ppt/template-360-ai-v3.pptx"),
        pptxBytes: 465113,
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
    label: text("播客制作", "PODCAST PRODUCTION"),
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
    caseLabel: text("真实节目", "REAL EPISODES"),
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
        prompt:
          "[模式: read_original]\n请将我上传的《上下文工程》笔记按原文朗读，不增写、不改写、不总结；使用男声，输出 MP3。",
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
        prompt:
          "[模式: news_single]\n请围绕「AI Agent 的 2026：从演示到数字员工」调研公开来源，制作一期单人播报；先展示完整口播稿、来源和音色，确认后生成 MP3。",
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
        prompt:
          "[模式: interview_two]\n请基于新华社等公开报道，制作一期双人访谈「人形机器人进厂：还差哪几步？」；男主持、女嘉宾，围绕真实案例和落地门槛展开多轮问答，先确认完整口播稿再合成。",
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
        prompt:
          "[模式: debate_two]\n请围绕「开源大模型会超过闭源大模型吗？」制作一期双人辩论；男女双方分别陈述立场、回应和总结，引用 Mozilla 报告和公开榜单，先确认完整口播稿再合成。",
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
    label: text("视觉设计", "VISUAL DESIGN"),
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
    caseLabel: text("真实作品", "REAL DESIGNS"),
    badge: text("产物已核验", "Verified output"),
    note: text(
      "10 个案例覆盖 5 类作品，每类 2 种风格；海报、知识卡、漫画、信息图由生图模型生成，结构图由 SVG 绘图能力生成。",
      "Ten designs across five categories and two styles each, using image generation for artwork and precise SVG for diagrams.",
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
        styleLabel: text("深空科技风", "Deep-space tech"),
        prompt:
          "[模式: 视觉创作]\n请为「AI Agent 实战训练营」设计一张竖版课程海报。深空科技风，画面主体是发光的神经网络球体，标题、副标题和底部信息要清晰可读；输出 9:16 图片。",
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
        tag: text("课程海报 · 瑞士网格风", "Course poster · Swiss grid"),
        description: text(
          "暖白纸感、橙色几何块和克制的网格排版。",
          "Warm paper, orange geometric blocks, and a restrained grid.",
        ),
        styleLabel: text("瑞士网格风", "Swiss grid"),
        prompt:
          "[模式: 视觉创作]\n请为「大模型应用开发」设计一张竖版课程海报。瑞士网格、暖白纸感、橙色几何色块，标题排版要克制；输出 9:16 图片。",
        preview: asset("visual/previews/poster-llm-light.webp"),
        original: asset("visual/originals/poster-llm-light.jpg"),
        downloadName: "visual-poster-llm.jpg",
        width: 1152,
        height: 2048,
      },
      {
        id: "visual-card-rag",
        kind: "visual",
        category: "card",
        title: text("RAG：让模型先查再答", "RAG: retrieve before answering"),
        tag: text("知识卡片 · 玻璃拟态", "Knowledge card · Glassmorphism"),
        description: text(
          "检索、增强、生成三步，一张卡讲清 RAG 的基本链路。",
          "Explain retrieval, augmentation, and generation in one card.",
        ),
        styleLabel: text("玻璃拟态", "Glassmorphism"),
        prompt:
          "[模式: 视觉创作]\n请把「RAG：让模型先查再答」做成一张 16:9 知识卡片，三步：检索、增强、生成；深色玻璃拟态风格，中文要准确。",
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
          "知识卡片 · 手写笔记风",
          "Knowledge card · Handwritten notes",
        ),
        description: text(
          "角色、任务、上下文、格式，用手写笔记和黄色高亮呈现。",
          "Role, task, context, and format in handwritten notes with yellow highlights.",
        ),
        styleLabel: text("手写笔记风", "Handwritten notes"),
        prompt:
          "[模式: 视觉创作]\n请把「提示词的四个要素：角色、任务、上下文、格式」做成一张 16:9 手写笔记风知识卡片，蓝色笔迹、黄色高亮、红色批注。",
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
        styleLabel: text("日系漫画", "Japanese manga"),
        prompt:
          "[模式: 视觉创作]\n请画一页 16:9 的四格知识漫画，主题是「什么是 AI Agent」，用学生和机器人两个角色，解释定目标、调用工具、检查结果。",
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
        tag: text("知识漫画 · 水墨白描", "Knowledge comic · Ink wash"),
        description: text(
          "用登山者下山的四格画面，解释看方向、走一步、重复。",
          "Four ink-wash panels explain choosing a direction, taking a step, and repeating.",
        ),
        styleLabel: text("水墨白描", "Ink wash"),
        prompt:
          "[模式: 视觉创作]\n请画一页 16:9 的四格水墨知识漫画，用登山者下山解释「梯度下降」：从山顶出发、看哪边更低、向低处走一步、重复到谷底。",
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
        tag: text("信息图 · 等距科技风", "Infographic · Isometric tech"),
        description: text(
          "数据清洗、预训练、监督微调、对齐、推理部署，五个阶段一张图看完。",
          "Five stages from data cleaning through deployment in one infographic.",
        ),
        styleLabel: text("等距科技风", "Isometric tech"),
        prompt:
          "[模式: 视觉创作]\n请把「大模型训练全流程」做成一张 16:9 信息图，五个阶段：数据清洗、预训练、监督微调、对齐、推理部署；不要编造数字指标。",
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
        tag: text("信息图 · 蓝图编辑风", "Infographic · Blueprint editorial"),
        description: text(
          "评估、试点、扩展、治理四个阶段，用浅色蓝图风格讲清落地路径。",
          "Assessment, pilots, scaling, and governance in a light blueprint style.",
        ),
        styleLabel: text("蓝图编辑风", "Blueprint editorial"),
        prompt:
          "[模式: 视觉创作]\n请把「企业 AI Agent 落地路线图」做成一张 16:9 信息图，四个阶段：评估、试点、扩展、治理；浅色蓝图风格，不要编造数字指标。",
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
          "知识结构图 · 暗色结构图",
          "Knowledge diagram · Dark structure",
        ),
        description: text(
          "按应用层、编排层、模型层、基础设施四层组织，适合课程导论和方案讲解。",
          "Four layers from applications to infrastructure for introductions and solution reviews.",
        ),
        styleLabel: text("暗色结构图", "Dark diagram"),
        prompt:
          "[模式: 结构图]\n请生成一张「AI Agent 技术栈」知识结构图，按应用层、编排层、模型层、基础设施四层组织，标注每层的关键能力，输出 SVG。",
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
          "知识结构图 · 浅色蓝图风",
          "Knowledge diagram · Light blueprint",
        ),
        description: text(
          "文档源、解析切分、向量库、检索器、生成模型和评估层，一张图理清 RAG 链路。",
          "Sources, parsing, vector storage, retrieval, generation, and evaluation in one diagram.",
        ),
        styleLabel: text("浅色蓝图风", "Light blueprint"),
        prompt:
          "[模式: 结构图]\n请生成一张「RAG 系统架构」知识结构图，包含文档源、解析与切分、向量库、检索器、生成模型、答案与引用，以及评估与观测层；输出 SVG。",
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
