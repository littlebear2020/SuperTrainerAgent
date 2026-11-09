"use client";

/**
 * video-gen 音频滑条控件（2026-10-06 模块内合并批）。
 *
 * 语速滑条三份（create 弹窗 / 详情页音频设置 / 项目设置）与 BGM 音量两份
 * （create 弹窗 / 详情页）的 input 本体逐字重复。收敛为控件级共享——
 * 头行（label 样式、「跟随」checkbox 及其文案/回落值）是三表面已确认的
 * 布局分叉，保留在调用方。
 */

/** 语速滑条：0.5–2.0x、步进 0.1；跟随态（null）禁用并显示「—」。 */
export function SpeedSliderControl({
  value,
  onChange,
}: {
  value: number | null;
  onChange: (v: number | null) => void;
}) {
  return (
    <div className="flex items-center gap-3">
      <input
        type="range"
        min={0.5}
        max={2.0}
        step={0.1}
        disabled={value === null}
        value={value ?? 1.0}
        onChange={(e) => onChange(Number(e.target.value))}
        className="bg-primary/20 h-2 flex-1 cursor-pointer appearance-none rounded-full accent-indigo-500 disabled:cursor-not-allowed disabled:opacity-40"
      />
      <span className="text-muted-foreground w-10 text-right text-xs">
        {value === null ? "—" : `${value.toFixed(1)}x`}
      </span>
    </div>
  );
}

/** BGM 音量滑条：0–100、步进 5。百分比展示行由调用方布局。 */
export function VolumeSliderControl({
  value,
  onChange,
}: {
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <input
      type="range"
      min={0}
      max={100}
      step={5}
      value={value}
      onChange={(e) => onChange(Number(e.target.value))}
      className="bg-primary/20 h-2 w-full cursor-pointer appearance-none rounded-full accent-indigo-500"
    />
  );
}
