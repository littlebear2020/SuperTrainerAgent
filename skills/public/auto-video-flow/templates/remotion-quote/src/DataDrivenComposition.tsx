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

export interface QuoteVideoProps {
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
  // Template params
  quote_char?: string;
  bg_color?: string;
}

/* ── Scene component ─────────────────────────────────────────── */

const QuoteSceneView: React.FC<{
  scene: Scene;
  fps: number;
  quoteChar: string;
}> = ({ scene, fps, quoteChar }) => {
  const frame = useCurrentFrame();

  const fadeInFrames = Math.round(fps * 0.6);
  const fadeOutFrames = Math.round(fps * 0.4);
  const holdEnd = scene.durationInFrames - fadeOutFrames;

  const opacity =
    frame < fadeInFrames
      ? interpolate(frame, [0, fadeInFrames], [0, 1], { easing: Easing.out(Easing.cubic) })
      : frame > holdEnd
        ? interpolate(frame, [holdEnd, scene.durationInFrames], [1, 0], { easing: Easing.in(Easing.cubic) })
        : 1;

  const translateY =
    frame < fadeInFrames
      ? interpolate(frame, [0, fadeInFrames], [20, 0], { easing: Easing.out(Easing.cubic) })
      : 0;

  return (
    <AbsoluteFill style={{ justifyContent: "center", alignItems: "center", padding: 80 }}>
      {/* Opening quote mark */}
      <div style={{
        fontSize: 120,
        lineHeight: 1,
        opacity: opacity * 0.3,
        position: "absolute",
        top: 80,
        left: 60,
        fontFamily: "Georgia, serif",
        color: "white",
      }}>
        {quoteChar}
      </div>

      {/* Narration text */}
      <div style={{
        fontSize: 36,
        lineHeight: 2,
        textAlign: "center",
        color: "white",
        opacity,
        transform: `translateY(${translateY}px)`,
        fontFamily: "'PingFang SC', 'Microsoft YaHei', 'Noto Sans SC', sans-serif",
        fontWeight: 500,
        letterSpacing: 2,
        maxWidth: "80%",
      }}>
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

const SubtitleOverlay: React.FC<{
  segments: SubtitleSegment[];
}> = ({ segments }) => {
  const frame = useCurrentFrame();

  const active = segments.find(
    (seg) => frame >= seg.startFrame && frame < seg.endFrame,
  );

  if (!active) return null;

  const fadeIn = interpolate(frame, [active.startFrame, active.startFrame + 6], [0, 1]);
  const fadeOut = interpolate(frame, [active.endFrame - 6, active.endFrame], [1, 0]);
  const opacity = Math.min(fadeIn, fadeOut);

  return (
    <AbsoluteFill style={{ justifyContent: "flex-end", alignItems: "center", paddingBottom: 60 }}>
      <div style={{
        color: "rgba(255,255,255,0.7)",
        fontSize: 22,
        lineHeight: 1.4,
        padding: "4px 16px",
        textAlign: "center",
        opacity,
        fontFamily: "'PingFang SC', 'Microsoft YaHei', 'Noto Sans SC', sans-serif",
      }}>
        {active.text}
      </div>
    </AbsoluteFill>
  );
};

/* ── Main composition ────────────────────────────────────────── */

export const DataDrivenVideo: React.FC<QuoteVideoProps> = (props) => {
  // transition/transitionDurationFrames 仍由后端 props 传入但不再消费：
  // 旧实现只做时长扣减（场景提前 td 帧、音轨重叠）而没有任何视觉转场效果，
  // 是纯 bug。2026-09-08 起场景排布对齐 remotion-base 不变量——场景 k 起点
  // 恒为 Σduration[0..k-1]，与 SRT 全局帧 / 拼接音轨逐帧对齐（音轨零重叠）。
  const {
    scenes, subtitleSegments, fps,
    bgmEnabled, bgmVolume, quote_char, bg_color,
  } = props;

  const quoteChar = quote_char || "\"";
  const bgColor = bg_color || "#1E293B";

  // bgmEnabled=true 但 public/bgm.mp3 缺失时 staticFile 会在渲染期抛错——
  // 无文件则静默降级不渲染 BGM（2026-09-08 修复，同 remotion-base）。
  const bgmExists = getStaticFiles().some((f) => f.name === "bgm.mp3");

  let cumFrame = 0;

  return (
    <>
      {bgmEnabled && bgmExists && (
        <Audio src={staticFile("bgm.mp3")} volume={bgmVolume} loop />
      )}
      <AbsoluteFill style={{ backgroundColor: bgColor }}>
        {scenes.map((scene) => {
          const from = cumFrame;
          cumFrame += scene.durationInFrames;

          return (
            <Sequence key={scene.index} from={from} durationInFrames={scene.durationInFrames}>
              <QuoteSceneView scene={scene} fps={fps} quoteChar={quoteChar} />
            </Sequence>
          );
        })}

        {/* 字幕层：根部全局帧渲染（2026-09-08 修复——原嵌在各场景 Sequence 内
            按全局帧匹配，Sequence 内 frame 从 0 重计，第 2 场景起字幕必消失）。 */}
        <SubtitleOverlay segments={subtitleSegments} />
      </AbsoluteFill>
    </>
  );
};
