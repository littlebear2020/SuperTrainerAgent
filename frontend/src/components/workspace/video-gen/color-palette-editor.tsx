"use client";

export interface ColorPalette {
  primary?: string;
  saturation?: number;
  contrast?: number;
  brightness?: number;
}

const PRESET_COLORS = [
  "#2C3E50",
  "#E74C3C",
  "#3498DB",
  "#2ECC71",
  "#F39C12",
  "#9B59B6",
  "#1ABC9C",
  "#E67E22",
];

export function ColorPaletteEditor({
  value,
  onChange,
}: {
  value: ColorPalette;
  onChange: (v: ColorPalette) => void;
}) {
  const {
    primary = "",
    saturation = 1.0,
    contrast = 1.0,
    brightness = 0.0,
  } = value;

  return (
    <div className="space-y-4">
      {/* Primary color */}
      <div className="space-y-2">
        <label className="text-sm font-medium">
          主色调（卡片+生图提示，非调色）
        </label>
        <div className="flex items-center gap-3">
          <input
            type="color"
            value={primary || "#2C3E50"}
            onChange={(e) => onChange({ ...value, primary: e.target.value })}
            className="border-border h-9 w-12 cursor-pointer rounded border"
          />
          <span className="text-muted-foreground font-mono text-xs">
            {primary || "未设置"}
          </span>
          {primary && (
            <button
              onClick={() => onChange({ ...value, primary: "" })}
              className="text-muted-foreground hover:text-destructive text-xs"
            >
              清除
            </button>
          )}
        </div>
        <div className="flex flex-wrap gap-1.5">
          {PRESET_COLORS.map((c) => (
            <button
              key={c}
              onClick={() => onChange({ ...value, primary: c })}
              className="border-border/50 h-6 w-6 rounded border transition-transform hover:scale-110"
              style={{ backgroundColor: c }}
              title={c}
            />
          ))}
        </div>
      </div>

      {/* Sliders */}
      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-1">
          <div className="flex items-center justify-between">
            <label className="text-xs font-medium">饱和度</label>
            <span className="text-muted-foreground font-mono text-xs">
              {saturation.toFixed(1)}
            </span>
          </div>
          <input
            type="range"
            min={0}
            max={2}
            step={0.1}
            value={saturation}
            onChange={(e) =>
              onChange({ ...value, saturation: parseFloat(e.target.value) })
            }
            className="w-full"
          />
        </div>
        <div className="space-y-1">
          <div className="flex items-center justify-between">
            <label className="text-xs font-medium">对比度</label>
            <span className="text-muted-foreground font-mono text-xs">
              {contrast.toFixed(1)}
            </span>
          </div>
          <input
            type="range"
            min={0}
            max={2}
            step={0.1}
            value={contrast}
            onChange={(e) =>
              onChange({ ...value, contrast: parseFloat(e.target.value) })
            }
            className="w-full"
          />
        </div>
        <div className="space-y-1">
          <div className="flex items-center justify-between">
            <label className="text-xs font-medium">亮度</label>
            <span className="text-muted-foreground font-mono text-xs">
              {brightness.toFixed(1)}
            </span>
          </div>
          <input
            type="range"
            min={-1}
            max={1}
            step={0.1}
            value={brightness}
            onChange={(e) =>
              onChange({ ...value, brightness: parseFloat(e.target.value) })
            }
            className="w-full"
          />
        </div>
      </div>
    </div>
  );
}
