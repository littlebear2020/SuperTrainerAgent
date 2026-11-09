/**
 * Doc type capability matrix — controls per-type behavior across the workbench.
 *
 * Each doc_type determines:
 * - Tool strategy (what the co-writer agent is allowed to do)
 * - Sidebar panels (voice/video/design visibility)
 * - Export pipelines (PPT/PDF)
 * - Editor features (auto-annotation, mobile preview)
 */

import type { DocType } from "./types";

export type ExportType = "pdf" | "ppt";

export interface DocTypeConfig {
  label: string;
  icon: string;
  description: string;
  defaultTitle: string;
  /** Tool strategy — SOUL.md behavioral hints */
  allowProactiveSearch: boolean;
  allowOnDemandSearch: boolean;
  allowSandbox: boolean;
  /** Editor features */
  allowMobilePreview: boolean;
  /** Export pipelines */
  exports: ExportType[];
  /** Sidebar publish panels */
  showVoicePanel: boolean;
  showDesignPanel: boolean;
  /** AI capabilities */
  supportFromScratch: boolean;
}

export const DOC_TYPE_CONFIGS: Record<DocType, DocTypeConfig> = {
  article: {
    label: "公众号文章",
    icon: "newspaper",
    description: "生动有感染力，适合手机阅读",
    defaultTitle: "未命名文章",
    allowProactiveSearch: true,
    allowOnDemandSearch: true,
    allowSandbox: false,
    allowMobilePreview: true,
    exports: ["pdf"],
    showVoicePanel: false,
    showDesignPanel: true,
    supportFromScratch: true,
  },
  case: {
    label: "商业案例",
    icon: "briefcase",
    description: "数据驱动，逻辑严密",
    defaultTitle: "未命名案例",
    allowProactiveSearch: true,
    allowOnDemandSearch: true,
    allowSandbox: false,
    allowMobilePreview: false,
    exports: ["pdf"],
    showVoicePanel: false,
    showDesignPanel: true,
    supportFromScratch: true,
  },
  script: {
    label: "视频脚本",
    icon: "video",
    description: "口语化，分场景，节奏感",
    defaultTitle: "未命名脚本",
    allowProactiveSearch: false,
    allowOnDemandSearch: true,
    allowSandbox: false,
    allowMobilePreview: false,
    exports: ["pdf"],
    showVoicePanel: true,
    showDesignPanel: false,
    supportFromScratch: true,
  },
  podcast: {
    label: "语音博客文稿",
    icon: "mic",
    description: "对话感，适合朗读",
    defaultTitle: "未命名文稿",
    allowProactiveSearch: false,
    allowOnDemandSearch: false,
    allowSandbox: false,
    allowMobilePreview: false,
    exports: ["pdf"],
    showVoicePanel: true,
    showDesignPanel: false,
    supportFromScratch: true,
  },
  course: {
    label: "技术分享课程",
    icon: "graduation-cap",
    description: "严谨，结构化，含代码示例",
    defaultTitle: "未命名课程",
    allowProactiveSearch: false,
    allowOnDemandSearch: true,
    allowSandbox: true,
    allowMobilePreview: false,
    exports: ["ppt", "pdf"],
    showVoicePanel: true,
    showDesignPanel: true,
    supportFromScratch: true,
  },
  general: {
    label: "通用写作",
    icon: "file-text",
    description: "自适应风格",
    defaultTitle: "未命名文档",
    allowProactiveSearch: false,
    allowOnDemandSearch: true,
    allowSandbox: false,
    allowMobilePreview: false,
    exports: ["pdf"],
    showVoicePanel: false,
    showDesignPanel: false,
    supportFromScratch: true,
  },
};

export const DOC_TYPE_ORDER: DocType[] = [
  "article",
  "case",
  "script",
  "podcast",
  "course",
  "general",
];
