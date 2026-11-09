/**
 * 课程视频版式设计令牌（design tokens）· 多主题字典。
 * 这些字段就是将来品牌包注入的位置：换品牌/换主题 = 换这一份配置。
 *
 * 语义令牌约定：
 * - bgDeep（代码块/视频区）与 subtitleBg（字幕条）在全部主题下保持深色，
 *   浅色主题上字幕条依然用深底浅字，保证可读性。
 * - card / line / visual* 等按明暗分别适配。
 */

export type ThemeName = "dark-blue" | "light" | "dark-green" | "warm";

/** 每个版式组件的注入参数（经 Composition defaultProps 注入）。 */
export type LayoutProps = { themeName: ThemeName };

export interface ThemeColors {
  /** 主色 */
  primary: string;
  /** 主色深色端（渐变） */
  primaryDeep: string;
  /** 强调色 */
  accent: string;
  /** 强调色深色端（渐变） */
  accentDeep: string;
  /** 页面背景 */
  bg: string;
  /** 抬升面 */
  bgElevated: string;
  /** 更深底（代码块/视频占位区，四主题统一深色） */
  bgDeep: string;
  text: string;
  textSecondary: string;
  textDim: string;
  line: string;
  success: string;
  danger: string;
  /** 主色实心块上的文字色 */
  onPrimary: string;
  /** 强调色实心块上的文字色 */
  onAccent: string;
  /** 普通卡片底 */
  card: string;
  /** 字幕条（浅底下仍为深底浅字） */
  subtitleBg: string;
  subtitleBorder: string;
  subtitleText: string;
  /** 占位素材块渐变三档 */
  visualFrom: string;
  visualVia: string;
  visualTo: string;
  /** 全幅视觉页蓝图网格线 */
  gridLine: string;
  /** 全幅视觉页底部标题条渐变终点（深色遮罩） */
  overlayTo: string;
  /** 背景光晕透明度 */
  glowPrimary: number;
  glowAccent: number;
}

export interface Theme {
  name: ThemeName;
  label: string;
  isLight: boolean;
  colors: ThemeColors;
  fontFamily: string;
  serifFamily: string;
  monoFamily: string;
  /** 安全区：底部 160px 字幕预留、左右边距 60px */
  safe: { side: number; bottom: number };
  type: {
    pageTitle: number;
    sectionTitle: number;
    body: number;
    bodySmall: number;
    subtitle: number;
  };
}

const base = {
  fontFamily: '"PingFang SC","Microsoft YaHei","Noto Sans SC",sans-serif',
  serifFamily: 'Georgia,"Songti SC","STSong",serif',
  monoFamily: '"SF Mono","JetBrains Mono","Fira Code",Consolas,monospace',
  safe: { side: 60, bottom: 160 },
  type: {
    pageTitle: 72,
    sectionTitle: 56,
    body: 34,
    bodySmall: 30,
    subtitle: 36,
  },
} as const;

/** hex 颜色 + 透明度 → rgba() 字符串 */
export const alpha = (hex: string, a: number): string => {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r}, ${g}, ${b}, ${a})`;
};

export const THEMES: Record<ThemeName, Theme> = {
  "dark-blue": {
    ...base,
    name: "dark-blue",
    label: "深空蓝",
    isLight: false,
    colors: {
      primary: "#3B82F6",
      primaryDeep: "#1D4ED8",
      accent: "#F59E0B",
      accentDeep: "#B45309",
      bg: "#0F172A",
      bgElevated: "#1E293B",
      bgDeep: "#0B1220",
      text: "#F8FAFC",
      textSecondary: "#94A3B8",
      textDim: "#64748B",
      line: "#334155",
      success: "#34D399",
      danger: "#F87171",
      onPrimary: "#FFFFFF",
      onAccent: "#0F172A",
      card: "rgba(30, 41, 59, 0.55)",
      subtitleBg: "rgba(2, 6, 23, 0.78)",
      subtitleBorder: "#334155",
      subtitleText: "#F8FAFC",
      visualFrom: "#1E293B",
      visualVia: "#24365E",
      visualTo: "#3B82F6",
      gridLine: "rgba(148, 163, 184, 0.07)",
      overlayTo: "rgba(2, 6, 23, 0.92)",
      glowPrimary: 0.14,
      glowAccent: 0.08,
    },
  },
  light: {
    ...base,
    name: "light",
    label: "明亮浅色",
    isLight: true,
    colors: {
      primary: "#2563EB",
      primaryDeep: "#1E40AF",
      accent: "#EA580C",
      accentDeep: "#9A3412",
      bg: "#FAFAF7",
      bgElevated: "#FFFFFF",
      bgDeep: "#1E293B",
      text: "#1E293B",
      textSecondary: "#64748B",
      textDim: "#94A3B8",
      line: "#E2E8F0",
      success: "#059669",
      danger: "#DC2626",
      onPrimary: "#FFFFFF",
      onAccent: "#FFFFFF",
      card: "#FFFFFF",
      subtitleBg: "rgba(15, 23, 42, 0.88)",
      subtitleBorder: "rgba(148, 163, 184, 0.35)",
      subtitleText: "#F8FAFC",
      visualFrom: "#F1F5F9",
      visualVia: "#DBEAFE",
      visualTo: "#93C5FD",
      gridLine: "rgba(15, 23, 42, 0.06)",
      overlayTo: "rgba(15, 23, 42, 0.9)",
      glowPrimary: 0.1,
      glowAccent: 0.06,
    },
  },
  "dark-green": {
    ...base,
    name: "dark-green",
    label: "墨绿沉稳",
    isLight: false,
    colors: {
      primary: "#10B981",
      primaryDeep: "#047857",
      accent: "#F59E0B",
      accentDeep: "#B45309",
      bg: "#0B1F1A",
      bgElevated: "#122B23",
      bgDeep: "#071510",
      text: "#F0FDF4",
      textSecondary: "#9FBDB0",
      textDim: "#5F7F72",
      line: "#1F3D33",
      success: "#34D399",
      danger: "#F87171",
      onPrimary: "#052E1B",
      onAccent: "#0B1F1A",
      card: "rgba(18, 43, 35, 0.55)",
      subtitleBg: "rgba(4, 15, 11, 0.8)",
      subtitleBorder: "#1F3D33",
      subtitleText: "#F0FDF4",
      visualFrom: "#122B23",
      visualVia: "#134E3F",
      visualTo: "#10B981",
      gridLine: "rgba(167, 243, 208, 0.06)",
      overlayTo: "rgba(4, 15, 11, 0.92)",
      glowPrimary: 0.13,
      glowAccent: 0.07,
    },
  },
  warm: {
    ...base,
    name: "warm",
    label: "暖色纸感",
    isLight: true,
    colors: {
      primary: "#C2410C",
      primaryDeep: "#7C2D12",
      accent: "#1D4ED8",
      accentDeep: "#1E3A8A",
      bg: "#F5F0E8",
      bgElevated: "#FFFCF7",
      bgDeep: "#1C1917",
      text: "#292524",
      textSecondary: "#78716C",
      textDim: "#A8A29E",
      line: "#E7E0D4",
      success: "#047857",
      danger: "#B91C1C",
      onPrimary: "#FFFFFF",
      onAccent: "#FFFFFF",
      card: "#FFFCF7",
      subtitleBg: "rgba(28, 25, 23, 0.88)",
      subtitleBorder: "rgba(168, 162, 158, 0.4)",
      subtitleText: "#FAFAF9",
      visualFrom: "#FAF6EF",
      visualVia: "#F5E7D3",
      visualTo: "#FDBA74",
      gridLine: "rgba(41, 37, 36, 0.06)",
      overlayTo: "rgba(28, 25, 23, 0.9)",
      glowPrimary: 0.1,
      glowAccent: 0.07,
    },
  },
};

export const THEME_NAMES: ThemeName[] = ["dark-blue", "light", "dark-green", "warm"];

/** 品牌包颜色覆写（brand.colors）：只覆盖给出的键，其余沿用主题默认。 */
export const resolveTheme = (
  themeName: ThemeName,
  overrides?: Partial<ThemeColors>,
  fontFamily?: string,
): Theme => {
  const base = THEMES[themeName] ?? THEMES["dark-blue"]; // D12：缺省深空蓝
  if (!overrides && !fontFamily) return base;
  return {
    ...base,
    colors: { ...base.colors, ...(overrides ?? {}) },
    fontFamily: fontFamily ?? base.fontFamily,
  };
};
