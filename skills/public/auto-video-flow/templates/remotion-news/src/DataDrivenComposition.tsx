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

export interface NewsVideoProps {
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
  theme_color?: string;
  show_footer?: boolean;
  // 底部信息条日期（props 注入，保证渲染可复现；不用 new Date()——
  // 墙钟日期会让同一 props 的两次渲染产出不同帧，且 Studio 缓存失效）
  render_date?: string;
}

/* ── Scene component ─────────────────────────────────────────── */

const NewsSceneView: React.FC<{
  scene: Scene;
  fps: number;
  themeColor: string;
}> = ({ scene, fps, themeColor }) => {
  const frame = useCurrentFrame();

  const fadeInFrames = Math.round(fps * 0.4);
  const fadeOutFrames = Math.round(fps * 0.3);
  const holdEnd = scene.durationInFrames - fadeOutFrames;

  const opacity =
    frame < fadeInFrames
      ? interpolate(frame, [0, fadeInFrames], [0, 1], { easing: Easing.out(Easing.quad) })
      : frame > holdEnd
        ? interpolate(frame, [holdEnd, scene.durationInFrames], [1, 0], { easing: Easing.in(Easing.quad) })
        : 1;

  return (
    <AbsoluteFill style={{ backgroundColor: "#0a0a0a", color: "white" }}>
      {/* Top accent bar */}
      <div style={{
        position: "absolute",
        top: 0,
        left: 0,
        right: 0,
        height: 4,
        backgroundColor: themeColor,
      }} />

      {/* Narration text */}
      <div style={{
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        height: "100%",
        padding: "60px 80px",
        opacity,
      }}>
        <div style={{
          fontSize: 40,
          lineHeight: 1.7,
          textAlign: "center",
          fontFamily: "'PingFang SC', 'Microsoft YaHei', 'Noto Sans SC', sans-serif",
          borderLeft: `4px solid ${themeColor}`,
          paddingLeft: 30,
        }}>
          {scene.narration}
        </div>
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
    <AbsoluteFill style={{ justifyContent: "flex-end", alignItems: "center", paddingBottom: 70 }}>
      <div style={{
        backgroundColor: "rgba(0,0,0,0.75)",
        color: "white",
        fontSize: 26,
        lineHeight: 1.4,
        padding: "6px 18px",
        borderRadius: 6,
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

export const DataDrivenVideo: React.FC<NewsVideoProps> = (props) => {
  // transition/transitionDurationFrames 仍由后端 props 传入但不再消费：
  // 旧实现只做时长扣减（场景提前 td 帧、音轨重叠）而没有任何视觉转场效果，
  // 是纯 bug。2026-09-08 起场景排布对齐 remotion-base 不变量——场景 k 起点
  // 恒为 Σduration[0..k-1]，与 SRT 全局帧 / 拼接音轨逐帧对齐（音轨零重叠）。
  const {
    scenes, subtitleSegments, fps,
    bgmEnabled, bgmVolume, title, theme_color, show_footer, render_date,
  } = props;

  const themeColor = theme_color || "#DC2626";
  const showFooter = show_footer !== false;

  // bgmEnabled=true 但 public/bgm.mp3 缺失时 staticFile 会在渲染期抛错——
  // 无文件则静默降级不渲染 BGM（2026-09-08 修复，同 remotion-base）。
  const bgmExists = getStaticFiles().some((f) => f.name === "bgm.mp3");

  let cumFrame = 0;

  return (
    <>
      {bgmEnabled && bgmExists && (
        <Audio src={staticFile("bgm.mp3")} volume={bgmVolume} loop />
      )}
      <AbsoluteFill style={{ backgroundColor: "#0a0a0a" }}>
        {/* Title bar */}
        {title && (
          <div style={{
            position: "absolute",
            top: 20,
            left: 30,
            right: 30,
            fontSize: 20,
            fontWeight: 700,
            color: themeColor,
            letterSpacing: 2,
            zIndex: 100,
            fontFamily: "'PingFang SC', 'Microsoft YaHei', 'Noto Sans SC', sans-serif",
          }}>
            {title}
          </div>
        )}

        {/* Footer */}
        {showFooter && (
          <div style={{
            position: "absolute",
            bottom: 0,
            left: 0,
            right: 0,
            height: 40,
            backgroundColor: themeColor,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "0 30px",
            zIndex: 100,
            fontFamily: "'PingFang SC', 'Microsoft YaHei', 'Noto Sans SC', sans-serif",
          }}>
            <span style={{ fontSize: 14, color: "white", fontWeight: 600 }}>NEWS</span>
            <span style={{ fontSize: 12, color: "rgba(255,255,255,0.8)" }}>
              {render_date ?? ""}
            </span>
          </div>
        )}

        {/* Scenes */}
        {scenes.map((scene) => {
          const from = cumFrame;
          cumFrame += scene.durationInFrames;

          return (
            <Sequence key={scene.index} from={from} durationInFrames={scene.durationInFrames}>
              <NewsSceneView scene={scene} fps={fps} themeColor={themeColor} />
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
