"use client";

import { useCallback, useState } from "react";

import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

export interface SegmentData {
  id: string;
  text: string;
  originalText: string;
}

interface SegmentCardProps {
  index: number;
  segment: SegmentData;
  onChange: (id: string, text: string) => void;
  onRemove: (id: string) => void;
}

export function SegmentCard({
  index,
  segment,
  onChange,
  onRemove,
}: SegmentCardProps) {
  const { t } = useI18n();
  const vt = t.voiceTwin;
  const [collapsed, setCollapsed] = useState(false);
  const isModified = segment.text !== segment.originalText;

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      onChange(segment.id, e.target.value);
    },
    [segment.id, onChange],
  );

  return (
    <div
      className={cn(
        "rounded-lg border px-3 py-2 transition-colors",
        isModified
          ? "border-indigo-500/30 bg-indigo-500/5"
          : "border-white/5 bg-white/[0.02]",
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between">
        <button
          type="button"
          onClick={() => setCollapsed(!collapsed)}
          className="flex items-center gap-1.5 text-xs font-medium text-slate-400"
        >
          <span
            className={cn(
              "inline-block h-1.5 w-1.5 rounded-full",
              isModified ? "bg-indigo-500" : "bg-slate-400",
            )}
          />
          {index + 1}
          <span className="text-slate-500">
            ({segment.text.length} {vt.charsUnit ?? "字"})
          </span>
        </button>
        <button
          type="button"
          onClick={() => onRemove(segment.id)}
          className="text-xs text-slate-500 transition-colors hover:text-red-400"
        >
          {vt.removeSegment ?? "删除"}
        </button>
      </div>

      {/* Text area */}
      {!collapsed && (
        <textarea
          value={segment.text}
          onChange={handleChange}
          rows={3}
          className={cn(
            "mt-2 w-full resize-none rounded-md border px-2 py-1.5 text-xs outline-none",
            "border-white/10 bg-white/5 placeholder:text-slate-500",
            "focus:border-indigo-500/40 focus:ring-1 focus:ring-indigo-500/20",
          )}
        />
      )}
    </div>
  );
}
