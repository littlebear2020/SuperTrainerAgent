import type { LucideIcon } from "lucide-react";

import type { AgentNewChatGuideId } from "@/core/agents/new-chat";

interface AgentNewChatCaseCopy {
  id: string;
  title: string;
  description: string;
  prompt: string;
}

interface AgentNewChatGuideCopy {
  displayName: string;
  title: string;
  description: string;
  cases: [
    AgentNewChatCaseCopy,
    AgentNewChatCaseCopy,
    AgentNewChatCaseCopy,
    AgentNewChatCaseCopy,
  ];
  tips: [string, string, string];
}

export interface Translations {
  // Locale meta
  locale: {
    localName: string;
  };

  // Common
  common: {
    settings: string;
    delete: string;
    edit: string;
    rename: string;
    openInNewWindow: string;
    close: string;
    more: string;
    loadMore: string;
    download: string;
    thinking: string;
    artifacts: string;
    public: string;
    custom: string;
    /** OBS-1 (2026-10-07): read-only legacy-skill tab label. */
    legacy: string;
    notAvailableInDemoMode: string;
    loading: string;
    lastUpdated: string;
    cancel: string;
    save: string;
    install: string;
    create: string;
    import: string;
    export: string;
    exportAsMarkdown: string;
    exportAsJSON: string;
    exportSuccess: string;
    previewNotSupported: string;
  };

  home: {
    tagline: string;
    enterWorkspace: string;
    highlights: {
      label: string;
      description: string;
    }[];
  };

  // Welcome
  welcome: {
    greeting: string;
    description: string;
    createYourOwnSkill: string;
    createYourOwnSkillDescription: string;
  };

  // Clipboard
  clipboard: {
    copyToClipboard: string;
    copiedToClipboard: string;
    linkCopied: string;
    /** DEF-4（2026-10-07）：复制会话链接菜单项（与发布灵感库的「分享」消歧）。 */
    copyLink: string;
    /** PR-2 4.5：工具详情面板复制失败反馈。 */
    failedToCopy: string;
  };

  artifactEditing: {
    unsaved: string;
    saving: string;
    saved: string;
    exit: string;
    discard: string;
    discardChanges: string;
    conflict: string;
    conflictShort: string;
    runInProgress: string;
    saveFailed: string;
  };

  // Input Box
  inputBox: {
    placeholder: string;
    createSkillPrompt: string;
    addAttachments: string;
    mode: string;
    flashMode: string;
    flashModeDescription: string;
    reasoningMode: string;
    reasoningModeDescription: string;
    reasoningModeUnavailable: string;
    proMode: string;
    proModeDescription: string;
    ultraMode: string;
    ultraModeDescription: string;
    reasoningEffort: string;
    reasoningEffortMinimal: string;
    reasoningEffortMinimalDescription: string;
    reasoningEffortLow: string;
    reasoningEffortLowDescription: string;
    reasoningEffortMedium: string;
    reasoningEffortMediumDescription: string;
    reasoningEffortHigh: string;
    reasoningEffortHighDescription: string;
    searchModels: string;
    surpriseMe: string;
    surpriseMePrompt: string;
    polish: string;
    polishing: string;
    polishEmpty: string;
    polishFailed: string;
    modelsLoadFailed: string;
    retry: string;
    modelDelistedToast: string;
    suggestions: {
      suggestion: string;
      prompt: string;
      icon: LucideIcon;
    }[];
    suggestionsCreate: (
      | {
          suggestion: string;
          prompt: string;
          icon: LucideIcon;
        }
      | {
          type: "separator";
        }
    )[];
  };

  // Sidebar
  sidebar: {
    recentChats: string;
    newChat: string;
    dashboard: string;
    chats: string;
    demoChats: string;
    agents: string;
    gallery: string;
    voiceTwin: string;
    writer: string;
    videoGen: string;
    /** Asset center (/workspace/assets, Phase 3.5 2026-09-11). */
    assets: string;
    mySpace: string;
    myWorks: string;
    myAssets: string;
    roleAdmin: string;
    roleUser: string;
    /** Regular user: their own usage. Admin: the platform-wide console. */
    consoleSelf: string;
    consoleAdmin: string;
  };

  // Operations console
  console: {
    /** Page heading, chosen by role — see sidebar.consoleSelf/consoleAdmin. */
    titleSelf: string;
    titleAdmin: string;
    subtitleSelf: string;
    subtitleAdmin: string;
    totalRuns: string;
    activeRuns: string;
    totalTokens: string;
    tokensHint: string;
    costUnpriced: string;
    /** What the spend figure does and does not include. */
    /** From which date per-model attribution exists at all. */
    overThreshold: string;
    totalCostAll: string;
    costBreakdownLlm: string;
    costBreakdownMedia: string;
    unpricedCard: string;
    unpricedCardHint: string;
    unpricedCardZero: string;
    runsPanelFailed: string;
    runsPanelThreshold: string;
    tabUsageAnalysis: string;
    tabPricingAdmin: string;
    dataFreshness: string;
    refreshNow: string;
    justNow: string;
    minutesAgo: string;
    chartTabUsage: string;
    chartTabCost: string;
    compositionByModel: string;
    compositionByProvider: string;
    compositionByType: string;
    compositionScopeNote: string;
    providerCostNote: string;
    priceFixBoundaryNote: string;
    emptyRunsInWindow: string;
    emptyNoData: string;
    emptyUnpricedOnly: string;
    usagePanelNote: string;
    costPanelNote: string;
    runsPanelNote: string;
    llmPanelNote: string;
    dailyTrend: string;
    windowTotal: string;
    peakDay: string;
    noUsageInWindow: string;
    /** B2: scope note under the trend chart title. */
    trendScopeNote: string;
    /** B2: legend labels for stacked bars. */
    legendChat: string;
    legendLlm: string;
    byModel: string;
    subagentTokens: string;
    runsTitle: string;
    noRuns: string;
    colCreatedAt: string;
    colTask: string;
    colUser: string;
    colStatus: string;
    colModel: string;
    colTokens: string;
    colCost: string;
    colDuration: string;
    colError: string;
    stopReasonHint: string;
    tokensLabel: string;
    runsLabel: string;
    costLabel: string;
    allUsers: string;
    allStatuses: string;
    lastNDays: string;
    showingRange: string;
    previous: string;
    next: string;
    loading: string;
    errorNoDatabase: string;
    errorForbidden: string;
    /** Retry button label for inline error states. */
    retry: string;
    // Pricing panel (A2)
    pricingTitle: string;
    pricingSubtitle: string;
    pricingCurrency: string;
    pricingInputPrice: string;
    pricingOutputPrice: string;
    pricingCacheHitPrice: string;
    pricingCacheHitPlaceholder: string;
    pricingSave: string;
    pricingSaving: string;
    pricingReplaceWarning: string;
    pricingSourceDb: string;
    pricingSourceYaml: string;
    pricingSourceUnpriced: string;
    pricingSaveSuccess: string;
    pricingSaveError: string;
    pricingNoDatabase: string;
    pricingDeleteConfirm: (count: number, names: string) => string;
    // LLM usage panel (C2)
    llmUsageTitle: string;
    llmUsageSubtitle: string;
    llmUsageDomain: string;
    llmUsageCalls: string;
    llmUsageFailedCalls: string;
    llmUsageTokens: string;
    llmUsageCost: string;
    llmUsageEmpty: string;
    llmUsageDomainVideoGen: string;
    llmUsageDomainVimax: string;
    llmUsageDomainWriterAssist: string;
    llmUsageDomainGalleryRecipe: string;
    llmUsageDomainSuggestions: string;
    llmUsageDomainUnknown: string;
    // External calls panel (F2)
    externalCallsTitle: string;
    externalCallsTotal: string;
    externalCallsProvider: string;
    externalCallsTabAll: string;
    externalCallsTabImage: string;
    externalCallsTabVideo: string;
    externalCallsTabTts: string;
    costChartReal: string;
    costChartEstimated: string;
    // Media pricing section
    pricingMagnitudeConfirm: string;
    mediaPricingTitle: string;
    mediaPricingSubtitle: string;
    mediaPricingProvider: string;
    mediaPricingModelId: string;
    mediaPricingModelPlaceholder: string;
    mediaPricingType: string;
    mediaPricingUnit: string;
    mediaPricingPrice: string;
    mediaPricingSourceDefault: string;
    mediaPricingSourceLabel: string;
    mediaPricingRowSave: string;
    mediaPricingRowDelete: string;
    mediaPricingDeleteConfirm: (label: string) => string;
    mediaPricingModelChangeWarn: string;
    mediaPricingInvalidPrice: string;
    runsPanelWindowSuffix: string;
    callsSuffix: string;
    // PROVIDER_LABELS
    providerGptImage2: string;
    providerSeedream: string;
    providerSeedanceArk: string;
    providerSeedance: string;
    providerQihu: string;
    providerNanoBanana: string;
    providerHappyhorse: string;
    providerDashscope: string;
    providerHailuo: string;
    providerDoubaoTts: string;
    providerQihuTts: string;
    providerVolcengine: string;
    providerAiImage: string;
    providerAiVideo: string;
  };

  // Agents
  agents: {
    title: string;
    newAgent: string;
    emptyTitle: string;
    emptyDescription: string;
    chat: string;
    delete: string;
    deleteConfirm: string;
    deleteSuccess: string;
    newChat: string;
    createPageTitle: string;
    createPageSubtitle: string;
    nameStepTitle: string;
    nameStepHint: string;
    nameStepPlaceholder: string;
    nameStepContinue: string;
    nameStepInvalidError: string;
    nameStepAlreadyExistsError: string;
    nameStepNetworkError: string;
    nameStepCheckError: string;
    nameStepBootstrapMessage: string;
    save: string;
    saving: string;
    saveRequested: string;
    saveHint: string;
    saveCommandMessage: string;
    agentCreatedPendingRefresh: string;
    more: string;
    agentCreated: string;
    startChatting: string;
    backToGallery: string;
    categoryPublic: string;
    categoryCustom: string;
    publicAgents: string;
    customAgents: string;
    noCustomAgents: string;
    newChatGuide: {
      casesTitle: string;
      casesDescription: string;
      tipsTitle: string;
      loading: string;
      loadFailed: string;
      retry: string;
      draftConfirmTitle: string;
      draftConfirmDescription: string;
      draftConfirmAppend: string;
      draftConfirmReplace: string;
    };
    newChatGuides: Record<AgentNewChatGuideId, AgentNewChatGuideCopy>;
  };

  // Workspace
  workspace: {
    /** Home hero and the fixed six-capability creation grid. */
    home: {
      heroTitle: string;
      createHeading: string;
      playbackUnavailable: string;
      courseItems: Record<
        | "course-video"
        | "ai_image"
        | "ai_video"
        | "creative-studio"
        | "ppt-master"
        | "podcast-studio",
        {
          title: string;
          description: string;
          mediaAlt: string;
        }
      >;
    };
    works: {
      tabsLabel: string;
      history: string;
      searchLabel: string;
      loading: string;
      loadError: string;
      retry: string;
      empty: (capability: string) => string;
      noResults: string;
      open: string;
      updated: string;
      updatedUnknown: string;
    };
  };

  // Conversation
  conversation: {
    noMessages: string;
  };

  // Auth（DSN-003a，2026-09-14：login/setup 页中文化 + 受控校验文案）
  auth: {
    signInSubtitle: string;
    signUpSubtitle: string;
    email: string;
    password: string;
    inviteCode: string;
    signIn: string;
    signUp: string;
    pleaseWait: string;
    emailPlaceholder: string;
    passwordPlaceholder: string;
    inviteCodePlaceholder: string;
    toSignUp: string;
    toSignIn: string;
    backHome: string;
    emailRequired: string;
    emailInvalid: string;
    passwordRequired: string;
    passwordMin: (n: number) => string;
    passwordMismatch: string;
    networkError: string;
    createAdminSubtitle: string;
    createAdminHint: string;
    confirmPassword: string;
    passwordMin8Placeholder: string;
    confirmPlaceholder: string;
    createAdminAccount: string;
    creating: string;
    completeSetupSubtitle: string;
    completeSetupHint: string;
    yourEmailPlaceholder: string;
    currentPasswordPlaceholder: string;
    newPasswordPlaceholder: string;
    confirmNewPlaceholder: string;
    completeSetup: string;
    settingUp: string;
    loading: string;
  };

  // Chats
  chats: {
    searchChats: string;
    pinChat: string;
    unpinChat: string;
    pinChatFailed: string;
    /** P2-1/BUG-003（2026-09-14）：会话删除确认弹层文案。 */
    deleteChatConfirmTitle: string;
    deleteChatConfirmDescription: string;
    deleteChatConfirmAction: string;
    /** DEF-4（2026-10-07）：剪贴板被拒时的分享链接兜底弹层文案。 */
    shareFallbackTitle: string;
    shareFallbackDescription: string;
  };

  // Page titles (document title)
  pages: {
    appName: string;
    chats: string;
    newChat: string;
    untitled: string;
  };

  // Tool calls
  toolCalls: {
    moreSteps: (count: number) => string;
    lessSteps: string;
    executeCommand: string;
    presentFiles: string;
    needYourHelp: string;
    useTool: (toolName: string) => string;
    searchForRelatedInfo: string;
    searchForRelatedImages: string;
    searchFor: (query: string) => string;
    searchForRelatedImagesFor: (query: string) => string;
    searchOnWebFor: (query: string) => string;
    viewWebPage: string;
    listFolder: string;
    readFile: string;
    writeFile: string;
    writeTodos: string;
    skillInstallTooltip: string;
    /** PR-2 4.6：工具详情面板（D4 常显默认收起）。 */
    details: string;
    input: string;
    result: string;
    error: string;
    noResult: string;
    emptyResult: string;
    truncated: string;
    callId: string;
    toolName: string;
  };

  // Uploads
  uploads: {
    uploading: string;
    uploadingFiles: string;
  };

  // Subtasks
  subtasks: {
    subtask: string;
    executing: (count: number) => string;
    in_progress: string;
    completed: string;
    failed: string;
  };

  // Token Usage
  tokenUsage: {
    title: string;
    label: string;
    input: string;
    output: string;
    total: string;
    unavailable: string;
    unavailableShort: string;
    /** D3（2026-09-23）：thread 累计 tokens 超过任务告警线时的徽章悬浮提示。 */
    overBudgetHint: string;
  };

  // LLM empty-response fallback card (B2, 2026-09-23 plan DEF-03/OBS-02)
  errorFallback: {
    title: string;
    retry: string;
  };

  // Shortcuts
  shortcuts: {
    /** DSN-002（2026-09-14）：⌘K 页面导航组标题。 */
    pages: string;
    searchActions: string;
    noResults: string;
    actions: string;
    keyboardShortcuts: string;
    keyboardShortcutsDescription: string;
    openCommandPalette: string;
    toggleSidebar: string;
  };

  // Settings
  settings: {
    title: string;
    description: string;
    sections: {
      account: string;
      appearance: string;
      memory: string;
      tools: string;
      skills: string;
      channels: string;
      notification: string;
      about: string;
    };
    channels: {
      title: string;
      description: string;
      configure: string;
      configureChannel: (name: string) => string;
      enabled: (name: string) => string;
      disabled: (name: string) => string;
      confirmDelete: string;
      deleted: (name: string) => string;
      saved: string;
    };
    memory: {
      title: string;
      description: string;
      empty: string;
      exportButton: string;
      exportSuccess: string;
      importButton: string;
      importConfirmTitle: string;
      importConfirmDescription: string;
      importFileLabel: string;
      importInvalidFile: string;
      importSuccess: string;
      manualFactSource: string;
      addFact: string;
      addFactTitle: string;
      editFactTitle: string;
      addFactSuccess: string;
      editFactSuccess: string;
      clearAll: string;
      clearAllConfirmTitle: string;
      clearAllConfirmDescription: string;
      clearAllSuccess: string;
      factDeleteConfirmTitle: string;
      factDeleteConfirmDescription: string;
      factDeleteSuccess: string;
      factContentLabel: string;
      factCategoryLabel: string;
      factConfidenceLabel: string;
      factContentPlaceholder: string;
      factCategoryPlaceholder: string;
      factConfidenceHint: string;
      factSave: string;
      factValidationContent: string;
      factValidationConfidence: string;
      noFacts: string;
      summaryReadOnly: string;
      memoryFullyEmpty: string;
      factPreviewLabel: string;
      searchPlaceholder: string;
      filterAll: string;
      filterFacts: string;
      filterSummaries: string;
      noMatches: string;
      markdown: {
        overview: string;
        userContext: string;
        work: string;
        personal: string;
        topOfMind: string;
        historyBackground: string;
        recentMonths: string;
        earlierContext: string;
        longTermBackground: string;
        updatedAt: string;
        facts: string;
        empty: string;
        table: {
          category: string;
          confidence: string;
          confidenceLevel: {
            veryHigh: string;
            high: string;
            normal: string;
            unknown: string;
          };
          source: string;
          createdAt: string;
          view: string;
        };
      };
    };
    appearance: {
      themeTitle: string;
      themeDescription: string;
      system: string;
      light: string;
      dark: string;
      systemDescription: string;
      lightDescription: string;
      darkDescription: string;
      languageTitle: string;
      languageDescription: string;
    };
    tools: {
      title: string;
      description: string;
      adminOnly: string;
    };
    skills: {
      title: string;
      description: string;
      createSkill: string;
      emptyTitle: string;
      emptyDescription: string;
      emptyButton: string;
      /** OBS-1 (2026-10-07): legacy-tab empty copy (no create CTA). */
      emptyLegacyTitle: string;
      emptyLegacyDescription: string;
      /** 收尾复核批（2026-10-07）：非 admin 在 legacy tab 的行内禁用提示。 */
      legacyAdminOnlyHint: string;
    };
    notification: {
      title: string;
      description: string;
      requestPermission: string;
      deniedHint: string;
      testButton: string;
      testTitle: string;
      testBody: string;
      notSupported: string;
    };
  };

  voiceTwin: {
    title: string;
    description: string;
    upload: string;
    record: string;
    statusTraining: string;
    statusReady: string;
    statusFailed: string;
    statusUploading: string;
    statusPending: string;
    statusExtracting: string;
    statusConverting: string;
    statusSynthesizing: string;
    statusComposing: string;
    submittedGenerating: string;
    submitFailed: string;
    taskDeleted: string;
    deleteTaskConfirm: string;
    acceptLayoutDiff: string;
    fontGateHint: string;
    noVoicesHint: string;
    changeAudio: string;
    uploadHint: string;
    supportedFormats: string;
    voiceNamePlaceholder: string;
    modelType: string;
    submitTraining: string;
    submitting: string;
    synthesize: string;
    synthesizeTitle: string;
    synthesizePlaceholder: string;
    synthesizeButton: string;
    synthesizing: string;
    speedRatio: string;
    outputFormat: string;
    deleteConfirm: string;
    deleteButton: string;
    deleting: string;
    prescreenFailed: string;
    prescreenPass: string;
    durationLabel: string;
    noVoices: string;
    minimumDurationHint: string;
    backToUpload: string;
    teleprompterText: string;
    customText: string;
    localPrescreenFailed: string;
    speakerIdPlaceholder: string;
    speakerIdRequired: string;
    fileTooLarge: string;
    scriptToAudio: string;
    selectVoice: string;
    scriptPlaceholder: string;
    generateAudio: string;
    generating: string;
    pptToVideo: string;
    pptDropHint: string;
    pptFileHint: string;
    preset: string;
    generateVideo: string;
    subtitleOverlay: string;
    subtitleHint: string;
    productionHistory: string;
    docToAudio: string;
    docDropHint: string;
    docFileHint: string;
    docExtracting: string;
    docPreview: string;
    docDirectGenerate: string;
    docTruncated: string;
    docCharCount: string;
    docEstimatedTime: string;
    // -- Voice Twin UX Upgrade --
    tabVoiceManagement: string;
    tabProduction: string;
    smartSegmentation: string;
    mergeAndGenerate: string;
    removeSegment: string;
    charsUnit: string;
    myVoices: string;
    searchVoice: string;
    noVoiceFound: string;
  };
}
