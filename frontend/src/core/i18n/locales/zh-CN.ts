import {
  CompassIcon,
  GraduationCapIcon,
  ImageIcon,
  MicroscopeIcon,
  PenLineIcon,
  ShapesIcon,
  SparklesIcon,
  VideoIcon,
} from "lucide-react";

import type { Translations } from "./types";

export const zhCN: Translations = {
  // Locale meta
  locale: {
    localName: "中文",
  },

  // Common
  common: {
    settings: "设置",
    delete: "删除",
    edit: "编辑",
    rename: "重命名",
    openInNewWindow: "在新窗口打开",
    close: "关闭",
    more: "更多",
    loadMore: "加载更多",
    download: "下载",
    thinking: "思考",
    artifacts: "文件",
    public: "公共",
    custom: "自定义",
    legacy: "旧版",
    notAvailableInDemoMode: "在演示模式下不可用",
    loading: "加载中...",
    lastUpdated: "最后更新",
    cancel: "取消",
    save: "保存",
    install: "安装",
    create: "创建",
    import: "导入",
    export: "导出",
    exportAsMarkdown: "导出为 Markdown",
    exportAsJSON: "导出为 JSON",
    exportSuccess: "对话已导出",
    previewNotSupported: "不支持预览",
  },

  // Home
  home: {
    tagline: "你的 AI 超级智能体工作台",
    enterWorkspace: "进入工作台",
    highlights: [
      {
        label: "深度研究",
        description: "多角度系统化研究，综合分析并生成报告",
      },
      { label: "数据分析", description: "上传数据文件，SQL 查询与可视化图表" },
      { label: "创作生成", description: "生成图片、视频、播客、PPT 和网页" },
    ],
  },

  // Welcome
  welcome: {
    greeting: "你好，欢迎回来！",
    description:
      "欢迎使用 🦌 Trainer AI，一个完全开源的超级智能体。通过内置和自定义的 Skills，\nTrainer AI 可以帮你搜索网络、分析数据，还能为你生成幻灯片、\n图片、视频、播客及网页等，几乎可以做任何事情。",

    createYourOwnSkill: "创建你自己的 Agent SKill",
    createYourOwnSkillDescription:
      "创建你的 Agent Skill 来释放 Trainer AI 的潜力。通过自定义技能，Trainer AI\n可以帮你搜索网络、分析数据，还能为你生成幻灯片、\n网页等作品，几乎可以做任何事情。",
  },

  // Clipboard
  clipboard: {
    copyToClipboard: "复制到剪贴板",
    copiedToClipboard: "已复制到剪贴板",
    linkCopied: "链接已复制到剪贴板",
    copyLink: "复制链接",
    failedToCopy: "复制失败",
  },

  artifactEditing: {
    unsaved: "未保存",
    saving: "正在保存...",
    saved: "文件已保存",
    exit: "退出编辑",
    discard: "放弃修改",
    discardChanges: "要放弃对此文件的未保存修改吗？",
    conflict: "开始编辑后文件已发生变化。请放弃草稿并重新加载后再保存。",
    conflictShort: "远端已更新",
    runInProgress: "请等待当前 Agent 运行结束后再保存。",
    saveFailed: "保存文件失败",
  },

  // Input Box
  inputBox: {
    placeholder: "今天我能为你做些什么？",
    createSkillPrompt:
      "我想创建一个新的自定义技能（保存到 skills/custom/）。先问问我希望这个技能能做什么。",
    addAttachments: "添加附件",
    mode: "模式",
    flashMode: "闪速",
    flashModeDescription: "快速且高效的完成任务，但可能不够精准",
    reasoningMode: "思考",
    reasoningModeDescription: "思考后再行动，在时间与准确性之间取得平衡",
    reasoningModeUnavailable: "当前模型不支持深度思考，可切换到豆包/通义等模型",
    proMode: "Pro",
    proModeDescription: "思考、计划再执行，获得更精准的结果，可能需要更多时间",
    ultraMode: "Ultra",
    ultraModeDescription:
      "继承自 Pro 模式，可调用子代理分工协作，适合复杂多步骤任务，能力最强",
    reasoningEffort: "推理深度",
    reasoningEffortMinimal: "最低",
    reasoningEffortMinimalDescription: "检索 + 直接输出",
    reasoningEffortLow: "低",
    reasoningEffortLowDescription: "简单逻辑校验 + 浅层推演",
    reasoningEffortMedium: "中",
    reasoningEffortMediumDescription: "多层逻辑分析 + 基础验证",
    reasoningEffortHigh: "高",
    reasoningEffortHighDescription: "全维度逻辑推演 + 多路径验证 + 反推校验",
    searchModels: "搜索模型...",
    surpriseMe: "小惊喜",
    surpriseMePrompt: "给我一个小惊喜吧",
    polish: "润色",
    polishing: "正在润色...",
    polishEmpty: "润色没有返回内容，已保留原文",
    polishFailed: "润色失败",
    modelsLoadFailed: "模型列表加载失败",
    retry: "重试",
    modelDelistedToast: "之前使用的模型 {model} 已下线，已切换为默认模型",
    suggestions: [
      {
        suggestion: "写作",
        prompt: "撰写一篇关于[主题]的博客文章",
        icon: PenLineIcon,
      },
      {
        suggestion: "研究",
        prompt: "深入浅出的研究一下[主题]，并总结发现。",
        icon: MicroscopeIcon,
      },
      {
        suggestion: "收集",
        prompt: "从[来源]收集数据并创建报告。",
        icon: ShapesIcon,
      },
      {
        suggestion: "学习",
        prompt: "学习关于[主题]并创建教程。",
        icon: GraduationCapIcon,
      },
    ],
    suggestionsCreate: [
      {
        suggestion: "网页",
        prompt: "生成一个关于[主题]的网页",
        icon: CompassIcon,
      },
      {
        suggestion: "图片",
        prompt: "生成一个关于[主题]的图片",
        icon: ImageIcon,
      },
      {
        suggestion: "视频",
        prompt: "生成一个关于[主题]的视频",
        icon: VideoIcon,
      },
      {
        type: "separator",
      },
      {
        suggestion: "技能",
        prompt:
          "我想创建一个新的自定义技能（保存到 skills/custom/）。先问问我希望这个技能能做什么。",
        icon: SparklesIcon,
      },
    ],
  },

  // Sidebar
  sidebar: {
    newChat: "新对话",
    dashboard: "首页",
    chats: "对话",
    recentChats: "最近的对话",
    demoChats: "演示对话",
    agents: "智能体",
    gallery: "灵感库",
    voiceTwin: "配音",
    writer: "写作",
    videoGen: "视频资产",
    assets: "资产中心",
    mySpace: "我的空间",
    myWorks: "我的作品",
    myAssets: "我的资产",
    roleAdmin: "管理员",
    roleUser: "普通用户",
    consoleSelf: "我的用量",
    consoleAdmin: "运营后台",
  },

  // Operations console
  console: {
    titleSelf: "我的用量",
    titleAdmin: "运营后台",
    subtitleSelf: "你自己的任务用量与花费",
    subtitleAdmin: "全平台任务用量与花费",
    totalRuns: "总任务数",
    activeRuns: "进行中",
    totalTokens: "总 token",
    tokensHint: "含子代理用量",
    costUnpriced: "未配置单价",
    overThreshold: "高用量任务",
    totalCostAll: "全链路总成本",
    costBreakdownLlm: "LLM",
    costBreakdownMedia: "媒体",
    unpricedCard: "未计价调用",
    unpricedCardHint: "另有 {n} 次调用无价目，未计入成本",
    unpricedCardZero: "全部调用已计价",
    runsPanelFailed: "失败任务 {n}（含超时）",
    runsPanelThreshold: "高用量任务 {n}（超过关注阈值，仅提示）",
    tabUsageAnalysis: "用量分析",
    tabPricingAdmin: "定价管理",
    dataFreshness: "数据更新于 {n} 前",
    refreshNow: "刷新",
    justNow: "刚刚",
    minutesAgo: "{n} 分钟",
    chartTabUsage: "用量",
    chartTabCost: "花费",
    compositionByModel: "按模型",
    compositionByProvider: "按供应商",
    compositionByType: "按类型",
    compositionScopeNote:
      "按模型/供应商拆分覆盖 2026-08-03 起的任务；按类型为媒体生成调用",
    providerCostNote: "真账优先，无真账按价目估算；带 * 为估算值",
    priceFixBoundaryNote:
      "2026-08-27 前媒体单价存在错位，跨该日的历史趋势不可比（读时重算所致）",
    emptyRunsInWindow: "所选窗口内没有任务记录",
    emptyNoData: "窗口内无数据",
    emptyUnpricedOnly: "有调用但均未计价",
    usagePanelNote: "含对话与配音/写作/视频等 LLM 调用 · 不含媒体生成",
    costPanelNote: "媒体生成调用（生图/生视频/TTS）· 真账优先+估算",
    runsPanelNote: "对话 Agent 的运行记录 · 不含视频/配音/写作任务",
    llmPanelNote: "非对话 LLM 调用（视频/配音/写作/图片生成管线）",
    dailyTrend: "每日 token 趋势",
    windowTotal: "区间合计",
    peakDay: "单日峰值",
    noUsageInWindow:
      "近 {n} 天没有对话或 LLM 调用记录。去「对话」「视频」「写作」发起任务后，这里会显示用量趋势。",
    trendScopeNote:
      "含对话与视频/写作等 LLM 调用；媒体生成费用见下方外部 API 面板",
    legendChat: "对话",
    legendLlm: "LLM 调用",
    byModel: "按模型拆分",
    subagentTokens: "子代理 token",
    runsTitle: "任务列表",
    noRuns: "没有符合条件的任务",
    colCreatedAt: "创建时间",
    colTask: "任务",
    colUser: "用户",
    colStatus: "状态",
    colModel: "模型",
    colTokens: "token",
    colCost: "花费",
    colDuration: "耗时",
    colError: "失败原因",
    stopReasonHint: "任务被中间件提前收尾的原因",
    tokensLabel: "token",
    runsLabel: "任务数",
    costLabel: "花费",
    allUsers: "全部用户",
    allStatuses: "全部状态",
    lastNDays: "近 {n} 天",
    showingRange: "第 {from}–{to} 条",
    previous: "上一页",
    next: "下一页",
    loading: "加载中…",
    errorNoDatabase:
      "运营后台需要 SQL 数据库；请把 config.yaml 的 database.backend 设为 sqlite 或 postgres。",
    errorForbidden: "只有管理员可以查看其他用户的用量。",
    retry: "重试",
    // Pricing panel (A2)
    pricingTitle: "模型定价",
    pricingSubtitle: "修改单价后无需重启，立即影响所有花费数字",
    pricingCurrency: "币种",
    pricingInputPrice: "输入单价（元/百万token）",
    pricingOutputPrice: "输出单价（元/百万token）",
    pricingCacheHitPrice: "缓存读取单价（元/百万token）",
    pricingCacheHitPlaceholder: "留空 = 按输入价计；填 0 = 命中免费",
    pricingSave: "保存定价",
    pricingSaving: "保存中…",
    pricingReplaceWarning: "保存将以此表为准替换全部模型定价",
    pricingSourceDb: "已配置",
    pricingSourceYaml: "默认值",
    pricingSourceUnpriced: "未定价",
    pricingSaveSuccess: "定价已保存",
    pricingSaveError: "保存失败",
    pricingNoDatabase: "定价需要 SQL 数据库后端",
    // LLM usage panel (C2)
    llmUsageTitle: "AI 写作消耗",
    llmUsageSubtitle: "AI 生成脚本、分镜、旁白等文字内容时的用量与花费",
    llmUsageDomain: "功能",
    llmUsageCalls: "调用次数",
    llmUsageFailedCalls: "失败次数",
    llmUsageTokens: "用量（token）",
    llmUsageCost: "花费",
    llmUsageEmpty: "该区间内没有 LLM 调用记录",
    // External calls panel (F2)
    externalCallsTitle: "素材生成调用",
    externalCallsTotal: "总调用次数",
    externalCallsProvider: "服务",
    externalCallsTabAll: "全部",
    externalCallsTabImage: "生图",
    externalCallsTabVideo: "生视频",
    externalCallsTabTts: "配音",
    costChartReal: "真账",
    costChartEstimated: "估算",
    // Media pricing section
    pricingMagnitudeConfirm:
      "检测到已有键单价变化超过 5 倍，确认要按新价保存吗？",
    mediaPricingTitle: "素材单价",
    mediaPricingSubtitle: "配置图片/视频/配音的单价，影响面板成本估算与预算闸",
    mediaPricingProvider: "服务",
    mediaPricingModelId: "模型",
    mediaPricingModelPlaceholder: "默认行（按服务计价）",
    mediaPricingType: "类型",
    mediaPricingUnit: "单位",
    mediaPricingPrice: "单价",
    mediaPricingSourceDefault: "内置默认",
    mediaPricingSourceLabel: "来源",
    // C7 单项 PATCH/DELETE（A20 用户拍板 2026-08-27）
    mediaPricingRowSave: "保存此行",
    mediaPricingRowDelete: "删除",
    mediaPricingDeleteConfirm: (label: string) =>
      `确认删除价目行「${label}」？删除后该键回退内置默认或变为未计价。`,
    mediaPricingModelChangeWarn:
      "模型 ID 已修改：保存将以新键新增一行（旧键行保留，可用「删除」移除）。确定继续？",
    mediaPricingInvalidPrice: "单价需为大于 0 的数字",
    // F2（2026-08-27 评审修复）：任务列表面板头部小字（窗口口径）标注窗口，
    // 与下方全时段表格区分
    runsPanelWindowSuffix: " · 近 {days} 天",
    // F4：构成区「按类型」次数单位
    callsSuffix: "次",
    // DOMAIN_LABELS i18n (P6-6)
    llmUsageDomainVideoGen: "视频脚本",
    llmUsageDomainVimax: "视频引擎",
    llmUsageDomainWriterAssist: "写作助手",
    llmUsageDomainGalleryRecipe: "灵感库配方",
    llmUsageDomainSuggestions: "追问建议",
    llmUsageDomainUnknown: "未知",
    // PROVIDER_LABELS
    providerGptImage2: "GPT 生图（360）",
    providerSeedream: "字节生图",
    providerSeedanceArk: "字节生视频",
    providerSeedance: "360 生视频",
    providerQihu: "360 素材生成",
    providerNanoBanana: "360 生图（NanoBanana）",
    providerHappyhorse: "Happyhorse 视频",
    providerDashscope: "阿里生图",
    providerHailuo: "海螺视频",
    providerDoubaoTts: "豆包配音",
    providerQihuTts: "360 配音",
    providerVolcengine: "火山配音",
    providerAiImage: "AI 生图",
    providerAiVideo: "AI 生视频",
    // Pricing panel — delete confirmation (A4)
    pricingDeleteConfirm: (count: number, names: string) =>
      count === 1
        ? `以下模型的已存定价将被清除：${names}。确定继续？`
        : `以下 ${count} 个模型的已存定价将被清除：${names}。确定继续？`,
  },

  // Agents
  agents: {
    title: "智能体",
    newAgent: "新建智能体",
    emptyTitle: "还没有自定义智能体",
    emptyDescription: "创建你的第一个自定义智能体，设置专属系统提示词。",
    chat: "对话",
    delete: "删除",
    deleteConfirm: "确定要删除该智能体吗？此操作不可撤销。",
    deleteSuccess: "智能体已删除",
    newChat: "新对话",
    createPageTitle: "设计你的智能体",
    createPageSubtitle: "描述你想要的智能体，我来帮你通过对话创建。",
    nameStepTitle: "给新智能体起个名字",
    nameStepHint:
      "只允许字母、数字和连字符，存储时自动转为小写（例如 code-reviewer）",
    nameStepPlaceholder: "例如 code-reviewer",
    nameStepContinue: "继续",
    nameStepInvalidError: "名称无效，只允许字母、数字和连字符",
    nameStepAlreadyExistsError: "已存在同名智能体",
    nameStepNetworkError: "网络请求失败，请检查网络或后端连接",
    nameStepCheckError: "无法验证名称可用性，请稍后重试",
    nameStepBootstrapMessage:
      "新智能体的名称是 {name}，现在开始为它生成 **SOUL**。",
    save: "保存智能体",
    saving: "正在保存智能体...",
    saveRequested:
      "已提交保存请求，Trainer AI 正在根据当前对话生成并保存初版智能体。",
    saveHint:
      "你可以在右上角的菜单里随时保存这个智能体，就算目前还只是初稿也可以。",
    saveCommandMessage:
      "请现在根据我们目前已经讨论的全部内容保存这个自定义智能体。这就是我明确的保存确认。如果仍有少量细节缺失，请根据上下文做出合理假设，生成一份简洁的英文初始 SOUL.md，并直接调用 setup_agent，不要再向我索要额外确认。",
    agentCreatedPendingRefresh:
      "智能体已创建，但 Trainer AI 暂时还无法读取到它。请稍后刷新当前页面。",
    more: "更多操作",
    agentCreated: "智能体已创建！",
    startChatting: "开始对话",
    backToGallery: "返回 Gallery",
    categoryPublic: "官方",
    categoryCustom: "我的",
    publicAgents: "官方智能体",
    customAgents: "我的智能体",
    noCustomAgents: "还没有自定义智能体，点击上方按钮创建。",
    newChatGuide: {
      casesTitle: "从一个真实任务开始",
      casesDescription: "选择案例只会填入提示词，确认后由你发送。",
      tipsTitle: "开始之前",
      loading: "正在加载创作助手…",
      loadFailed: "创作助手信息加载失败，请重试。",
      retry: "重试",
      draftConfirmTitle: "输入框已有内容",
      draftConfirmDescription: "选择替换现有内容，或在后面追加案例提示词。",
      draftConfirmAppend: "追加",
      draftConfirmReplace: "替换",
    },
    newChatGuides: {
      "course-video": {
        displayName: "课程视频",
        title: "把知识与素材做成视频课",
        description: "从主题调研、真实素材或完整讲稿出发，交付一个讲解 MP4。",
        cases: [
          {
            id: "knowledge-explainer",
            title: "调研主题做知识科普",
            description: "从主题调研到讲稿、分镜与知识科普 MP4",
            prompt:
              "请围绕[主题]调研并制作约[时长]的知识科普视频，面向[受众]。先展示调研来源、讲稿和分镜供我确认，再用TTS配音和自定义Remotion图文动画交付一个MP4。",
          },
          {
            id: "product-tutorial",
            title: "用我的截图做产品讲解",
            description: "以真实截图设计图文版式，不虚构产品界面",
            prompt:
              "我会提供[产品/功能]的真实截图，请只用这些素材制作约[时长]的产品讲解视频。按图文版式设计分镜，先确认讲稿和分镜，再用TTS与自定义Remotion交付一个MP4；缺少截图请向我索取，不要虚构UI。",
          },
          {
            id: "concept-animation",
            title: "把我的数据做成动态图表",
            description: "严格按用户提供的数据生成图表与动态视频",
            prompt:
              "我会提供[数据文件/表格数据]，请严格按这些数据选择合适图表，设计动态图表和讲解分镜，先给我确认，再用TTS与自定义Remotion交付一个MP4。缺少数据请向我索取，不要编造数据。",
          },
          {
            id: "script-to-video",
            title: "完整讲稿保真转视频",
            description: "逐字保留完整讲稿，只设计分镜与画面",
            prompt:
              "我会提供完整口播稿，请逐字保留，不改写、不扩写，直接据此设计分镜，用TTS与自定义Remotion图文动画交付一个MP4：\n[粘贴完整讲稿]",
          },
        ],
        tips: [
          "告诉我主题、目标学员和期望时长，或提供真实截图、数据与完整讲稿。",
          "已有讲稿可直接粘贴，无需重新整理。",
          "先确认讲稿和分镜，再进入视频生成。",
        ],
      },
      "ppt-master": {
        displayName: "PPT 制作",
        title: "从资料到可编辑的 PPT",
        description: "从主题、文档或既有课件开始，得到能继续修改的演示文稿。",
        cases: [
          {
            id: "from-scratch",
            title: "按主题快速生成课件",
            description: "Quick 直达可编辑 PPTX，无 Stage 1/2",
            prompt:
              "请快速生成一份关于[主题]的可编辑课件，面向[受众]，共[页数]页。走Quick流程，跳过Stage 1/2策略与方案确认，直接制作并运行质量检查后交付PPTX。",
          },
          {
            id: "roundtrip",
            title: "局部修改现有 PPT",
            description: "Edit Native 保留原设计，未编辑页不重建",
            prompt:
              "我会上传现有PPTX，请走Edit Native流程，保持原设计，只修改我点名的页面或内容：[修改要求]。未编辑页不要重建，交付可编辑PPTX。",
          },
          {
            id: "beautify",
            title: "1:1 美化现有 PPT",
            description: "文字、页数和页序不变，仅做页内视觉优化",
            prompt:
              "我会上传现有PPTX，请走Beautify流程，保持全部文字、页数和页序1:1不变，只改善页内排版、间距、配色和视觉层级。拥挤页只在原页内改善；图表和表格按提取数据重建。交付可编辑PPTX。",
          },
          {
            id: "document-to-course",
            title: "把文档精编成课程 PPT",
            description: "PDF、DOCX、Markdown 经 Default 生成新课件",
            prompt:
              "我会提供PDF、DOCX或Markdown资料，请走Default流程，忠于资料精编结构与内容，先与我确认课程结构和设计方案，再生成新的可编辑PPTX。",
          },
        ],
        tips: [
          "可以上传参考文档或一份现有 PPT。",
          "说明受众、页数和使用场景会更高效。",
          "最终产物是可继续编辑的 PPTX。",
        ],
      },
      "creative-studio": {
        displayName: "视觉设计",
        title: "让知识成为好看的图片",
        description: "把要点、课程内容与品牌信息做成清楚、美观的视觉作品。",
        cases: [
          {
            id: "knowledge-card",
            title: "生成毛玻璃知识卡",
            description: "三种主题，交付 SVG 静态图与 MP4 动画",
            prompt:
              "请把[主题]整理为3个核心要点，制作毛玻璃风格知识卡。使用科技蓝、霓虹紫或翡翠绿三种主题之一，交付SVG静态图和MP4动画。",
          },
          {
            id: "course-poster",
            title: "设计科技风课程海报",
            description: "默认 1080×1920 竖版，交付课程海报 PNG",
            prompt:
              "请为课程[课程标题]设计科技风竖版课程海报，分享日期为[分享日期]，课程时长为[课程时长]，默认尺寸1080×1920，交付PNG。可补充讲师姓名和2-4个内容要点。",
          },
          {
            id: "infographic",
            title: "制作单张中文信息图",
            description: "21种布局 × 22种风格，交付一张 PNG",
            prompt:
              "请把[内容/流程]制作成一张中文信息图，面向[受众]。从21种布局与22种风格中推荐合适组合，经我确认后交付单张infographic.png。",
          },
          {
            id: "series-card",
            title: "把知识点画成连续漫画",
            description: "默认建议4页且可调整，保持角色与画风统一",
            prompt:
              "请围绕[主题]创作连续知识漫画，默认建议4页，我可以调整页数。先确认画风、角色表和分镜，保持连续角色与统一画风，再生成页面图片并合并为PDF。",
          },
        ],
        tips: [
          "提供主题、核心信息和目标受众。",
          "有尺寸或品牌风格要求可以一起说明。",
          "选择案例后仍可修改提示词再发送。",
        ],
      },
      "co-writer": {
        displayName: "协同写作",
        title: "把想法写成好内容",
        description: "从选题、大纲到完整稿件，按内容类型组织合适的表达。",
        cases: [
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
        tips: [
          "先选择最接近的内容类型。",
          "告诉我主题、受众和使用场景。",
          "长文会先确认大纲，再完成全文。",
        ],
      },
      "podcast-studio": {
        displayName: "播客制作",
        title: "把资料变成真实播客音频",
        description: "从文档、公开链接或主题调研出发，经必要确认后生成 MP3。",
        cases: [
          {
            id: "read_original",
            title: "文档原文朗读成音频",
            description: "保持提取正文和顺序，生成单音色 MP3",
            prompt:
              "[模式: read_original]\n请把我提供的PDF、DOCX、Markdown、HTML或安全链接中的可见正文逐字朗读，不增补、不总结，生成单音色MP3音频。",
          },
          {
            id: "news_single",
            title: "单人新闻播报",
            description: "调研后先确认完整新闻稿，再生成云舟 MP3",
            prompt:
              "[模式: news_single]\n请围绕[主题]调研并展示完整新闻口播稿和来源，经我确认后生成云舟单主持人MP3音频。",
          },
          {
            id: "interview_two",
            title: "双人访谈播客",
            description: "男女双人至少四轮访谈，确认后合成",
            prompt:
              "[模式: interview_two]\n请基于[主题/资料]设计男女双人至少4轮访谈，展示完整口播稿、来源和音色，经我确认后生成双人MP3音频。",
          },
          {
            id: "debate_two",
            title: "双人辩论播客",
            description: "男女双人正反辩论，确认后合成",
            prompt:
              "[模式: debate_two]\n请基于[主题/资料]设计男女双人至少4轮正反辩论，呈现双方立场和完整口播稿、来源及音色，经我确认后生成双人MP3音频。",
          },
        ],
        tips: [
          "可以提供文档、公开安全链接或一个调研主题。",
          "改写模式会先展示完整口播稿、来源和音色。",
          "确认后才合成并交付可下载的 MP3。",
        ],
      },
    },
  },

  // Workspace
  workspace: {
    // 首页 hero 与六张创作卡统一走 i18n。
    home: {
      heroTitle: "让好内容，从一个想法开始",
      createHeading:
        "选择一种能力，把文字、图片和资料变成视频、海报、PPT 或播客。",
      playbackUnavailable: "预览暂不可用，仍可开始创作",
      courseItems: {
        "course-video": {
          title: "文字动效",
          description: "把文稿与知识点变成节奏清晰、易理解的动态讲解视频",
          mediaAlt: "文字动效课程样例",
        },
        ai_image: {
          title: "图片动效",
          description: "根据脚本生成画面，让静态内容自然流动",
          mediaAlt: "图片动效样例",
        },
        ai_video: {
          title: "AI视频",
          description: "从一个创意生成更自由、更丰富的动态镜头",
          mediaAlt: "AI 视频样例",
        },
        "creative-studio": {
          title: "海报制作",
          description: "把主题与要点做成清晰、醒目的视觉海报",
          mediaAlt: "知识卡片海报样例",
        },
        "ppt-master": {
          title: "PPT制作",
          description: "从主题或资料生成结构清晰、可编辑的演示文稿",
          mediaAlt: "PPT 演示文稿装饰预览",
        },
        "podcast-studio": {
          title: "播客制作",
          description: "把文档、公开链接或主题调研做成可下载的 MP3 播客",
          mediaAlt: "双人耳机与音频波形封面",
        },
      },
    },
    works: {
      tabsLabel: "作品类型",
      history: "对话历史",
      searchLabel: "搜索对话标题",
      loading: "正在加载对话…",
      loadError: "对话加载失败，请稍后重试",
      retry: "重试",
      empty: (capability: string) => `暂无${capability}对话`,
      noResults: "没有匹配的对话",
      open: "打开",
      updated: "最近更新",
      updatedUnknown: "更新时间未知",
    },
  },

  // Conversation
  conversation: {
    noMessages: "还没有消息",
  },

  // Auth（DSN-003a）
  auth: {
    signInSubtitle: "登录你的账号",
    signUpSubtitle: "创建新账号",
    email: "邮箱",
    password: "密码",
    inviteCode: "邀请码",
    signIn: "登录",
    signUp: "注册",
    pleaseWait: "请稍候…",
    emailPlaceholder: "you@example.com",
    passwordPlaceholder: "•••••••",
    inviteCodePlaceholder: "输入邀请码",
    toSignUp: "还没有账号？注册",
    toSignIn: "已有账号？登录",
    backHome: "← 返回首页",
    emailRequired: "请输入邮箱",
    emailInvalid: "邮箱格式不正确",
    passwordRequired: "请输入密码",
    passwordMin: (n: number) => `密码至少 ${n} 位`,
    passwordMismatch: "两次输入的密码不一致",
    networkError: "网络异常，请稍后重试",
    createAdminSubtitle: "创建管理员账号",
    createAdminHint: "设置管理员账号以开始使用。",
    confirmPassword: "确认密码",
    passwordMin8Placeholder: "密码（至少 8 位）",
    confirmPlaceholder: "再次输入密码",
    createAdminAccount: "创建管理员账号",
    creating: "创建中…",
    completeSetupSubtitle: "完成管理员账号设置",
    completeSetupHint: "设置你的真实邮箱与新密码。",
    yourEmailPlaceholder: "你的邮箱",
    currentPasswordPlaceholder: "当前密码",
    newPasswordPlaceholder: "新密码",
    confirmNewPlaceholder: "再次输入新密码",
    completeSetup: "完成设置",
    settingUp: "设置中…",
    loading: "加载中…",
  },

  // Chats
  chats: {
    searchChats: "搜索对话",
    pinChat: "置顶对话",
    unpinChat: "取消置顶",
    pinChatFailed: "置顶状态更新失败。",
    deleteChatConfirmTitle: "确认删除",
    deleteChatConfirmDescription: "确定要删除这个会话吗？此操作无法撤销。",
    deleteChatConfirmAction: "删除",
    shareFallbackTitle: "复制分享链接",
    shareFallbackDescription:
      "自动复制被浏览器拒绝（常见于非 HTTPS 环境）。链接已在下方选中，可手动复制或点「复制到剪贴板」。",
  },

  // Page titles (document title)
  pages: {
    appName: "Trainer AI",
    chats: "对话",
    newChat: "新对话",
    untitled: "未命名",
  },

  // Tool calls
  toolCalls: {
    moreSteps: (count: number) => `查看其他 ${count} 个步骤`,
    lessSteps: "隐藏步骤",
    executeCommand: "执行命令",
    presentFiles: "展示文件",
    needYourHelp: "需要你的协助",
    useTool: (toolName: string) => `使用 “${toolName}” 工具`,
    searchFor: (query: string) => `搜索 “${query}”`,
    searchForRelatedInfo: "搜索相关信息",
    searchForRelatedImages: "搜索相关图片",
    searchForRelatedImagesFor: (query: string) => `搜索相关图片 “${query}”`,
    searchOnWebFor: (query: string) => `在网络上搜索 “${query}”`,
    viewWebPage: "查看网页",
    listFolder: "列出文件夹",
    readFile: "读取文件",
    writeFile: "写入文件",
    writeTodos: "更新 To-do 列表",
    skillInstallTooltip: "安装技能并使其可在 Trainer AI 中使用",
    details: "详情",
    input: "输入",
    result: "结果",
    error: "错误",
    noResult: "暂无结果",
    emptyResult: "结果为空",
    truncated: "输出过长，已截断显示",
    callId: "调用 ID",
    toolName: "工具",
  },

  uploads: {
    uploading: "上传中...",
    uploadingFiles: "文件上传中，请稍候...",
  },

  subtasks: {
    subtask: "子任务",
    executing: (count: number) =>
      `${count > 1 ? "并行" : ""}执行 ${count} 个子任务`,
    in_progress: "子任务运行中",
    completed: "子任务已完成",
    failed: "子任务失败",
  },

  // Token Usage
  tokenUsage: {
    title: "Token 用量",
    label: "Tokens",
    input: "输入",
    output: "输出",
    total: "总计",
    unavailable:
      "暂无 Token 用量。只有模型成功返回且供应商提供 usage_metadata 时才会显示。",
    unavailableShort: "未返回用量",
    overBudgetHint:
      "本会话累计 token 已超过该任务类型的告警线（仅提示，不影响任务继续）。",
  },

  // 空响应兜底错误卡片（2026-09-23 批 1 B2）
  errorFallback: {
    title: "响应中断",
    retry: "重试（发送「继续」）",
  },

  // Shortcuts
  shortcuts: {
    pages: "页面导航",
    searchActions: "搜索操作...",
    noResults: "未找到结果。",
    actions: "操作",
    keyboardShortcuts: "键盘快捷键",
    keyboardShortcutsDescription: "使用键盘快捷键更快地操作 Trainer AI。",
    openCommandPalette: "打开命令面板",
    toggleSidebar: "切换侧边栏",
  },

  // Settings
  settings: {
    title: "设置",
    description: "根据你的偏好调整 Trainer AI 的界面和行为。",
    sections: {
      account: "账号",
      appearance: "外观",
      memory: "记忆",
      tools: "工具",
      skills: "技能",
      channels: "IM 渠道",
      notification: "通知",
      about: "关于",
    },
    channels: {
      title: "IM 渠道",
      description: "配置你的 IM 渠道集成（飞书、企业微信等）",
      configure: "配置",
      configureChannel: (name: string) => `配置 ${name}`,
      enabled: (name: string) => `${name} 已启用`,
      disabled: (name: string) => `${name} 已禁用`,
      confirmDelete: "确定要删除此渠道配置吗？",
      deleted: (name: string) => `${name} 配置已删除`,
      saved: "渠道配置已保存",
    },
    memory: {
      title: "记忆",
      description:
        "Trainer AI 会在后台不断从你的对话中自动学习。这些记忆能帮助 Trainer AI 更好地理解你，并提供更个性化的体验。",
      empty: "暂无可展示的记忆数据。",
      exportButton: "导出记忆",
      exportSuccess: "记忆已导出",
      importButton: "导入记忆",
      importConfirmTitle: "导入记忆？",
      importConfirmDescription: "这会用选中的 JSON 备份覆盖当前记忆。",
      importFileLabel: "已选择文件",
      importInvalidFile: "读取记忆文件失败，请选择有效的 JSON 导出文件。",
      importSuccess: "记忆已导入",
      manualFactSource: "手动添加",
      addFact: "添加事实",
      addFactTitle: "添加记忆事实",
      editFactTitle: "编辑记忆事实",
      addFactSuccess: "事实已创建",
      editFactSuccess: "事实已更新",
      clearAll: "清空全部记忆",
      clearAllConfirmTitle: "要清空全部记忆吗？",
      clearAllConfirmDescription:
        "这会删除所有已保存的摘要和事实。此操作无法撤销。",
      clearAllSuccess: "已清空全部记忆",
      factDeleteConfirmTitle: "要删除这条事实吗？",
      factDeleteConfirmDescription:
        "这条事实会立即从记忆中删除。此操作无法撤销。",
      factDeleteSuccess: "事实已删除",
      factContentLabel: "内容",
      factCategoryLabel: "类别",
      factConfidenceLabel: "置信度",
      factContentPlaceholder: "描述你想保存的记忆事实",
      factCategoryPlaceholder: "context",
      factConfidenceHint: "请输入 0 到 1 之间的数字。",
      factSave: "保存事实",
      factValidationContent: "事实内容不能为空。",
      factValidationConfidence: "置信度必须是 0 到 1 之间的数字。",
      noFacts: "还没有保存的事实。",
      summaryReadOnly:
        "摘要分区当前仍为只读。现在你可以清空全部记忆或删除单条事实。",
      memoryFullyEmpty: "还没有保存任何记忆。",
      factPreviewLabel: "即将删除的事实",
      searchPlaceholder: "搜索记忆",
      filterAll: "全部",
      filterFacts: "事实",
      filterSummaries: "摘要",
      noMatches: "没有找到匹配的记忆。",
      markdown: {
        overview: "概览",
        userContext: "用户上下文",
        work: "工作",
        personal: "个人",
        topOfMind: "近期关注（Top of mind）",
        historyBackground: "历史背景",
        recentMonths: "近几个月",
        earlierContext: "更早上下文",
        longTermBackground: "长期背景",
        updatedAt: "更新于",
        facts: "事实",
        empty: "（空）",
        table: {
          category: "类别",
          confidence: "置信度",
          confidenceLevel: {
            veryHigh: "极高",
            high: "较高",
            normal: "一般",
            unknown: "未知",
          },
          source: "来源",
          createdAt: "创建时间",
          view: "查看",
        },
      },
    },
    appearance: {
      themeTitle: "主题",
      themeDescription: "跟随系统或选择固定的界面模式。",
      system: "系统",
      light: "浅色",
      dark: "深色",
      systemDescription: "自动跟随系统主题。",
      lightDescription: "更明亮的配色，适合日间使用。",
      darkDescription: "更暗的配色，减少眩光方便专注。",
      languageTitle: "语言",
      languageDescription: "在不同语言之间切换。",
    },
    tools: {
      title: "工具",
      description: "管理 MCP 工具的配置和启用状态。",
      adminOnly: "MCP 工具配置仅管理员可见。",
    },
    skills: {
      title: "技能",
      description: "管理 Agent Skill 配置和启用状态。",
      createSkill: "新建技能",
      emptyTitle: "还没有技能",
      // OBS-1（2026-10-07）：旧文案指向根目录 /skills/custom——P1-S3 起 custom
      // 技能按用户落盘，该指引已失效；改为指向现役入口（新建技能 / 会话安装）。
      emptyDescription:
        "点击右上角「新建技能」创建，或在会话中让智能体为你安装技能。",
      emptyButton: "创建你的第一个技能",
      emptyLegacyTitle: "没有旧版技能",
      emptyLegacyDescription:
        "旧版全局技能在此只读展示；仍需使用的可用开关启用（启停仅影响你自己）。",
      legacyAdminOnlyHint: "旧版技能仅管理员可启停。",
    },
    notification: {
      title: "通知",
      description:
        "Trainer AI 只会在窗口不活跃时发送完成通知，特别适合长时间任务：你可以先去做别的事，完成后会收到提醒。",
      requestPermission: "请求通知权限",
      deniedHint:
        "通知权限已被拒绝。可在浏览器的网站设置中重新开启，以接收完成提醒。",
      testButton: "发送测试通知",
      testTitle: "Trainer AI",
      testBody: "这是一条测试通知。",
      notSupported: "当前浏览器不支持通知功能。",
    },
  },

  // Voice Twin
  voiceTwin: {
    title: "配音",
    description: "录制讲师声音或选择预设音色，快速生成课程配音与讲解视频",
    upload: "上传音频",
    record: "开始录制",
    statusTraining: "训练中",
    statusReady: "已就绪",
    statusFailed: "失败",
    statusUploading: "上传中",
    statusPending: "排队中",
    statusExtracting: "提取中",
    statusConverting: "转换中",
    statusSynthesizing: "合成中",
    statusComposing: "合成中",
    submittedGenerating: "已提交，正在生成…",
    submitFailed: "提交失败",
    taskDeleted: "任务已删除",
    deleteTaskConfirm: "确定删除这个任务吗？",
    acceptLayoutDiff: "接受版式差异，继续生成",
    fontGateHint:
      "提示：为确保文字换行不乱，请在 PowerPoint「文件→选项→保存→勾选将字体嵌入文件」后上传",
    noVoicesHint:
      "还没有训练好的音色，请到「声线管理」创建，或选用下方预设音色",
    changeAudio: "换一个",
    uploadHint: "拖拽音频文件到此处上传，最大 10MB",
    supportedFormats: "支持 .wav, .mp3, .ogg, .m4a, .aac 格式",
    voiceNamePlaceholder: "为你的声线命名",
    modelType: "模型类型",
    submitTraining: "开始训练",
    submitting: "提交中…",
    synthesize: "合成语音",
    synthesizeTitle: "文本转语音",
    synthesizePlaceholder: "输入要合成的文本…",
    synthesizeButton: "开始合成",
    synthesizing: "合成中…",
    speedRatio: "语速",
    outputFormat: "输出格式",
    deleteConfirm: "确定要删除这条声线吗？此操作不可撤销。",
    deleteButton: "删除",
    deleting: "删除中…",
    prescreenFailed: "音频预检失败",
    prescreenPass: "音频预检通过",
    durationLabel: "时长",
    noVoices: "暂无声线，上传音频开始克隆",
    minimumDurationHint: "至少录制 30 秒，最长 3 分钟（10MB 限制）",
    backToUpload: "返回上传",
    teleprompterText: "提词文本",
    customText: "自定义文本",
    localPrescreenFailed: "本地校验失败",
    speakerIdPlaceholder: "预付费声音 ID（必填，如 S_xxx）",
    speakerIdRequired: "必须填写火山引擎控制台创建的声音 ID",
    fileTooLarge: "文件过大，最大支持 10MB",
    scriptToAudio: "文稿转音频",
    selectVoice: "选择音色",
    scriptPlaceholder: "粘贴或输入讲解稿文本...",
    generateAudio: "生成音频",
    generating: "生成中...",
    pptToVideo: "PPT 转视频",
    pptDropHint: "拖拽 PPT 文件到此处或点击选择",
    pptFileHint: ".pptx / .ppt（支持多选，每个最大 100MB）",
    preset: "预设效果",
    generateVideo: "生成视频",
    subtitleOverlay: "显示字幕",
    subtitleHint: "根据文稿内容自动生成字幕",
    productionHistory: "制作历史",
    docToAudio: "文档转音频",
    docDropHint: "拖拽文档到此处或点击选择",
    docFileHint: ".docx .doc .txt .md（最大 50MB）",
    docExtracting: "正在提取文本...",
    docPreview: "编辑文本",
    docDirectGenerate: "直接生成",
    docTruncated: "文档过长，已截断前 100,000 字符",
    docCharCount: "字",
    docEstimatedTime: "预计朗读 {n} 分钟",
    // -- Voice Twin UX Upgrade --
    tabVoiceManagement: "声线管理",
    tabProduction: "课件配音工具箱",
    smartSegmentation: "智能分段",
    mergeAndGenerate: "合并生成",
    removeSegment: "删除",
    charsUnit: "字",
    myVoices: "我的音色",
    searchVoice: "搜索音色",
    noVoiceFound: "未找到匹配音色",
  },
};
