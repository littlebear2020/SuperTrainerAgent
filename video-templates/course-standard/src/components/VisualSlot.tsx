import React from "react";
import { Img, OffthreadVideo, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { interpolate, spring } from "remotion";
import { Theme } from "../theme";
import { SceneVisualSpec } from "../course-props";
import { VisualPlaceholder } from "./VisualPlaceholder";

/**
 * 视觉素材槽（T2 VisualLayer 的版式内落点）。
 * - image → <Img> + spring 轻推运镜（替代 Ken Burns）
 * - video → <OffthreadVideo>（muted，画面素材不带自己的声音）
 * - graphic/none/素材缺失 → VisualPlaceholder 占位（不炸渲染，降级由
 *   DataDrivenCourse 侧记录 degradations）
 * path 一律相对 public/（worker 渲染前把 task 素材 symlink 到 public/assets）。
 */
export const VisualSlot: React.FC<{
  theme: Theme;
  spec: SceneVisualSpec | undefined;
  width: number | string;
  height: number | string;
  label?: string;
  note?: string;
  radius?: number;
  style?: React.CSSProperties;
}> = ({ theme, spec, width, height, label, note, radius = 24, style }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  if (!spec || !spec.path || spec.kind === "none" || spec.kind === "graphic") {
    return (
      <VisualPlaceholder theme={theme} width={width} height={height} label={label} note={note} radius={radius} style={style} />
    );
  }

  const src = spec.path.startsWith("http") ? spec.path : staticFile(spec.path);
  const objectFit = spec.fit === "contain" ? ("contain" as const) : ("cover" as const);
  // spring 轻推运镜：1.0 → 1.045，落定即静止（每场景重置，由 frame 驱动）
  const scale = spring({ frame, fps, config: { damping: 200, stiffness: 40, mass: 0.6 } });
  const zoom = interpolate(scale, [0, 1], [1.0, 1.045]);

  const boxStyle: React.CSSProperties = {
    width,
    height,
    borderRadius: radius,
    overflow: "hidden",
    flexShrink: 0,
    ...style,
  };

  if (spec.kind === "video") {
    return (
      <div style={boxStyle}>
        <OffthreadVideo
          src={src}
          muted
          style={{ width: "100%", height: "100%", objectFit }}
        />
      </div>
    );
  }
  return (
    <div style={boxStyle}>
      <Img
        src={src}
        style={{ width: "100%", height: "100%", objectFit, transform: `scale(${zoom})` }}
      />
    </div>
  );
};
