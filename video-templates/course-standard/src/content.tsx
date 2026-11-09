/**
 * 场景内容契约（DataDrivenCourse 的 scene.layoutProps 形状）。
 * gateway 侧 remotion_course_props.py 按 D13/D14 产出该结构；
 * 缺字段一律安全降级（空串/空数组），绝不炸渲染。
 */
import React from "react";
import { ThemeName } from "./theme";
import { SceneVisualSpec } from "./course-props";

export interface ChartDatum {
  label: string;
  value: number;
}

export interface StepItem {
  name: string;
  desc: string;
}

export interface QuizOption {
  key: string;
  text: string;
}

export interface CaseCard {
  label: string;
  body: string;
  metric?: string;
}

export interface SceneContent {
  /** 页面主标题（Header title / 大标题） */
  title?: string;
  /** Header 小标签（如 "CONCEPT · 概念讲解"） */
  kicker?: string;
  /** 副标题（封面副标题 / FullVisual 底部条副题） */
  subtitle?: string;
  /** 引导语（概念页 lead 等） */
  lead?: string;
  /** 要点列表（Concept/Objectives/Summary/ImageText/Code 侧栏） */
  bullets?: string[];
  /** 大纲章节列表（Outline） */
  chapters?: string[];
  /** 当前章（ChapterDivider，1 基） */
  chapterNo?: number;
  chapterTotal?: number;
  /** Outline 高亮章（0 基） */
  currentChapter?: number;
  /** 流程步骤（Process） */
  steps?: StepItem[];
  /** 当前步（Process，0 基） */
  currentStep?: number;
  /** 图表数据（Chart） */
  chart?: ChartDatum[];
  /** 图表结论区大数字（如 "42% → 92%"） */
  headline?: string;
  conclusion?: string;
  note?: string;
  /** 对比页（Comparison） */
  leftLabel?: string;
  rightLabel?: string;
  left?: string[];
  right?: string[];
  /** 案例三段卡（Case） */
  cards?: CaseCard[];
  /** 代码页（Code） */
  fileName?: string;
  code?: string;
  /** 金句页（Quote）。富文本标记见 renderRichText */
  quote?: string;
  source?: string;
  /** 思考题（Quiz） */
  question?: string;
  options?: QuizOption[];
  /** 结尾行动页（Ending） */
  recap?: string;
  nextLabel?: string;
  nextTitle?: string;
  nextSubtitle?: string;
  cta?: string;
  /** 封面（Cover） */
  series?: string;
  courseNo?: string;
  instructor?: { initial: string; name: string; title: string };
  /** 视频页徽标（VideoClip，如 "视频 · 02:36"） */
  badge?: string;
  /** 片尾（EndCard） */
  brandName?: string;
  follow?: string;
  copyright?: string;
}

/* ── 富文本标记（保持数据为平面字符串）────────────────────────
 * `**文字**` → 主文字色加粗（CasePage 正文强调）
 * `{a}文字{/a}` → 强调色（QuotePage accent span）
 * ────────────────────────────────────────────────────────── */

export interface RichTextTheme {
  text: string;
  textSecondary: string;
  accent: string;
}

export const renderRichText = (
  text: string,
  theme: RichTextTheme,
  baseColor: string,
): React.ReactNode[] => {
  // 先按换行拆（保留为 <br/> 语义由调用方 lineHeight 承接），再拆内联标记
  const lines = text.split("\n");
  const out: React.ReactNode[] = [];
  lines.forEach((line, li) => {
    const parts = line.split(/(\*\*[^*]+\*\*|\{a\}[^{]*\{\/a\})/g).filter(Boolean);
    parts.forEach((part, pi) => {
      if (part.startsWith("**") && part.endsWith("**")) {
        out.push(
          <span key={`${li}-${pi}`} style={{ color: theme.text, fontWeight: 700 }}>
            {part.slice(2, -2)}
          </span>,
        );
      } else if (part.startsWith("{a}") && part.endsWith("{/a}")) {
        out.push(
          <span key={`${li}-${pi}`} style={{ color: theme.accent }}>
            {part.slice(3, -4)}
          </span>,
        );
      } else {
        out.push(
          <span key={`${li}-${pi}`} style={{ color: baseColor }}>
            {part}
          </span>,
        );
      }
    });
    if (li < lines.length - 1) {
      out.push(<br key={`br-${li}`} />);
    }
  });
  return out;
};

/** 布局组件统一收到的渲染上下文（gallery 与数据驱动两种来源共用）。 */
export interface LayoutSceneProps {
  themeName: ThemeName;
  /** 场景文案（缺省安全降级） */
  content?: SceneContent;
  /** 视觉素材规格（版式按自己的槽位尺寸渲染 VisualSlot；缺省回退占位观感） */
  visualSpec?: SceneVisualSpec;
  /** 启用转场时为 true——版式禁用自带入场淡入淡出（T2 红线 2） */
  transitionsOn?: boolean;
  /** 品牌 LOGO URL（封面/片尾直接消费；内容场景由 BrandLayer 统一盖章） */
  logoUrl?: string;
  brandName?: string;
  /**
   * 版式内字幕条文案——仅画廊/静帧预览模式使用（保持 M0 视觉）。
   * 数据驱动成片模式不传（字幕由根部 SubtitleLayer 按全局帧渲染，防双重字幕）。
   */
  subtitleText?: string;
  /** Remotion 4.0.522 泛型约束：Composition props 须带索引签名 */
  [key: string]: unknown;
}
