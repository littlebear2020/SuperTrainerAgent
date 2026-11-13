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

export const enUS: Translations = {
  // Locale meta

  locale: {
    localName: "English",
  },

  // Common

  common: {
    settings: "Settings",

    delete: "Delete",

    edit: "Edit",

    rename: "Rename",

    openInNewWindow: "Open in new window",

    close: "Close",

    more: "More",

    loadMore: "Load more",

    download: "Download",

    thinking: "Thinking",

    artifacts: "Artifacts",

    public: "Public",

    custom: "Custom",

    legacy: "Legacy",

    notAvailableInDemoMode: "Not available in demo mode",

    loading: "Loading...",

    lastUpdated: "Last updated",

    cancel: "Cancel",

    save: "Save",

    install: "Install",

    create: "Create",

    import: "Import",

    export: "Export",

    exportAsMarkdown: "Export as Markdown",

    exportAsJSON: "Export as JSON",

    exportSuccess: "Conversation exported",

    previewNotSupported: "preview is not supported",
  },

  // Home

  home: {
    tagline: "Your AI SuperAgent Workspace",

    enterWorkspace: "Enter Workspace",

    highlights: [
      {
        label: "Deep Research",

        description:
          "Multi-angle systematic research with comprehensive reports",
      },

      {
        label: "Data Analysis",

        description: "Upload data files, SQL queries and visualizations",
      },

      {
        label: "Creative Generation",

        description: "Generate images, videos, podcasts, slides and web pages",
      },
    ],
  },

  // Welcome

  welcome: {
    greeting: "Hello, again!",

    description:
      "Welcome to 🦌 Trainer AI, an open source super agent. With built-in and custom skills, Trainer AI helps you search on the web, analyze data, and generate artifacts like slides, web pages and do almost anything.",

    createYourOwnSkill: "Create Your Own Skill",

    createYourOwnSkillDescription:
      "Create your own skill to release the power of Trainer AI. With customized skills,\nTrainer AI can help you search on the web, analyze data, and generate\n artifacts like slides, web pages and do almost anything.",
  },

  // Clipboard

  clipboard: {
    copyToClipboard: "Copy to clipboard",

    copiedToClipboard: "Copied to clipboard",

    linkCopied: "Link copied to clipboard",

    copyLink: "Copy link",

    failedToCopy: "Failed to copy",
  },

  artifactEditing: {
    unsaved: "Unsaved",

    saving: "Saving...",

    saved: "Artifact saved",

    exit: "Exit editing",

    discard: "Discard changes",

    discardChanges: "Discard the unsaved changes to this artifact?",

    conflict:
      "This artifact changed after you started editing. Discard your draft and reload before saving.",

    conflictShort: "Changed remotely",

    runInProgress: "Wait for the current agent run to finish before saving.",

    saveFailed: "Failed to save artifact",
  },

  // Input Box

  inputBox: {
    placeholder: "How can I assist you today?",

    createSkillPrompt:
      "I want to create a new custom skill (saved under skills/custom/). To start, ask me what I want this skill to do.",

    addAttachments: "Add attachments",

    mode: "Mode",

    flashMode: "Flash",

    flashModeDescription: "Fast and efficient, but may not be accurate",

    reasoningMode: "Reasoning",

    reasoningModeUnavailable:
      "The selected model doesn't support deep reasoning — switch to Doubao or Qwen to enable it",

    reasoningModeDescription:
      "Reasoning before action, balance between time and accuracy",

    proMode: "Pro",

    proModeDescription:
      "Reasoning, planning and executing, get more accurate results, may take more time",

    ultraMode: "Ultra",

    ultraModeDescription:
      "Pro mode with subagents to divide work; best for complex multi-step tasks",

    reasoningEffort: "Reasoning Effort",

    reasoningEffortMinimal: "Minimal",

    reasoningEffortMinimalDescription: "Retrieval + Direct Output",

    reasoningEffortLow: "Low",

    reasoningEffortLowDescription: "Simple Logic Check + Shallow Deduction",

    reasoningEffortMedium: "Medium",

    reasoningEffortMediumDescription:
      "Multi-layer Logic Analysis + Basic Verification",

    reasoningEffortHigh: "High",

    reasoningEffortHighDescription:
      "Full-dimensional Logic Deduction + Multi-path Verification + Backward Check",

    searchModels: "Search models...",

    surpriseMe: "Surprise",

    surpriseMePrompt: "Surprise me",

    polish: "Polish",

    polishing: "Polishing...",

    polishEmpty: "Polish returned nothing; your text was kept",

    polishFailed: "Polish failed",

    modelsLoadFailed: "Failed to load the model list",

    retry: "Retry",

    modelDelistedToast:
      "The model {model} you were using has been delisted; switched to the default model",

    suggestions: [
      {
        suggestion: "Write",

        prompt: "Write a blog post about the latest trends on [topic]",

        icon: PenLineIcon,
      },

      {
        suggestion: "Research",

        prompt:
          "Conduct a deep dive research on [topic], and summarize the findings.",

        icon: MicroscopeIcon,
      },

      {
        suggestion: "Collect",

        prompt: "Collect data from [source] and create a report.",

        icon: ShapesIcon,
      },

      {
        suggestion: "Learn",

        prompt: "Learn about [topic] and create a tutorial.",

        icon: GraduationCapIcon,
      },
    ],

    suggestionsCreate: [
      {
        suggestion: "Webpage",

        prompt: "Create a webpage about [topic]",

        icon: CompassIcon,
      },

      {
        suggestion: "Image",

        prompt: "Create an image about [topic]",

        icon: ImageIcon,
      },

      {
        suggestion: "Video",

        prompt: "Create a video about [topic]",

        icon: VideoIcon,
      },

      {
        type: "separator",
      },

      {
        suggestion: "Skill",

        prompt:
          "I want to create a new custom skill (saved under skills/custom/). To start, ask me what I want this skill to do.",

        icon: SparklesIcon,
      },
    ],
  },

  // Sidebar

  sidebar: {
    newChat: "New chat",

    dashboard: "Home",

    chats: "Chats",

    recentChats: "Recent chats",

    demoChats: "Demo chats",

    agents: "Agents",

    gallery: "Gallery",

    voiceTwin: "Dubbing",

    writer: "Writing",

    videoGen: "Video assets",

    assets: "Assets",

    mySpace: "My space",

    myWorks: "My works",

    myAssets: "My assets",

    roleAdmin: "Admin",

    roleUser: "User",

    consoleSelf: "My usage",

    consoleAdmin: "Operations console",
  },

  // Operations console

  console: {
    titleSelf: "My usage",

    titleAdmin: "Operations console",

    subtitleSelf: "Your own run usage and spend",

    subtitleAdmin: "Platform-wide run usage and spend",

    totalRuns: "Total runs",

    activeRuns: "Active",

    totalTokens: "Total tokens",

    tokensHint: "Includes subagent usage",

    costUnpriced: "No pricing configured",

    overThreshold: "High-usage runs",

    totalCostAll: "Full-Chain Cost",

    costBreakdownLlm: "LLM",

    costBreakdownMedia: "Media",

    unpricedCard: "Unpriced Calls",

    unpricedCardHint: "{n} more calls have no pricing row and are not counted",

    unpricedCardZero: "All calls priced",

    runsPanelFailed: "Failed runs: {n} (incl. timeouts)",

    runsPanelThreshold: "High-usage runs: {n} (attention threshold, advisory)",

    tabUsageAnalysis: "Usage Analysis",

    tabPricingAdmin: "Pricing Admin",

    dataFreshness: "Updated {n} ago",

    refreshNow: "Refresh",

    justNow: "just now",

    minutesAgo: "{n} min",

    chartTabUsage: "Usage",

    chartTabCost: "Cost",

    compositionByModel: "By Model",

    compositionByProvider: "By Provider",

    compositionByType: "By Type",

    compositionScopeNote:
      "Model/provider split covers tasks since 2026-08-03; by-type covers media-generation calls",

    providerCostNote:
      "Real bills first, estimation otherwise; * marks estimates",

    priceFixBoundaryNote:
      "Media unit prices before 2026-08-27 were misaligned; trends crossing that date are not comparable (read-time recompute)",

    emptyRunsInWindow: "No task records in the selected window",

    emptyNoData: "No data in window",

    emptyUnpricedOnly: "Calls present but none priced",

    usagePanelNote:
      "Chat + dubbing/writing/video LLM calls · excludes media generation",

    costPanelNote:
      "Media-generation calls (image/video/TTS) · real bills first + estimates",

    runsPanelNote: "Chat-agent runs · excludes video/dubbing/writing tasks",

    llmPanelNote:
      "Non-conversation LLM calls (video/dubbing/writing/image pipelines)",

    dailyTrend: "Daily token trend",

    windowTotal: "Window total",

    peakDay: "Peak day",

    noUsageInWindow:
      "No conversations or LLM calls in the last {n} days. Start a task in Chats, Video or Writer and the trend will appear here.",

    trendScopeNote:
      "Includes conversations and LLM calls from video/writing; media generation costs are in the External API panel below",

    legendChat: "Chat",

    legendLlm: "LLM calls",

    byModel: "By model",

    subagentTokens: "Subagent tokens",

    runsTitle: "Runs",

    noRuns: "No runs match these filters",

    colCreatedAt: "Created",

    colTask: "Task",

    colUser: "User",

    colStatus: "Status",

    colModel: "Model",

    colTokens: "Tokens",

    colCost: "Cost",

    colDuration: "Duration",

    colError: "Failure reason",

    stopReasonHint: "Why a middleware wrapped this run up early",

    tokensLabel: "Tokens",

    runsLabel: "Runs",

    costLabel: "Cost",

    allUsers: "All users",

    allStatuses: "All statuses",

    lastNDays: "Last {n} days",

    showingRange: "Showing {from}–{to}",

    previous: "Previous",

    next: "Next",

    loading: "Loading…",

    errorNoDatabase:
      "The console requires a SQL database; set database.backend to sqlite or postgres in config.yaml.",

    errorForbidden: "Only an admin may view another user's usage.",

    retry: "Retry",

    // Pricing panel (A2)

    pricingTitle: "Model Pricing",

    pricingSubtitle:
      "Price changes take effect immediately — no restart needed",

    pricingCurrency: "Currency",

    pricingInputPrice: "Input Price (¥/1M tokens)",

    pricingOutputPrice: "Output Price (¥/1M tokens)",

    pricingCacheHitPrice: "Cache Read Price (¥/1M tokens)",

    pricingCacheHitPlaceholder:
      "Empty = billed at input price; 0 = free on cache hit",

    pricingSave: "Save Pricing",

    pricingSaving: "Saving…",

    pricingReplaceWarning:
      "Saving replaces the entire pricing table with this table",

    pricingSourceDb: "Configured",

    pricingSourceYaml: "Default",

    pricingSourceUnpriced: "Unpriced",

    pricingSaveSuccess: "Pricing saved",

    pricingSaveError: "Failed to save pricing",

    pricingNoDatabase: "Pricing requires a SQL database backend",

    // LLM usage panel (C2)

    llmUsageTitle: "AI Writing Usage",

    llmUsageSubtitle:
      "Usage and cost of AI-generated scripts, storyboards, and narration",

    llmUsageDomain: "Feature",

    llmUsageCalls: "Calls",

    llmUsageFailedCalls: "Failed",

    llmUsageTokens: "Tokens",

    llmUsageCost: "Cost",

    llmUsageEmpty: "No LLM call records in this window",

    // External calls panel (F2)

    externalCallsTitle: "Asset Generation Calls",

    externalCallsTotal: "Total Calls",

    externalCallsProvider: "Service",

    externalCallsTabAll: "All",

    externalCallsTabImage: "Image",

    externalCallsTabVideo: "Video",

    externalCallsTabTts: "Voice",

    costChartReal: "Real",

    costChartEstimated: "Estimated",

    // Media pricing section

    pricingMagnitudeConfirm:
      "Unit prices changed by more than 5x on existing keys. Save with the new prices?",

    mediaPricingTitle: "Media Unit Pricing",

    mediaPricingSubtitle:
      "Configure per-unit prices for image/video/voice generation — drives cost estimates and budget gate",

    mediaPricingProvider: "Service",

    mediaPricingModelId: "Model",

    mediaPricingModelPlaceholder: "Default row (provider-level)",

    mediaPricingType: "Type",

    mediaPricingUnit: "Unit",

    mediaPricingPrice: "Unit Price",

    mediaPricingSourceDefault: "Built-in",

    mediaPricingSourceLabel: "Source",

    // C7 single-row PATCH/DELETE (A20, 2026-08-27)

    mediaPricingRowSave: "Save row",

    mediaPricingRowDelete: "Delete",

    mediaPricingDeleteConfirm: (label: string) =>
      `Delete pricing row "${label}"? The key falls back to the built-in default or becomes unpriced.`,

    mediaPricingModelChangeWarn:
      "Model ID changed: saving upserts a NEW row under the new key (the old-key row stays — use Delete to remove it). Continue?",

    mediaPricingInvalidPrice: "Price must be a number greater than 0",

    // F2 (2026-08-27 review fix): annotate the runs-panel headline's windowed

    // counters (table below stays all-time)

    runsPanelWindowSuffix: " · last {days} days",

    // F4: composition "by type" calls unit

    callsSuffix: "calls",

    // DOMAIN_LABELS i18n (P6-6)

    llmUsageDomainVideoGen: "Video Script",

    llmUsageDomainVimax: "Video Engine",

    llmUsageDomainWriterAssist: "Writing Assistant",

    llmUsageDomainGalleryRecipe: "Gallery Recipe",

    llmUsageDomainSuggestions: "Follow-up Suggestions",

    llmUsageDomainUnknown: "Unknown",

    // PROVIDER_LABELS

    providerGptImage2: "GPT Image (360)",

    providerSeedream: "ByteDance Image",

    providerSeedanceArk: "ByteDance Video",

    providerSeedance: "360 Video",

    providerQihu: "360 Media",

    providerNanoBanana: "360 Image (NanoBanana)",

    providerHappyhorse: "Happyhorse Video",

    providerDashscope: "DashScope Image",

    providerHailuo: "Hailuo Video",

    providerDoubaoTts: "Doubao Voice",

    providerQihuTts: "360 Voice",

    providerVolcengine: "Volcengine Voice",

    providerAiImage: "AI Image",

    providerAiVideo: "AI Video",

    // Pricing panel — delete confirmation (A4)

    pricingDeleteConfirm: (count: number, names: string) =>
      count === 1
        ? `The saved pricing for this model will be cleared: ${names}. Continue?`
        : `The saved pricing for ${count} models will be cleared: ${names}. Continue?`,
  },

  // Agents

  agents: {
    title: "Agents",

    newAgent: "New Agent",

    emptyTitle: "No custom agents yet",

    emptyDescription:
      "Create your first custom agent with a specialized system prompt.",

    chat: "Chat",

    delete: "Delete",

    deleteConfirm:
      "Are you sure you want to delete this agent? This action cannot be undone.",

    deleteSuccess: "Agent deleted",

    newChat: "New chat",

    createPageTitle: "Design your Agent",

    createPageSubtitle:
      "Describe the agent you want — I'll help you create it through conversation.",

    nameStepTitle: "Name your new Agent",

    nameStepHint:
      "Letters, digits, and hyphens only — stored lowercase (e.g. code-reviewer)",

    nameStepPlaceholder: "e.g. code-reviewer",

    nameStepContinue: "Continue",

    nameStepInvalidError:
      "Invalid name — use only letters, digits, and hyphens",

    nameStepAlreadyExistsError: "An agent with this name already exists",

    nameStepNetworkError:
      "Network request failed — check your network or backend connection",

    nameStepCheckError: "Could not verify name availability — please try again",

    nameStepBootstrapMessage:
      "The new custom agent name is {name}. Let's bootstrap it's **SOUL**.",

    save: "Save agent",

    saving: "Saving agent...",

    saveRequested:
      "Save requested. Trainer AI is generating and saving an initial version now.",

    saveHint:
      "You can save this agent at any time from the top-right menu, even if this is only a first draft.",

    saveCommandMessage:
      "Please save this custom agent now based on everything we have discussed so far. Treat this as my explicit confirmation to save. If some details are still missing, make reasonable assumptions, generate a concise first SOUL.md in English, and call setup_agent immediately without asking me for more confirmation.",

    agentCreatedPendingRefresh:
      "The agent was created, but Trainer AI could not load it yet. Please refresh this page in a moment.",

    more: "More actions",

    agentCreated: "Agent created!",

    startChatting: "Start chatting",

    backToGallery: "Back to Gallery",

    categoryPublic: "Official",

    categoryCustom: "Custom",

    publicAgents: "Official Agents",

    customAgents: "My Agents",

    noCustomAgents:
      "No custom agents yet. Click the button above to create one.",

    newChatGuide: {
      casesTitle: "Start with a real task",

      casesDescription:
        "Choosing an example only fills the prompt. You decide when to send it.",

      tipsTitle: "Before you start",

      loading: "Loading your creative assistant…",

      loadFailed: "Could not load the creative assistant. Please try again.",

      retry: "Retry",

      draftConfirmTitle: "There is already text in the input",

      draftConfirmDescription:
        "Replace the current draft or append the example prompt after it.",

      draftConfirmAppend: "Append",

      draftConfirmReplace: "Replace",
    },

    newChatGuides: {
      "course-video": {
        displayName: "Course video",

        title: "Turn knowledge and real assets into video lessons",

        description:
          "Start with researched topics, real assets, or a complete script and deliver one explainer MP4.",

        cases: [
          {
            id: "knowledge-explainer",

            title: "Research a topic into an explainer",

            description: "From topic research to script, storyboard, and MP4",

            prompt:
              "Research [topic] and create a [duration] explainer video for [audience]. Show me the research sources, script, and storyboard for confirmation, then use TTS narration and custom Remotion motion graphics to deliver one MP4.",
          },

          {
            id: "product-tutorial",

            title: "Turn my screenshots into a product walkthrough",

            description: "Use real screenshots in image-and-text layouts",

            prompt:
              "I will provide real screenshots of [product/feature]. Use only these assets to create a [duration] product walkthrough with image-and-text layouts. Confirm the script and storyboard first, then use TTS and custom Remotion to deliver one MP4. Ask me for missing screenshots instead of inventing UI.",
          },

          {
            id: "concept-animation",

            title: "Turn my data into animated charts",

            description:
              "Create charts and motion video strictly from user data",

            prompt:
              "I will provide [a data file/table data]. Use only this data to select suitable charts and design animated charts and a narration storyboard for my confirmation, then use TTS and custom Remotion to deliver one MP4. Ask me for missing data instead of inventing values.",
          },

          {
            id: "script-to-video",

            title: "Turn my full script into video faithfully",

            description:
              "Preserve every word and only design scenes and visuals",

            prompt:
              "I will provide a complete narration script. Preserve it word for word, do not rewrite or expand it, and derive the storyboard directly from it. Use TTS and custom Remotion motion graphics to deliver one MP4:\n[Paste complete script]",
          },
        ],

        tips: [
          "Share the topic, learners, and duration, or provide real screenshots, data, and a complete script.",

          "Paste an existing script directly if you have one.",

          "Confirm the script and storyboard before video generation.",
        ],
      },

      "ppt-master": {
        displayName: "PPT creation",

        title: "From source material to an editable PPT",

        description:
          "Start with a topic, document, or existing deck and get slides you can keep editing.",

        cases: [
          {
            id: "from-scratch",

            title: "Quickly generate a deck from a topic",

            description: "Quick delivery of an editable PPTX without Stage 1/2",

            prompt:
              "Quickly generate an editable deck about [topic] for [audience], with [slide count] slides. Use the Quick workflow, skip Stage 1/2 strategy and solution confirmations, author the deck directly, run quality checks, and deliver a PPTX.",
          },

          {
            id: "roundtrip",

            title: "Edit selected content in an existing PPT",

            description: "Edit Native preserves the design and unedited slides",

            prompt:
              "I will upload an existing PPTX. Use Edit Native, preserve the original design, and change only the slides or content I specify: [requested edits]. Do not rebuild unedited slides. Deliver an editable PPTX.",
          },

          {
            id: "beautify",

            title: "Beautify an existing PPT 1:1",

            description:
              "Keep text, count, and order unchanged; improve within pages",

            prompt:
              "I will upload an existing PPTX. Use Beautify and keep all text, slide count, and slide order 1:1 unchanged. Improve only layout, spacing, colors, and visual hierarchy within each page. Improve crowded slides only within the original slide, and rebuild charts and tables from extracted data. Deliver an editable PPTX.",
          },

          {
            id: "document-to-course",

            title: "Turn documents into a course deck",

            description:
              "Refine PDF, DOCX, or Markdown into a new deck via Default",

            prompt:
              "I will provide PDF, DOCX, or Markdown source material. Use the Default workflow and stay faithful to the source material while refining its structure and content. Confirm the course structure and design approach with me, then generate a new editable PPTX.",
          },
        ],

        tips: [
          "Upload a reference document or an existing PPT if available.",

          "Share the audience, slide count, and presentation setting.",

          "The final deliverable is an editable PPTX.",
        ],
      },

      "creative-studio": {
        displayName: "Visual design",

        title: "Turn knowledge into beautiful images",

        description:
          "Transform key ideas, course content, and brand information into clear visual assets.",

        cases: [
          {
            id: "knowledge-card",

            title: "Create glassmorphism knowledge cards",

            description:
              "Three themes, with SVG static cards and MP4 animation",

            prompt:
              "Distill [topic] into three key takeaways and create a glassmorphism knowledge card. Choose one of three themes: tech blue, neon purple, or emerald green. Deliver an SVG static image and an MP4 animation.",
          },

          {
            id: "course-poster",

            title: "Design a tech-style course poster",

            description: "A 1080×1920 portrait poster delivered as PNG",

            prompt:
              "Design a tech-style portrait poster for [course title], with [sharing date] and [course duration]. Use the default 1080×1920 canvas and deliver a PNG. Optionally include the instructor name and 2-4 content highlights.",
          },

          {
            id: "infographic",

            title: "Create one Chinese infographic",

            description: "21 layouts × 22 styles, delivered as one PNG",

            prompt:
              "Turn [content/process] into one Chinese infographic for [audience]. Recommend a suitable combination from 21 layouts and 22 styles, then deliver a single infographic.png after my confirmation.",
          },

          {
            id: "series-card",

            title: "Turn knowledge into a continuous comic",

            description:
              "Four pages by default, adjustable, with consistent characters",

            prompt:
              "Create a continuous knowledge comic about [topic], using four pages by default and letting me adjust the page count. Confirm the art style, character sheet, and storyboard first. Keep recurring characters and the art style consistent, then generate the page images and merge them into a PDF.",
          },
        ],

        tips: [
          "Share the topic, core information, and target audience.",

          "Include size or brand-style requirements when known.",

          "You can edit the example prompt before sending it.",
        ],
      },

      "co-writer": {
        displayName: "Collaborative writing",

        title: "Turn ideas into strong content",

        description:
          "Move from topic and outline to a complete draft with a format-specific approach.",

        cases: [
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

        tips: [
          "Choose the content type closest to your goal.",

          "Share the topic, audience, and intended use.",

          "Long-form work confirms the outline before the full draft.",
        ],
      },

      "podcast-studio": {
        displayName: "Podcast production",

        title: "Turn source material into real podcast audio",

        description:
          "Start with documents, safe public links, or topic research and deliver MP3 after the required confirmation.",

        cases: [
          {
            id: "read_original",

            title: "Read a document aloud as audio",

            description:
              "Preserve extracted text and order in a single-voice MP3",

            prompt:
              "[模式: read_original]\nRead the visible text from my PDF, DOCX, Markdown, HTML, or safe public link verbatim, without adding or summarizing, and generate a single-voice MP3 audio.",
          },

          {
            id: "news_single",

            title: "Single-host news podcast",

            description:
              "Confirm the researched script before single-host audio",

            prompt:
              "[模式: news_single]\nResearch [topic] and show the complete news script and sources, then after my confirmation, generate a single-host MP3 audio with the Yunzhou voice.",
          },

          {
            id: "interview_two",

            title: "Two-person interview podcast",

            description: "At least four male/female turns before audio",

            prompt:
              "[模式: interview_two]\nCreate at least four interview turns between a male and a female host about [topic/source]. Show the complete script, sources, and voices, then after my confirmation, generate a two-person MP3 audio.",
          },

          {
            id: "debate_two",

            title: "Two-person debate podcast",

            description: "Male/female pro-and-con debate before audio",

            prompt:
              "[模式: debate_two]\nCreate at least four debate turns between a male and a female host about [topic/source], with both positions and the complete script, sources, and voices. After my confirmation, generate a two-person MP3 audio.",
          },
        ],

        tips: [
          "Provide a document, a safe public link, or a research topic.",

          "Rewritten modes show the complete script, sources, and voices first.",

          "Audio is synthesized and delivered as a downloadable MP3 after confirmation.",
        ],
      },
    },
  },

  // Workspace

  workspace: {
    // Home hero and all visible creation-entry copy stay locale-aware.

    home: {
      heroTitle: "Great content starts with one idea.",

      heroKicker: "YOUR CONTENT STUDIO",

      createHeading:
        "Choose a tool to turn your text, images, and source material into videos, posters, slides, or podcasts.",

      teacherAlt:
        "Smiling young Asian male cartoon teacher holding a book and pointer",

      teacherNote: "Your AI creative assistant",

      playbackUnavailable: "Preview unavailable. You can still start creating.",

      groups: {
        video: { title: "Video courses", kicker: "VIDEO CREATION" },

        studio: { title: "Design & audio courses", kicker: "DESIGN & AUDIO" },
      },

      courseItems: {
        "course-video": {
          title: "Text animation",

          description:
            "Turn scripts and key ideas into clear, easy-to-follow animated explainer videos",

          mediaAlt: "Text animation course sample",

          mediaLabel: "Script → animated lesson",

          tags: [
            "Topic → explainer video",

            "Screenshots → product tutorial",

            "Script → video, unchanged",
          ],
        },

        ai_image: {
          title: "Image motion",

          description:
            "Generate scenes from your script and bring static content to life",

          mediaAlt: "Image motion sample",

          mediaLabel: "Script → moving images",

          tags: [
            "Isometric infographics",

            "Whiteboard sketch",

            "Business footage",
          ],
        },

        ai_video: {
          title: "AI video",

          description: "Turn one idea into freer, richer dynamic shots",

          mediaAlt: "AI video sample",

          mediaLabel: "Script → dynamic shots",

          tags: ["Paper theater", "3D characters", "Business footage"],
        },

        "creative-studio": {
          title: "Course poster",

          description:
            "Turn a topic and key points into clear, striking visual posters",

          mediaAlt: "Full-bleed editorial poster artwork",

          mediaLabel: "Topic → visual poster",

          tags: [
            "Key points → knowledge cards",

            "Course → vertical poster",

            "Knowledge → comic series",
          ],
        },

        "ppt-master": {
          title: "PPT slides",

          description:
            "Turn a topic or source material into structured, editable presentations",

          mediaAlt: "Presentation canvases and charts",

          mediaLabel: "Sources → presentation",

          tags: [
            "Topic → quick slides",

            "Edit part of an old deck",

            "Restyle, keep text unchanged",
          ],
        },

        "podcast-studio": {
          title: "Voice podcast",

          description:
            "Turn documents, public links, or topic research into a downloadable MP3 podcast",

          mediaAlt: "Headphones and audio waveform cover",

          mediaLabel: "Sources → voice podcast",

          tags: [
            "Read the original text",

            "Solo news",

            "Two-person interview",

            "Two-person debate",
          ],
        },
      },
    },

    works: {
      tabsLabel: "Work type",

      history: " conversation history",

      searchLabel: "Search conversation titles",

      loading: "Loading conversations…",

      loadError: "Could not load conversations. Please try again.",

      retry: "Retry",

      empty: (capability: string) => `No ${capability} conversations yet`,

      noResults: "No matching conversations",

      open: "Open",

      updated: "Updated",

      updatedUnknown: "Update time unknown",
    },
  },

  // Conversation

  conversation: {
    noMessages: "No messages yet",
  },

  // Auth (DSN-003a)

  auth: {
    signInSubtitle: "Sign in to your account",

    signUpSubtitle: "Create a new account",

    email: "Email",

    password: "Password",

    inviteCode: "Invite Code",

    signIn: "Sign In",

    signUp: "Sign Up",

    pleaseWait: "Please wait...",

    emailPlaceholder: "you@example.com",

    passwordPlaceholder: "•••••••",

    inviteCodePlaceholder: "Enter invite code",

    toSignUp: "Don't have an account? Sign up",

    toSignIn: "Already have an account? Sign in",

    backHome: "← Back to home",

    emailRequired: "Please enter your email",

    emailInvalid: "Invalid email format",

    passwordRequired: "Please enter your password",

    passwordMin: (n: number) => `Password must be at least ${n} characters`,

    passwordMismatch: "Passwords do not match",

    networkError: "Network error. Please try again.",

    createAdminSubtitle: "Create admin account",

    createAdminHint: "Set up the administrator account to get started.",

    confirmPassword: "Confirm Password",

    passwordMin8Placeholder: "Password (min. 8 characters)",

    confirmPlaceholder: "Confirm password",

    createAdminAccount: "Create Admin Account",

    creating: "Creating account...",

    completeSetupSubtitle: "Complete admin account setup",

    completeSetupHint: "Set your real email and a new password.",

    yourEmailPlaceholder: "Your email",

    currentPasswordPlaceholder: "Current password",

    newPasswordPlaceholder: "New password",

    confirmNewPlaceholder: "Confirm new password",

    completeSetup: "Complete Setup",

    settingUp: "Setting up...",

    loading: "Loading...",
  },

  // Chats

  chats: {
    searchChats: "Search chats",

    pinChat: "Pin chat",

    unpinChat: "Unpin chat",

    pinChatFailed: "Failed to update pin state.",

    deleteChatConfirmTitle: "Confirm deletion",

    deleteChatConfirmDescription:
      "Are you sure you want to delete this chat? This action cannot be undone.",

    deleteChatConfirmAction: "Delete",

    shareFallbackTitle: "Copy share link",

    shareFallbackDescription:
      "The browser blocked automatic copying (common outside HTTPS). The link is selected below — copy it manually or use the button.",
  },

  // Page titles (document title)

  pages: {
    appName: "Trainer AI",

    chats: "Chats",

    newChat: "New chat",

    untitled: "Untitled",
  },

  // Tool calls

  toolCalls: {
    moreSteps: (count: number) => `${count} more step${count === 1 ? "" : "s"}`,

    lessSteps: "Less steps",

    executeCommand: "Execute command",

    presentFiles: "Present files",

    needYourHelp: "Need your help",

    useTool: (toolName: string) => `Use "${toolName}" tool`,

    searchFor: (query: string) => `Search for "${query}"`,

    searchForRelatedInfo: "Search for related information",

    searchForRelatedImages: "Search for related images",

    searchForRelatedImagesFor: (query: string) =>
      `Search for related images for "${query}"`,

    searchOnWebFor: (query: string) => `Search on the web for "${query}"`,

    viewWebPage: "View web page",

    listFolder: "List folder",

    readFile: "Read file",

    writeFile: "Write file",

    writeTodos: "Update to-do list",

    skillInstallTooltip: "Install skill and make it available to Trainer AI",

    details: "Details",

    input: "Input",

    result: "Result",

    error: "Error",

    noResult: "No result yet",

    emptyResult: "Empty result",

    truncated: "Output truncated for display",

    callId: "Call ID",

    toolName: "Tool",
  },

  // Subtasks

  uploads: {
    uploading: "Uploading...",

    uploadingFiles: "Uploading files, please wait...",
  },

  subtasks: {
    subtask: "Subtask",

    executing: (count: number) =>
      `Executing ${count === 1 ? "" : count + " "}subtask${count === 1 ? "" : "s in parallel"}`,

    in_progress: "Running subtask",

    completed: "Subtask completed",

    failed: "Subtask failed",
  },

  // Token Usage

  tokenUsage: {
    title: "Token Usage",

    label: "Tokens",

    input: "Input",

    output: "Output",

    total: "Total",

    unavailable:
      "No token usage yet. Usage appears only after a successful model response when the provider returns usage_metadata.",

    unavailableShort: "No usage returned",

    overBudgetHint:
      "This thread's cumulative tokens exceed the task-type alert threshold (advisory only — the task continues).",
  },

  // LLM empty-response fallback card (B2, 2026-09-23 plan DEF-03/OBS-02)

  errorFallback: {
    title: "Response interrupted",

    retry: "Retry (send “继续”)",
  },

  // Shortcuts

  shortcuts: {
    pages: "Pages",

    searchActions: "Search actions...",

    noResults: "No results found.",

    actions: "Actions",

    keyboardShortcuts: "Keyboard Shortcuts",

    keyboardShortcutsDescription:
      "Navigate Trainer AI faster with keyboard shortcuts.",

    openCommandPalette: "Open Command Palette",

    toggleSidebar: "Toggle Sidebar",
  },

  // Settings

  settings: {
    title: "Settings",

    description: "Adjust how Trainer AI looks and behaves for you.",

    sections: {
      account: "Account",

      appearance: "Appearance",

      memory: "Memory",

      tools: "Tools",

      skills: "Skills",

      channels: "Channels",

      notification: "Notification",

      about: "About",
    },

    channels: {
      title: "IM Channels",

      description:
        "Configure your IM channel integrations (Feishu, WeChat, etc.)",

      configure: "Configure",

      configureChannel: (name: string) => `Configure ${name}`,

      enabled: (name: string) => `${name} enabled`,

      disabled: (name: string) => `${name} disabled`,

      confirmDelete:
        "Are you sure you want to delete this channel configuration?",

      deleted: (name: string) => `${name} configuration deleted`,

      saved: "Channel configuration saved",
    },

    memory: {
      title: "Memory",

      description:
        "Trainer AI automatically learns from your conversations in the background. These memories help Trainer AI understand you better and deliver a more personalized experience.",

      empty: "No memory data to display.",

      exportButton: "Export memory",

      exportSuccess: "Memory exported",

      importButton: "Import memory",

      importConfirmTitle: "Import memory?",

      importConfirmDescription:
        "This will overwrite your current memory with the selected JSON backup.",

      importFileLabel: "Selected file",

      importInvalidFile:
        "Failed to read the selected memory file. Please choose a valid JSON export.",

      importSuccess: "Memory imported",

      manualFactSource: "Manual",

      addFact: "Add fact",

      addFactTitle: "Add memory fact",

      editFactTitle: "Edit memory fact",

      addFactSuccess: "Fact created",

      editFactSuccess: "Fact updated",

      clearAll: "Clear all memory",

      clearAllConfirmTitle: "Clear all memory?",

      clearAllConfirmDescription:
        "This will remove all saved summaries and facts. This action cannot be undone.",

      clearAllSuccess: "All memory cleared",

      factDeleteConfirmTitle: "Delete this fact?",

      factDeleteConfirmDescription:
        "This fact will be removed from memory immediately. This action cannot be undone.",

      factDeleteSuccess: "Fact deleted",

      factContentLabel: "Content",

      factCategoryLabel: "Category",

      factConfidenceLabel: "Confidence",

      factContentPlaceholder: "Describe the memory fact you want to save",

      factCategoryPlaceholder: "context",

      factConfidenceHint: "Use a number between 0 and 1.",

      factSave: "Save fact",

      factValidationContent: "Fact content cannot be empty.",

      factValidationConfidence: "Confidence must be a number between 0 and 1.",

      noFacts: "No saved facts yet.",

      summaryReadOnly:
        "Summary sections are read-only for now. You can currently add, edit, or delete individual facts, or clear all memory.",

      memoryFullyEmpty: "No memory saved yet.",

      factPreviewLabel: "Fact to delete",

      searchPlaceholder: "Search memory",

      filterAll: "All",

      filterFacts: "Facts",

      filterSummaries: "Summaries",

      noMatches: "No matching memory found.",

      markdown: {
        overview: "Overview",

        userContext: "User context",

        work: "Work",

        personal: "Personal",

        topOfMind: "Top of mind",

        historyBackground: "History",

        recentMonths: "Recent months",

        earlierContext: "Earlier context",

        longTermBackground: "Long-term background",

        updatedAt: "Updated at",

        facts: "Facts",

        empty: "(empty)",

        table: {
          category: "Category",

          confidence: "Confidence",

          confidenceLevel: {
            veryHigh: "Very high",

            high: "High",

            normal: "Normal",

            unknown: "Unknown",
          },

          source: "Source",

          createdAt: "CreatedAt",

          view: "View",
        },
      },
    },

    appearance: {
      themeTitle: "Theme",

      themeDescription:
        "Choose how the interface follows your device or stays fixed.",

      system: "System",

      light: "Light",

      dark: "Dark",

      systemDescription: "Match the operating system preference automatically.",

      lightDescription: "Bright palette with higher contrast for daytime.",

      darkDescription: "Dim palette that reduces glare for focus.",

      languageTitle: "Language",

      languageDescription: "Switch between languages.",
    },

    tools: {
      title: "Tools",

      description: "Manage the configuration and enabled status of MCP tools.",

      adminOnly: "MCP tool configuration is visible to administrators only.",
    },

    skills: {
      title: "Agent Skills",

      description:
        "Manage the configuration and enabled status of the agent skills.",

      createSkill: "Create skill",

      emptyTitle: "No agent skill yet",

      emptyDescription:
        'Create one with "New Skill" in the top right, or ask an agent to install a skill in a chat.',

      emptyButton: "Create Your First Skill",

      emptyLegacyTitle: "No legacy skills",

      emptyLegacyDescription:
        "Legacy global skills appear here read-only. Enable the ones you still need — toggles only affect you.",

      legacyAdminOnlyHint: "Legacy skills can only be toggled by admins.",
    },

    notification: {
      title: "Notification",

      description:
        "Trainer AI only sends a completion notification when the window is not active. This is especially useful for long-running tasks so you can switch to other work and get notified when done.",

      requestPermission: "Request notification permission",

      deniedHint:
        "Notification permission was denied. You can enable it in your browser's site settings to receive completion alerts.",

      testButton: "Send test notification",

      testTitle: "Trainer AI",

      testBody: "This is a test notification.",

      notSupported: "Your browser does not support notifications.",
    },
  },

  // Voice Twin

  voiceTwin: {
    title: "Dubbing",

    description:
      "Record lecturer voice or select preset voices for course narration and video production",

    upload: "Upload Audio",

    record: "Start Recording",

    statusTraining: "Training",

    statusReady: "Ready",

    statusFailed: "Failed",

    statusUploading: "Uploading",

    statusPending: "Queued",

    statusExtracting: "Extracting",

    statusConverting: "Converting",

    statusSynthesizing: "Synthesizing",

    statusComposing: "Composing",

    submittedGenerating: "Submitted, generating…",

    submitFailed: "Submit failed",

    taskDeleted: "Task deleted",

    deleteTaskConfirm: "Delete this task?",

    acceptLayoutDiff: "Accept layout diff and continue",

    fontGateHint:
      "Tip: To prevent text wrapping issues, embed fonts in PowerPoint via File → Options → Save → Embed fonts in file",

    noVoicesHint:
      "No trained voices yet — create one in Voice Management, or pick a preset below",

    changeAudio: "Change",

    uploadHint: "Drag & drop audio file here, max 10MB",

    supportedFormats: "Supports .wav, .mp3, .ogg, .m4a, .aac formats",

    voiceNamePlaceholder: "Name your voice",

    modelType: "Model Type",

    submitTraining: "Start Training",

    submitting: "Submitting…",

    synthesize: "Synthesize",

    synthesizeTitle: "Text to Speech",

    synthesizePlaceholder: "Enter text to synthesize…",

    synthesizeButton: "Synthesize",

    synthesizing: "Synthesizing…",

    speedRatio: "Speed",

    outputFormat: "Output Format",

    deleteConfirm: "Are you sure? This action cannot be undone.",

    deleteButton: "Delete",

    deleting: "Deleting…",

    prescreenFailed: "Audio prescreen failed",

    prescreenPass: "Audio prescreen passed",

    durationLabel: "Duration",

    noVoices: "No voices yet. Upload audio to start cloning.",

    minimumDurationHint: "Min 30s, max 3min (10MB limit)",

    backToUpload: "Back to Upload",

    teleprompterText: "Teleprompter Text",

    customText: "Custom Text",

    localPrescreenFailed: "Local validation failed",

    speakerIdPlaceholder: "Pre-paid speaker ID (required, e.g. S_xxx)",

    speakerIdRequired: "Voice ID created in Volcengine console is required",

    fileTooLarge: "File too large, max 10MB",

    scriptToAudio: "Script to Audio",

    selectVoice: "Select Voice",

    scriptPlaceholder: "Paste or enter narration script...",

    generateAudio: "Generate Audio",

    generating: "Generating...",

    pptToVideo: "PPT to Video",

    pptDropHint: "Drag & drop a PPT file here or click to select",

    pptFileHint: ".pptx / .ppt (multi-select, max 100MB each)",

    preset: "Preset",

    generateVideo: "Generate Video",

    subtitleOverlay: "Show subtitles",

    subtitleHint: "Auto-generate subtitles from script",

    productionHistory: "Production History",

    docToAudio: "Doc to Audio",

    docDropHint: "Drag & drop a document here or click to select",

    docFileHint: ".docx .doc .txt .md (max 50MB)",

    docExtracting: "Extracting text...",

    docPreview: "Edit Text",

    docDirectGenerate: "Generate Directly",

    docTruncated: "Document too long, truncated to first 100,000 characters",

    docCharCount: "chars",

    docEstimatedTime: "Estimated {n} min",

    // -- Voice Twin UX Upgrade --

    tabVoiceManagement: "Voice Management",

    tabProduction: "Production Studio",

    smartSegmentation: "Smart Segmentation",

    mergeAndGenerate: "Merge & Generate",

    removeSegment: "Remove",

    charsUnit: "chars",

    myVoices: "My Voices",

    searchVoice: "Search voices",

    noVoiceFound: "No matching voices",
  },
};
