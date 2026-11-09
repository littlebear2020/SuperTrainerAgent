/**
 * DataDrivenCourse 的 props 契约（设计文档 §5）。
 * gateway 侧 remotion_course_props.py 构建完整 props 随 payload 下发，worker 只消费。
 * ⚠️ Remotion 4.0.522 泛型约束：props 类型必须带索引签名 [key: string]: unknown。
 */

export interface CaptionWord {
  word: string;
  /** 全局毫秒（已折算场景起点偏移；帧换算 ms = frame / fps * 1000） */
  startMs: number;
  endMs: number;
  /**
   * T-B2（F1，2026-09-09 feedback-round1）：所属场景序号（DataDrivenCourse
   * flatMap 时注入）。字幕层据此实现 hold-until-next 限定当前场景内（词间隙
   * 保持在场、场景间转场窗让位）+ 行分包不跨场景拼行。缺省（存量 props）=
   * 无场景域（词间隙 hold 跨场景兜底，行为向前兼容）。
   */
  sceneIndex?: number;
}

export interface SceneVisualSpec {
  kind: "image" | "video" | "graphic" | "none";
  /** 相对 public/ 的路径（worker 渲染前 symlink task 素材到 public/assets），或 http(s) URL */
  path?: string;
  fit?: "contain" | "cover";
}

export interface SceneSpec {
  index: number;
  narration: string;
  audioFile?: string;
  durationInFrames: number;
  layout: string;
  layoutProps?: Record<string, unknown>;
  visual?: SceneVisualSpec;
  captions?: CaptionWord[];
}

export interface SubtitleStyleSpec {
  /** 字号（px），默认主题 type.subtitle */
  fontSize?: number;
  /** 逐词高亮开关（D6 默认 true） */
  karaokeEnabled?: boolean;
  /** 每行最大字符数（贪心装箱），默认 18 */
  maxCharsPerLine?: number;
  /**
   * T-E9（2026-09-09 feedback-round1）：字幕样式变体（三级链：任务显式 > 项目 >
   * 默认，gateway 侧解析后下发）。缺省 = "default"（字幕条）。
   * - default：深底字幕条（现状）
   * - bold：加大加粗、无底条（强调型）
   * - minimal：无底条、小字号、轻投影（极简型）
   * - outline：描边字、无底条（高对比可读型）
   * - none：无字幕（gateway 侧直接不产 captions，此值仅作显式声明）
   */
  variant?: "default" | "bold" | "minimal" | "outline" | "none";
}

export interface BrandSpec {
  /** 主题预设名（D12 默认 dark-blue） */
  themeName?: string;
  /** 颜色覆写（只覆盖给出的键） */
  colors?: Record<string, string | number>;
  fontFamily?: string;
  brandName?: string;
  /** LOGO 相对 public/ 路径；缺省用主题渐变占位徽标 */
  logoPath?: string;
  subtitleStyle?: SubtitleStyleSpec;
  /** 片头（精简 CoverPage 变体；不配则无片头） */
  intro?: { headline?: string; subtitle?: string; durationInFrames?: number };
  /** 片尾（精简 EndCard 变体；不配则无片尾） */
  outro?: { headline?: string; follow?: string; durationInFrames?: number };
}

export interface BgmSpec {
  enabled: boolean;
  volume: number;
  /** 相对 public/ 路径 */
  path?: string;
}

export interface CourseProps {
  title: string;
  fps: number;
  width: number;
  height: number;
  brand: BrandSpec;
  bgm?: BgmSpec;
  scenes: SceneSpec[];
  /** 渲染降级留痕（占位素材等），worker 侧只写不读——模板侧 console.warn 供日志 */
  [key: string]: unknown;
}
