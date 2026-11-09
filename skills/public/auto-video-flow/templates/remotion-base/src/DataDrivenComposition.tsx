import { Fragment } from "react";
import {
  AbsoluteFill,
  Audio,
  Sequence,
  staticFile,
  useCurrentFrame,
  interpolate,
  Easing,
  getStaticFiles,
} from "remotion";
import {
  TransitionSeries,
  linearTiming,
} from "@remotion/transitions";
import type { TransitionPresentation } from "@remotion/transitions";
import { fade } from "@remotion/transitions/fade";
import { slide } from "@remotion/transitions/slide";
import { wipe } from "@remotion/transitions/wipe";
import { flip } from "@remotion/transitions/flip";
import { zoomInOut } from "@remotion/transitions/zoom-in-out";
import { iris } from "@remotion/transitions/iris";
import { KaraokeCaptions, WordCaption } from "./KaraokeCaptions";

/* ── Transition presets ──────────────────────────────────────── */

// 4.0.522 实测 API：presentation 走子路径导出，且不存在旧代码假设的
// zoom()/circle()——zoom_in 归一到 zoomInOut（官方缩放转场），circle 归一到
// iris（圆形遮罩揭示，即旧 circle wipe 的现代等价物，需传入画布宽高）。
// dissolve 语义≈交叉淡化，保持映射 fade（DOM 实现，无 shader 依赖）。
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyPresentation = TransitionPresentation<any>;

const buildTransitionMap = (
  width: number,
  height: number,
): Record<string, AnyPresentation> => ({
  fade: fade(),
  dissolve: fade(),
  slide_left: slide({ direction: "from-right" }),
  slide_right: slide({ direction: "from-left" }),
  wipe_left: wipe({ direction: "from-right" }),
  wipe_right: wipe({ direction: "from-left" }),
  flip: flip(),
  zoom_in: zoomInOut({}),
  circle: iris({ width, height }),
});

/* ── Props types ─────────────────────────────────────────────── */

interface Scene {
  index: number;
  narration: string;
  durationInFrames: number;
  audioFile: string;
}

interface SubtitleSegment {
  sceneIndex: number;
  startFrame: number;
  endFrame: number;
  text: string;
}

export interface DataDrivenVideoProps {
  // Composition/calculateMetadata 泛型约束要求 Record<string, unknown>（4.0.522）
  [key: string]: unknown;
  title: string;
  fps: number;
  width: number;
  height: number;
  totalDurationFrames: number;
  style: string;
  transition: string;
  transitionDurationFrames: number;
  bgmEnabled: boolean;
  bgmVolume: number;
  scenes: Scene[];
  subtitleSegments: SubtitleSegment[];
  // 可选：词级字幕（T10 karaoke，@remotion/captions Caption JSON 形状，
  // 全局毫秒时间轴）。传入时取代 subtitleSegments 的句级字幕。
  captions?: WordCaption[];
}

/* ── Scene component ─────────────────────────────────────────── */

const SceneView: React.FC<{
  scene: Scene;
  fps: number;
  softFade: boolean;
}> = ({ scene, fps, softFade }) => {
  const frame = useCurrentFrame();

  // softFade 仅用于无转场布局：入场 0.5s 淡入 + 出场 0.5s 淡出。
  // 有转场时场景衔接由 TransitionSeries 全权负责——若场景自身再淡出，
  // 转场窗口内出场场景已透明，交叉淡化会退化成"经背景色闪切"。
  const fadeInFrames = Math.round(fps * 0.5);
  const fadeOutFrames = Math.round(fps * 0.5);
  const holdEnd = scene.durationInFrames - fadeOutFrames;

  const opacity = !softFade
    ? 1
    : frame < fadeInFrames
      ? interpolate(frame, [0, fadeInFrames], [0, 1], {
          easing: Easing.out(Easing.quad),
        })
      : frame > holdEnd
        ? interpolate(frame, [holdEnd, scene.durationInFrames], [1, 0], {
            easing: Easing.in(Easing.quad),
          })
        : 1;

  // Gentle scale-in
  const scale =
    softFade && frame < fadeInFrames
      ? interpolate(frame, [0, fadeInFrames], [0.95, 1], {
          easing: Easing.out(Easing.quad),
        })
      : 1;

  return (
    <AbsoluteFill
      style={{
        backgroundColor: "#0f172a",
        color: "white",
        justifyContent: "center",
        alignItems: "center",
        padding: 60,
      }}
    >
      {/* Narration text */}
      <div
        style={{
          fontSize: 42,
          lineHeight: 1.6,
          textAlign: "center",
          opacity,
          transform: `scale(${scale})`,
          fontFamily: "'PingFang SC', 'Microsoft YaHei', 'Noto Sans SC', sans-serif",
        }}
      >
        {scene.narration}
      </div>

      {/* Scene audio */}
      {scene.audioFile && (
        <Audio src={staticFile(scene.audioFile)} volume={1} />
      )}
    </AbsoluteFill>
  );
};

/* ── Subtitle overlay ────────────────────────────────────────── */

// 2026-09-08 修复：本组件必须挂在 Composition 根部（<Sequence> 之外），
// 此时 useCurrentFrame() 返回全局帧，与 SRT 全局帧契约直接匹配。
// 原实现嵌在 <Sequence> 内却按全局帧匹配——Sequence 内 frame 从 0 重计，
// 第 2 场景起字幕必消失。sceneStartFrame prop 旧代码传了但从未使用，已删除。
const SubtitleOverlay: React.FC<{
  segments: SubtitleSegment[];
}> = ({ segments }) => {
  const frame = useCurrentFrame();

  // Find the segment that matches the current global frame
  const active = segments.find(
    (seg) => frame >= seg.startFrame && frame < seg.endFrame,
  );

  if (!active) return null;

  // Fade effect
  const fadeIn = interpolate(
    frame,
    [active.startFrame, active.startFrame + 6],
    [0, 1],
  );
  const fadeOut = interpolate(
    frame,
    [active.endFrame - 6, active.endFrame],
    [1, 0],
  );
  const opacity = Math.min(fadeIn, fadeOut);

  return (
    <AbsoluteFill
      style={{
        justifyContent: "flex-end",
        alignItems: "center",
        paddingBottom: 80,
      }}
    >
      <div
        style={{
          backgroundColor: "rgba(0, 0, 0, 0.7)",
          color: "white",
          fontSize: 36,
          lineHeight: 1.4,
          padding: "8px 20px",
          borderRadius: 8,
          textAlign: "center",
          opacity,
          fontFamily: "'PingFang SC', 'Microsoft YaHei', 'Noto Sans SC', sans-serif",
        }}
      >
        {active.text}
      </div>
    </AbsoluteFill>
  );
};

/* ── Main composition ────────────────────────────────────────── */

export const DataDrivenVideo: React.FC<DataDrivenVideoProps> = (props) => {
  const {
    scenes,
    subtitleSegments,
    fps,
    width,
    height,
    transition,
    transitionDurationFrames,
    bgmEnabled,
    bgmVolume,
  } = props;

  const td = Math.max(0, transitionDurationFrames || 0);
  const hasTransition =
    Boolean(transition) && transition !== "none" && scenes.length > 1 && td > 0;
  const presentation = hasTransition
    ? (buildTransitionMap(width || 1920, height || 1080)[transition] ?? fade())
    : null;

  // bgmEnabled=true 但 public/bgm.mp3 缺失时 staticFile 会在渲染期抛错——
  // 无文件则静默降级不渲染 BGM（2026-09-08 T1）。
  const bgmExists = getStaticFiles().some((f) => f.name === "bgm.mp3");

  let cumFrame = 0;

  return (
    <>
      {bgmEnabled && bgmExists && (
        <Audio src={staticFile("bgm.mp3")} volume={bgmVolume} loop />
      )}
      <AbsoluteFill style={{ backgroundColor: "#0f172a" }}>
        {presentation ? (
          // 真转场（2026-09-08 T1）：TransitionSeries 让 TRANSITION_MAP 的
          // 9 种预设真实生效，取代原 TransitionWrapper 的 CSS 透明度假转场。
          // 关键不变量：转场重叠的 td 帧由前一场景的延展时长吸收（末场景不
          // 延展），使场景 k 起点恒为 Σduration[0..k-1]——与后端
          // remotion_props 产出的 SRT 全局帧 / 拼接音轨逐帧对齐。
          <TransitionSeries>
            {scenes.map((scene, idx) => {
              const isLast = idx === scenes.length - 1;
              const seqDuration = scene.durationInFrames + (isLast ? 0 : td);
              return (
                <Fragment key={scene.index}>
                  <TransitionSeries.Sequence durationInFrames={seqDuration}>
                    <SceneView scene={scene} fps={fps} softFade={false} />
                  </TransitionSeries.Sequence>
                  {!isLast && (
                    <TransitionSeries.Transition
                      presentation={presentation}
                      timing={linearTiming({ durationInFrames: td })}
                    />
                  )}
                </Fragment>
              );
            })}
          </TransitionSeries>
        ) : (
          // 无转场：简单 Sequence 布局，保留场景自身淡入淡出
          scenes.map((scene) => {
            const from = cumFrame;
            cumFrame += scene.durationInFrames;
            return (
              <Sequence
                key={scene.index}
                from={from}
                durationInFrames={scene.durationInFrames}
              >
                <SceneView scene={scene} fps={fps} softFade />
              </Sequence>
            );
          })
        )}
        {/* 字幕层：根部全局帧渲染（修复前嵌在各场景 Sequence 内，第 2 场景起必消失）。
            传入词级 captions 时用 karaoke 高亮字幕，否则句级 SRT 字幕。 */}
        {props.captions && props.captions.length > 0 ? (
          <KaraokeCaptions captions={props.captions} fps={fps} />
        ) : (
          <SubtitleOverlay segments={subtitleSegments} />
        )}
      </AbsoluteFill>
    </>
  );
};
