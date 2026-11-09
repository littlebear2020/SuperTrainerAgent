"use client";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { REMOTION_LAYOUTS } from "@/core/video-gen/types";

/**
 * T7（2026-09-08 remotion-course）：帧版式切换器（19 版式下拉）。
 * 仅 remotion_course 任务显示；写回帧 layout 字段（D14 优先级链的用户手选端）。
 * null 值 = "自动"（planner hint / 确定性规则表接管）。
 */
const AUTO_VALUE = "__auto__";

interface LayoutSwitcherProps {
  value: string | null | undefined;
  onChange: (value: string | null) => void;
  disabled?: boolean;
}

export function LayoutSwitcher({
  value,
  onChange,
  disabled,
}: LayoutSwitcherProps) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-muted-foreground shrink-0 text-xs font-medium">
        画面版式
      </span>
      <Select
        value={value ?? AUTO_VALUE}
        onValueChange={(v) => onChange(v === AUTO_VALUE ? null : v)}
        disabled={disabled}
      >
        <SelectTrigger className="h-7 w-[136px] text-xs">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={AUTO_VALUE} className="text-xs">
            自动（推荐）
          </SelectItem>
          {REMOTION_LAYOUTS.map((l) => (
            <SelectItem key={l.value} value={l.value} className="text-xs">
              {l.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
