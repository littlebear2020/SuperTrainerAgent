/**
 * M0 样例文案（示例课程《AI 提示词工程入门》）——从 19 版式组件中移出的
 * 原硬编码内容，按场景数据化。用途：
 * 1. Root.tsx 的 76 个画廊 Composition 的 defaultProps（保持 M0 静帧可复现）；
 * 2. DataDrivenCourse 的 defaultProps（本地冒烟出片）；
 * 3. gateway 侧 remotion_course_props.py 的 layoutProps 形状参照。
 *
 * 富文本标记（renderRichText 消费）：
 * - `**文字**` → 主题主文字色加粗（原 CasePage 内嵌 span）；
 * - `{a}文字{/a}` → 强调色（原 QuotePage 内嵌 accent span）；
 * - `\n` → 换行。
 */

export interface SampleScene {
  layout: string;
  narration: string;
  layoutProps: Record<string, unknown>;
}

/**
 * 画廊模式字幕条文案（M0 版式内 SubtitleBar 的原硬编码文本）。
 * 仅 76 画廊 Composition 消费；数据驱动成片由根部 SubtitleLayer 接管。
 */
export const SAMPLE_SUBTITLES: Record<string, string> = {
  ObjectivesPage: "四个可带走的能力目标，接下来逐一解锁。",
  OutlinePage: "现在进入第二章：提示词的基本结构。",
  ConceptPage: "提示词，就是你写给模型的任务说明书。",
  ImageTextLeft: "先告诉模型「你是谁」，再告诉它「做什么」。",
  ImageTextRight: "把模型当作新同事：先交接背景，再布置任务。",
  VideoClipPage: "把模糊需求改写成结构化提示词，只花了三轮。",
  ComparisonPage: "方法论的差距，就是产出稳定性的差距。",
  ChartPage: "不是模型变强了，是指令变清楚了。",
  ProcessPage: "第三步最关键：用真实案例测试，而不是凭感觉。",
  CodePage: "提示词也是工程资产：进仓库、可 review。",
  CasePage: "提示词的价值，最终要用业务指标说话。",
  SummaryPage: "带着这四个要点，我们进入下一章。",
  QuizPage: "把你的答案打在弹幕上，下一页揭晓。",
  EndingPage: "作业：用四要素改写一条你常用的提示词，下集见。",
};

export const SAMPLE_COURSE = {
  title: "AI 提示词工程入门",
  brandName: "启点学院",
  courseNo: "AI-PROMPT-101",
  fps: 30,
  width: 1920,
  height: 1080,
  scenes: [
    {
      layout: "CoverPage",
      narration: "欢迎来到 AI 实战系列课程，《AI 提示词工程入门》。我是主讲林晓岚。",
      layoutProps: {
        series: "AI 实战系列课程",
        title: "AI 提示词工程入门",
        subtitle: "从零写出高质量 Prompt 的实战课",
        courseNo: "COURSE · AI-PROMPT-101",
        instructor: { initial: "林", name: "主讲 · 林晓岚", title: "前大厂 AI 应用架构师" },
      },
    },
    {
      layout: "ObjectivesPage",
      narration: "学完这门课，你将能够：理解大模型的工作原理，掌握提示词四大核心要素，稳定提升输出质量，并搭建自己的模板库。",
      layoutProps: {
        kicker: "OBJECTIVES · 学习目标",
        title: "学完这门课，你将能够",
        bullets: [
          "理解大语言模型的工作原理与能力边界",
          "掌握提示词的四大核心要素：角色、任务、上下文、约束",
          "学会用角色、示例与约束稳定提升输出质量",
          "搭建一套可复用、可迭代的个人提示词模板库",
        ],
      },
    },
    {
      layout: "OutlinePage",
      narration: "本课程共六章，从认识大模型到搭建个人模板库。现在进入第二章：提示词的基本结构。",
      layoutProps: {
        kicker: "OUTLINE · 课程大纲",
        title: "六章，从入门到实战",
        chapters: [
          "认识大语言模型",
          "提示词的基本结构",
          "角色与上下文设定",
          "进阶技巧：思维链与少样本",
          "调试与效果评估",
          "实战：搭建个人模板库",
        ],
        currentChapter: 1,
      },
    },
    {
      layout: "ChapterDivider",
      narration: "第二章，提示词的基本结构。一个清晰的结构，胜过十次漫无目的的修改。",
      layoutProps: {
        chapterNo: 2,
        chapterTotal: 6,
        title: "提示词的基本结构",
        lead: "一个清晰的结构，胜过十次漫无目的的修改。",
      },
    },
    {
      layout: "ConceptPage",
      narration: "什么是提示词？提示词，就是你写给模型的任务说明书。",
      layoutProps: {
        kicker: "CONCEPT · 概念讲解",
        title: "什么是提示词（Prompt）？",
        lead: "在学着写之前，先统一一个认知——",
        bullets: [
          "提示词是人与模型之间的「任务说明书」",
          "模型基于提示词，预测最可能的文本输出",
          "同一个模型，不同提示词，结果天差地别",
          "写好提示词 = 明确目标 + 提供上下文 + 约束输出",
        ],
      },
    },
    {
      layout: "ImageTextLeft",
      narration: "角色设定的力量。先告诉模型「你是谁」，再告诉它「做什么」。",
      layoutProps: {
        kicker: "图文讲解",
        title: "角色设定的力量",
        bullets: [
          "给模型一个身份：资深翻译、儿科医生、架构师",
          "角色会显著改变用词、深度与视角",
          "角色越具体，输出越稳定",
        ],
      },
    },
    {
      layout: "ImageTextRight",
      narration: "上下文让答案更准。把模型当作新同事：先交接背景，再布置任务。",
      layoutProps: {
        kicker: "图文讲解",
        title: "上下文让答案更准",
        bullets: [
          "背景信息：产品是什么、写给谁、用在哪",
          "参考资料：过往文案、品牌语调、禁用词",
          "上下文越充分，答案越贴合真实需求",
        ],
      },
    },
    {
      layout: "FullVisualPage",
      narration: "少样本示例：给模型打个样。一组好示例，胜过一百句解释。",
      layoutProps: {
        kicker: "FULL VISUAL · 全幅示例",
        title: "少样本示例：给模型打个样",
        subtitle: "一组好示例，胜过一百句解释",
        note: "建议 1920 × 1080 · 少样本示例对比图",
      },
    },
    {
      layout: "VideoClipPage",
      narration: "实操演示：一次真实的提示词调优。把模糊需求改写成结构化提示词，只花了三轮。",
      layoutProps: {
        title: "实操演示：一次真实的提示词调优",
        badge: "视频 · 02:36",
      },
    },
    {
      layout: "ComparisonPage",
      narration: "传统做法对比提示词工程。方法论的差距，就是产出稳定性的差距。",
      layoutProps: {
        kicker: "COMPARISON · 对比",
        title: "传统做法 vs 提示词工程",
        leftLabel: "传统做法",
        rightLabel: "提示词工程",
        left: ["凭感觉写指令，反复试错", "输出质量忽高忽低", "经验留在脑子里，无法复用"],
        right: ["角色＋任务＋上下文＋约束，结构化写法", "输出稳定，质量可预期", "模板沉淀入库，团队可复用"],
      },
    },
    {
      layout: "ChartPage",
      narration: "五轮迭代，准确率翻倍。不是模型变强了，是指令变清楚了。",
      layoutProps: {
        kicker: "DATA · 数据说话",
        title: "五轮迭代，准确率翻倍",
        chart: [
          { label: "初稿", value: 42 },
          { label: "＋角色", value: 58 },
          { label: "＋示例", value: 71 },
          { label: "＋约束", value: 86 },
          { label: "终稿", value: 92 },
        ],
        headline: "42% → 92%",
        conclusion: "同一模型、同一任务：结构化提示词让准确率从 42% 提升到 92%。",
        note: "内部评测集 · n = 120",
      },
    },
    {
      layout: "ProcessPage",
      narration: "提示词迭代四步法。第三步最关键：用真实案例测试，而不是凭感觉。",
      layoutProps: {
        kicker: "PROCESS · 流程",
        title: "提示词迭代四步法",
        steps: [
          { name: "明确目标", desc: "一句话说清要什么" },
          { name: "编写初稿", desc: "按四要素搭结构" },
          { name: "测试迭代", desc: "真实 case 反复打磨" },
          { name: "沉淀模板", desc: "入库复用，持续优化" },
        ],
        currentStep: 2,
      },
    },
    {
      layout: "CodePage",
      narration: "把模板固化成代码。提示词也是工程资产：进仓库、可 review。",
      layoutProps: {
        kicker: "CODE · 代码示例",
        title: "把模板固化成代码",
        fileName: "prompt_demo.py",
        code: [
          "# 角色 + 任务 + 约束：三段式模板",
          'prompt = """',
          "你是一名资深文案。",
          "任务：为新品咖啡写一句 slogan。",
          "约束：15 字以内，突出「提神」，",
          "      风格年轻、有记忆点。",
          '"""',
          "",
          "resp = llm.invoke(prompt)",
          "print(resp)  # → 清醒，从这一杯开始",
        ].join("\n"),
        bullets: [
          "模板字符串承载四要素，结构一目了然",
          "约束写得可衡量：15 字以内、突出「提神」",
          "一处维护模板，处处标准化调用",
        ],
      },
    },
    {
      layout: "QuotePage",
      narration: "提示词不是咒语，而是清晰的思考。",
      layoutProps: {
        quote: "提示词不是咒语，\n而是{a}清晰的思考{/a}。",
        source: "—— 第三章 · 提示词的基本结构",
      },
    },
    {
      layout: "CasePage",
      narration: "客服 FAQ 机器人提示词改造。提示词的价值，最终要用业务指标说话。",
      layoutProps: {
        kicker: "CASE STUDY · 实战案例",
        title: "客服 FAQ 机器人提示词改造",
        cards: [
          {
            label: "背景",
            body: "某电商团队的 FAQ 机器人答非所问，首次解决率长期徘徊在**41%**。",
          },
          {
            label: "做法",
            body: "用四要素重写系统提示词：锁定客服角色、注入产品知识库、**约束回答格式**。",
          },
          {
            label: "结果",
            metric: "+35%",
            body: "首次解决率提升至 76%，人工转接量下降一半。",
          },
        ],
      },
    },
    {
      layout: "SummaryPage",
      narration: "本节小结。带着这四个要点，我们进入下一章。",
      layoutProps: {
        kicker: "SUMMARY · 小结",
        title: "本节小结",
        bullets: [
          "提示词四要素：角色、任务、上下文、约束",
          "结构化写法，优于口语化的随口描述",
          "用真实 case 迭代，而不是凭感觉调整",
          "好提示词要沉淀成模板，反复复用",
        ],
      },
    },
    {
      layout: "QuizPage",
      narration: "先停一下，想一想：下面哪种写法，更可能获得稳定的高质量输出？",
      layoutProps: {
        kicker: "QUIZ · 思考题",
        title: "先停一下，想一想",
        question: "下面哪种写法，更可能获得稳定的高质量输出？",
        options: [
          { key: "A", text: "「随便写点介绍就行」" },
          { key: "B", text: "「写得好一点，拜托了」" },
          { key: "C", text: "「以资深编辑身份，写 100 字产品介绍」" },
          { key: "D", text: "「你看着办，自由发挥」" },
        ],
      },
    },
    {
      layout: "EndingPage",
      narration: "今天，我们拆出了提示词的四要素。下一章，角色与上下文设定实战。作业：用四要素改写一条你常用的提示词，下集见。",
      layoutProps: {
        kicker: "本节回顾",
        recap: "今天，我们拆出了提示词的四要素：角色、任务、上下文、约束。",
        nextLabel: "下集预告",
        nextTitle: "第三章 · 角色与上下文设定实战",
        nextSubtitle: "给模型一个身份，让输出立刻专业三分。",
        cta: "收藏本节模板，动手练一遍 →",
      },
    },
    {
      layout: "EndCard",
      narration: "感谢观看。",
      layoutProps: {
        brandName: "启点学院",
        title: "感谢观看",
        follow: "关注公众号「启点学院」",
        cta: "回复【提示词】领取本节模板库",
        copyright: "© 2026 启点学院 QIDIAN ACADEMY · 保留所有权利",
      },
    },
  ],
} as const;
