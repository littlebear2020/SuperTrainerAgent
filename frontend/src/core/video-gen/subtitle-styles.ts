/**
 * 字幕样式共享常量——创建对话框与详情页同源（2026-07-31 T7）。
 *
 * T10（2026-08-13）：previewClassName 近似 ASS 渲染效果（思源黑体 + 描边 + 最多两行）。
 * 实际烧录使用 PlayRes 对齐的 ASS（WrapStyle=2，内置 \N 预折行），
 * 这里用 CSS 近似仅作预览参考。
 *
 * font-family 链：浏览器若有思源黑体/Noto Sans CJK 则用，否则回落到系统 CJK。
 */

/** CJK 字体栈——近似 ASS 渲染的 Noto Sans CJK SC */
const CJK_FONT =
  "[font-family:'Noto_Sans_CJK_SC','Source_Han_Sans_SC','PingFang_SC','Microsoft_YaHei',sans-serif]";

export const SUBTITLE_STYLES = [
  {
    value: "default",
    label: "标准",
    // 2026-08-20：后端倍率 default 1.0→1.2（16/17 ≈ 1.2/1.25），预览同步
    previewClassName: `text-[16px] font-medium text-white leading-tight ${CJK_FONT} [text-shadow:0_0_3px_#000,0_0_3px_#000]`,
  },
  {
    value: "bold",
    label: "粗体大字",
    previewClassName: `text-[17px] font-bold text-white leading-tight ${CJK_FONT} [text-shadow:0_0_4px_#000,0_0_4px_#000]`,
  },
  {
    value: "minimal",
    label: "极简小字",
    // 2026-08-20：后端倍率 minimal 0.75→0.95（13/17 ≈ 0.95/1.25），预览同步
    previewClassName: `text-[13px] font-normal text-white leading-tight ${CJK_FONT} [text-shadow:0_0_2px_#000]`,
  },
  {
    value: "outline",
    label: "粗描边",
    // 2026-08-20：后端倍率 outline 1.15→1.25（与 bold 同号，用户拍板），预览同步
    previewClassName: `text-[17px] font-bold text-white leading-tight ${CJK_FONT} [text-shadow:0_0_1px_#000,0_1px_2px_#000,1px_0_2px_#000,-1px_0_2px_#000,0_-1px_2px_#000]`,
  },
  {
    value: "none",
    label: "无字幕",
    previewClassName: "text-[13px] text-muted-foreground italic",
  },
] as const;

export type SubtitleStyle = (typeof SUBTITLE_STYLES)[number]["value"];

export function isSubtitleStyle(v: unknown): v is SubtitleStyle {
  return SUBTITLE_STYLES.some((s) => s.value === v);
}

/** 展示用中文标签；未知值原样回显（不吞掉后端新增枚举）。 */
export function subtitleStyleLabel(v: string | undefined | null): string {
  if (!v) return "标准";
  return SUBTITLE_STYLES.find((s) => s.value === v)?.label ?? v;
}

export function subtitleStylePreviewClass(
  v: string | undefined | null,
): string {
  return (
    SUBTITLE_STYLES.find((s) => s.value === v)?.previewClassName ??
    SUBTITLE_STYLES[0].previewClassName
  );
}
