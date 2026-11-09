import React from "react";
import { LayoutSceneProps } from "./content";
import { CoverPage } from "./layouts/CoverPage";
import { ObjectivesPage } from "./layouts/ObjectivesPage";
import { OutlinePage } from "./layouts/OutlinePage";
import { ChapterDivider } from "./layouts/ChapterDivider";
import { ConceptPage } from "./layouts/ConceptPage";
import { ImageTextLeft } from "./layouts/ImageTextLeft";
import { ImageTextRight } from "./layouts/ImageTextRight";
import { FullVisualPage } from "./layouts/FullVisualPage";
import { VideoClipPage } from "./layouts/VideoClipPage";
import { ComparisonPage } from "./layouts/ComparisonPage";
import { ChartPage } from "./layouts/ChartPage";
import { ProcessPage } from "./layouts/ProcessPage";
import { CodePage } from "./layouts/CodePage";
import { QuotePage } from "./layouts/QuotePage";
import { CasePage } from "./layouts/CasePage";
import { SummaryPage } from "./layouts/SummaryPage";
import { QuizPage } from "./layouts/QuizPage";
import { EndingPage } from "./layouts/EndingPage";
import { EndCard } from "./layouts/EndCard";

export type LayoutComponent = React.FC<LayoutSceneProps>;

/** 19 版式注册表（LayoutRouter）。未知 layout → ConceptPage 兜底 + 记录。 */
export const LAYOUT_REGISTRY: Record<string, LayoutComponent> = {
  CoverPage,
  ObjectivesPage,
  OutlinePage,
  ChapterDivider,
  ConceptPage,
  ImageTextLeft,
  ImageTextRight,
  FullVisualPage,
  VideoClipPage,
  ComparisonPage,
  ChartPage,
  ProcessPage,
  CodePage,
  QuotePage,
  CasePage,
  SummaryPage,
  QuizPage,
  EndingPage,
  EndCard,
};

export const LAYOUT_IDS = Object.keys(LAYOUT_REGISTRY);

export const resolveLayout = (layout: string): { component: LayoutComponent; fallback: boolean } => {
  if (layout in LAYOUT_REGISTRY) {
    return { component: LAYOUT_REGISTRY[layout], fallback: false };
  }
  // 未知版式降级 ConceptPage（不炸渲染）；调用方负责 console.warn 留痕
  return { component: ConceptPage, fallback: true };
};
