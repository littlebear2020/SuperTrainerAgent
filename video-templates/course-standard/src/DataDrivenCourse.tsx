import React from "react";
import { AbsoluteFill, Audio, staticFile, useVideoConfig } from "remotion";
import { TransitionSeries, linearTiming } from "@remotion/transitions";
import { fade } from "@remotion/transitions/fade";
import { resolveTheme, Theme, ThemeColors, ThemeName } from "./theme";
import { CourseProps } from "./course-props";
import { resolveLayout } from "./LayoutRouter";
import { SubtitleLayer } from "./layers/SubtitleLayer";
import { BrandLayer } from "./layers/BrandLayer";
import { Backdrop } from "./components/Backdrop";
import { LogoBadge } from "./components/LogoBadge";

/** D7 暂定值：fade 转场 15 帧（T5 冒烟首条成片后用户定夺）。 */
export const TRANSITION_FRAMES = 15;
const INTRO_FRAMES_DEFAULT = 90; // 3s
const OUTRO_FRAMES_DEFAULT = 90; // 3s

/* ── 时间轴纯函数（与渲染共用，防漂移）───────────────────────
 * 不变量（T2 红线 1）：场景 k 起点恒为 INTRO + Σ_{i<k} d_i。
 * TransitionSeries 的转场重叠帧由**前一元素延展**吸收（末元素不延展），
 * 因此字幕全局时间轴不发生系统性漂移。
 * ────────────────────────────────────────────────────────── */
export interface CourseTimeline {
  introFrames: number;
  outroFrames: number;
  sceneStarts: number[]; // 全局帧
  totalFrames: number;
}

export const calculateTimeline = (props: CourseProps): CourseTimeline => {
  const introFrames = props.brand?.intro ? props.brand.intro.durationInFrames ?? INTRO_FRAMES_DEFAULT : 0;
  const outroFrames = props.brand?.outro ? props.brand.outro.durationInFrames ?? OUTRO_FRAMES_DEFAULT : 0;
  const sceneStarts: number[] = [];
  let acc = introFrames;
  for (const s of props.scenes ?? []) {
    sceneStarts.push(acc);
    acc += Math.max(1, Math.round(s.durationInFrames));
  }
  const totalFrames = Math.max(1, acc + outroFrames);
  return { introFrames, outroFrames, sceneStarts, totalFrames };
};

export const calculateCourseMetadata = ({ props }: { props: CourseProps }) => {
  const tl = calculateTimeline(props);
  return {
    durationInFrames: tl.totalFrames,
    fps: props.fps ?? 30,
    width: props.width ?? 1920,
    height: props.height ?? 1080,
  };
};

/* ── 片头/片尾精简变体（BrandLayer 归属，T2 规格）────────── */

const BrandIntro: React.FC<{ theme: Theme; props: CourseProps }> = ({ theme, props }) => {
  const intro = props.brand?.intro ?? {};
  return (
    <AbsoluteFill style={{ fontFamily: theme.fontFamily, backgroundColor: theme.colors.bg }}>
      <Backdrop theme={theme} />
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "center" }}>
        <LogoBadge theme={theme} size={96} logoUrl={props.brand?.logoPath} />
        <div
          style={{
            marginTop: 30,
            fontSize: 72,
            fontWeight: 800,
            color: theme.colors.text,
            textAlign: "center",
            lineHeight: 1.25,
          }}
        >
          {intro.headline ?? props.title}
        </div>
        {intro.subtitle ? (
          <div
            style={{
              marginTop: 26,
              fontSize: 32,
              color: theme.colors.textSecondary,
              textAlign: "center",
            }}
          >
            {intro.subtitle}
          </div>
        ) : null}
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

const BrandOutro: React.FC<{ theme: Theme; props: CourseProps }> = ({ theme, props }) => {
  const outro = props.brand?.outro ?? {};
  return (
    <AbsoluteFill style={{ fontFamily: theme.fontFamily, backgroundColor: theme.colors.bg }}>
      <Backdrop theme={theme} />
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "center" }}>
        <LogoBadge theme={theme} size={96} logoUrl={props.brand?.logoPath} />
        <div
          style={{
            marginTop: 30,
            fontSize: 64,
            fontWeight: 800,
            color: theme.colors.text,
            letterSpacing: 6,
          }}
        >
          {outro.headline ?? "感谢观看"}
        </div>
        {outro.follow ? (
          <div style={{ marginTop: 30, fontSize: 30, color: theme.colors.textSecondary }}>
            {outro.follow}
          </div>
        ) : null}
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

/* ── 主 Composition ────────────────────────────────────────── */

export const DataDrivenCourse: React.FC<CourseProps> = (props) => {
  useVideoConfig(); // 确保 Composition 上下文（calculateMetadata 已定 fps/尺寸）
  const theme = resolveTheme(
    (props.brand?.themeName as ThemeName) ?? "dark-blue",
    props.brand?.colors as Partial<ThemeColors> | undefined,
    props.brand?.fontFamily,
  );
  const tl = calculateTimeline(props);
  const hasIntro = tl.introFrames > 0;
  const hasOutro = tl.outroFrames > 0;
  const transitionsOn = TRANSITION_FRAMES > 0;

  // 渲染时长：非末元素延展 TR 吸收转场重叠（红线 1），末元素不延展
  const elements = [
    ...(hasIntro ? [{ kind: "intro" as const, dur: tl.introFrames }] : []),
    ...(props.scenes ?? []).map((s, i) => ({ kind: "scene" as const, dur: Math.max(1, Math.round(s.durationInFrames)), i })),
    ...(hasOutro ? [{ kind: "outro" as const, dur: tl.outroFrames }] : []),
  ];
  const renderedDur = (idx: number) => elements[idx].dur + (idx < elements.length - 1 ? TRANSITION_FRAMES : 0);

  // 降级留痕：未知版式 / 素材缺失（不炸渲染，console.warn 供 worker 日志采集）
  const degradations: string[] = [];
  for (const s of props.scenes ?? []) {
    const { fallback } = resolveLayout(s.layout);
    if (fallback) degradations.push(`unknown layout '${s.layout}' → ConceptPage`);
    if (s.visual?.kind === "image" || s.visual?.kind === "video") {
      if (!s.visual.path) degradations.push(`scene ${s.index}: visual kind=${s.visual.kind} missing path → placeholder`);
    }
  }
  if (degradations.length) {
    // eslint-disable-next-line no-console
    console.warn("[DataDrivenCourse] degradations:", JSON.stringify(degradations));
  }

  // 字幕：合并全部场景 captions（全局毫秒已在 gateway 侧折算）。
  // T-B2（F1）：注入 sceneIndex（SubtitleLayer hold 边界限定当前场景内）+
  // 下传场景毫秒区间（帧时间轴换算，与 calculateTimeline 同源防漂移）。
  const msPerFrame = 1000 / (props.fps ?? 30);
  const allCaptions = (props.scenes ?? []).flatMap((s, i) =>
    (s.captions ?? []).map((w) => ({ ...w, sceneIndex: i })),
  );
  const sceneRangesMs = (props.scenes ?? []).map((s, i) => ({
    startMs: Math.round(tl.sceneStarts[i] * msPerFrame),
    endMs: Math.round((tl.sceneStarts[i] + Math.max(1, Math.round(s.durationInFrames))) * msPerFrame),
  }));

  return (
    <AbsoluteFill style={{ backgroundColor: theme.colors.bg }}>
      <TransitionSeries>
        {elements.map((el, idx) => {
          const node = (() => {
            if (el.kind === "intro") return <BrandIntro theme={theme} props={props} />;
            if (el.kind === "outro") return <BrandOutro theme={theme} props={props} />;
            const s = props.scenes[el.i];
            const { component: LayoutComp } = resolveLayout(s.layout);
            return (
              <>
                {s.audioFile ? <Audio src={staticFile(s.audioFile)} /> : null}
                <LayoutComp
                  themeName={theme.name}
                  content={s.layoutProps}
                  visualSpec={s.visual}
                  transitionsOn={transitionsOn}
                  logoUrl={props.brand?.logoPath}
                  brandName={props.brand?.brandName}
                />
                <BrandLayer theme={theme} logoPath={props.brand?.logoPath} />
              </>
            );
          })();
          return (
            <React.Fragment key={idx}>
              <TransitionSeries.Sequence durationInFrames={renderedDur(idx)}>{node}</TransitionSeries.Sequence>
              {idx < elements.length - 1 ? (
                <TransitionSeries.Transition
                  presentation={fade()}
                  timing={linearTiming({ durationInFrames: TRANSITION_FRAMES })}
                />
              ) : null}
            </React.Fragment>
          );
        })}
      </TransitionSeries>

      {/* 字幕层：根部全局帧（严禁进 Sequence/TransitionSeries——T2 红线） */}
      <SubtitleLayer
        theme={theme}
        captions={allCaptions}
        subtitleStyle={props.brand?.subtitleStyle}
        sceneRangesMs={sceneRangesMs}
      />

      {/* BGM 唯一归属：模板内混音；ffmpeg 后处理只做 loudnorm */}
      {props.bgm?.enabled && props.bgm.path ? (
        <Audio src={staticFile(props.bgm.path)} volume={props.bgm.volume ?? 0.15} loop />
      ) : null}
    </AbsoluteFill>
  );
};
