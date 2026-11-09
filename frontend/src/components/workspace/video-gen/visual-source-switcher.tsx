"use client";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

/**
 * graphic-native-text（2026-08-17）：graphic 独立卡片渲染退役——数据/流程镜的文字
 * 由生图模型直接画进画面（AI 原生文字，风格构造性统一）。切换器不再提供 graphic
 * 新选项；存量 graphic 帧仅展示（含义=图内文字镜，可切走）。
 */
const VISUAL_SOURCES = [
  { value: "ai_image", label: "AI 生图" },
  { value: "ai_video", label: "AI 生视频" },
] as const;

const LEGACY_VALUES: Record<string, string> = {
  graphic: "图文镜（图内文字）",
  remotion: "文字动效（旧）",
};

/**
 * 选项集（纯函数便于单测）：新任务三选（ai_image/ai_video/graphic）；
 * 当前值为存量 remotion 时追加只读展示项在首位（保证当前值可显示、可切换走）。
 */
export function visualSourceOptions(currentValue?: string | null) {
  const legacy = currentValue ? LEGACY_VALUES[currentValue] : undefined;
  return legacy
    ? [{ value: currentValue, label: legacy }, ...VISUAL_SOURCES]
    : VISUAL_SOURCES;
}

interface VisualSourceSwitcherProps {
  value: string | null | undefined;
  onChange: (value: string) => void;
  disabled?: boolean;
}

export function VisualSourceSwitcher({
  value,
  onChange,
  disabled,
}: VisualSourceSwitcherProps) {
  const options = visualSourceOptions(value);
  // T-E8（R23）：未知值（如路由归一前的 "remotion_course" 窗口值）不在选项集内
  // ——Select 显示空白。兜底显示 ai_image（visual_routing 的缺省路由），选其他
  // 值时正常写回；已知值（含 legacy graphic/remotion）原样显示。
  const known =
    value != null && options.some((o) => o.value === value)
      ? value
      : "ai_image";
  return (
    <div className="flex items-center gap-2">
      <span className="text-muted-foreground shrink-0 text-xs font-medium">
        视觉来源
      </span>
      <Select value={known} onValueChange={onChange} disabled={disabled}>
        <SelectTrigger className="h-7 w-[120px] text-xs">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((s) => (
            <SelectItem key={s.value} value={s.value} className="text-xs">
              {s.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
