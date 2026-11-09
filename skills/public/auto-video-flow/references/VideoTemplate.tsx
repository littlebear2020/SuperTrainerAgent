/**
 * Remotion Video Component Template（2026-09-29 视觉质量版）
 *
 * Copy this file as a starting point for new videos.
 * Rename VideoName to your actual video name.
 *
 * 主流程口径见 SKILL.md（第一步内容稿+分镜表 → 第二步 TTS → 第三步代码 →
 * 第四步静帧预览门）：本模板是第三步「代码生成」自定义组件路径的起点，
 * 每个场景组件对应分镜表一行（版式/屏显/视觉事件）。
 *
 * 视觉纪律（与 SKILL.md「关键规则」同源）：
 * - 逐句入场：视觉元素挂 Reveal at={句索引}，随旁白逐句出现。句索引 =
 *   src/tts_timing.json 的 segments（场景内局部帧）数组中该场景的句序号；
 *   禁用 segments_global（那是根部字幕层 props.subtitleSegments 的全局帧口径）。
 * - 动效密度：任意连续 90 帧至少一次视觉事件；单场景静止 ≤150 帧；
 *   每场景 ≥2 个句索引挂载点；相邻场景版式不同
 *   （本模板三场景 = 标题页 / 要点逐出 / 结尾 CTA）。
 * - 动态背景只用 ✅ 级低开销效果（gotchas「动态背景的性能权衡」表）；
 *   浮动粒子（每帧 N 次位置计算）禁用。
 *
 * 中文字体：src/index.css 已内置 Noto Sans SC / PingFang SC / Microsoft YaHei
 * 回退栈（模板自带，勿删）；组件继承 :root 字体即可，缺了中文会变方块。
 *
 * 尺寸体系：已对齐 references/gotchas.md「1920×1080 画面尺寸参数表」——
 * 改尺寸前先查表，不要把卡片/字号改回小尺寸（内容应占屏 70-80%）。
 *
 * 字幕约定：SUBTITLE_SEGMENTS 的时间戳是【场景内局部帧】（与
 * generate_audio_template.py 输出的 tts_timing.json segments 一致），因此字幕
 * 组件随场景挂在 <Sequence> 内（useCurrentFrame 同为局部帧，二者直接匹配）。
 * 若改用全局帧时间轴（如豆包字级时间戳 / 全局 SRT），字幕组件必须移到
 * Composition 根部按全局帧渲染——混用两种约定会导致第 2 场景起字幕消失。
 * 两套段别口径以 SKILL.md 第二步为准，本文件只实现场景内（局部帧）口径。
 */

import React from 'react';
import {
  AbsoluteFill,
  Audio,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
  Sequence,
  staticFile,
} from 'remotion';

// ============ CONFIG ============

// Color palette - customize for your video
const COLORS = {
  bg: '#0a0e27',
  bgLight: '#111640',
  neonBlue: '#00d4ff',
  neonPurple: '#7c3aed',
  neonPink: '#ec4899',
  neonGreen: '#10b981',
  white: '#ffffff',
  gray: '#94a3b8',
  darkText: '#1e293b',
  cardBg: 'rgba(17, 22, 64, 0.8)',
  cardBorder: 'rgba(0, 212, 255, 0.2)',
};

// Scene durations (frames at 30fps) — 从 tts_timing.json（第二步 TTS 产出）
// 各场景实测音频帧数回填；示例值按 15-30s/场景（SKILL.md 第一步场景时长纪律）
const SCENES = [
  { name: 'intro', duration: 540 },
  { name: 'main', duration: 720 },
  { name: 'conclusion', duration: 450 },
];

// Audio files - one per scene
const AUDIO_FILES = [
  '01-intro.mp3',
  '02-main.mp3',
  '03-conclusion.mp3',
];

// Subtitle segments: [sceneIndex, startFrame, endFrame, text]
// startFrame/endFrame 为场景内局部帧（见文件头「字幕约定」）；示例值与 SCENES 对齐
const SUBTITLE_SEGMENTS: [number, number, number, string][] = [
  [0, 0, 100, "First subtitle"],
  [0, 100, 210, "Second subtitle"],
  [0, 210, 330, "Third subtitle"],
  [0, 330, 460, "Fourth subtitle"],
  [1, 0, 110, "Scene 2 subtitle 1"],
  [1, 110, 230, "Scene 2 subtitle 2"],
  [1, 230, 350, "Scene 2 subtitle 3"],
  [1, 350, 470, "Scene 2 subtitle 4"],
  [1, 470, 590, "Scene 2 subtitle 5"],
  [1, 590, 690, "Scene 2 subtitle 6"],
  [2, 0, 130, "Scene 3 subtitle 1"],
  [2, 130, 280, "Scene 3 subtitle 2"],
  [2, 280, 410, "Scene 3 subtitle 3"],
  // ... 按第二步 TTS 产出回填
];

// ============ SENTENCE INDEX（局部帧口径） ============

/**
 * 句索引：本组件挂在 <Sequence> 内，useCurrentFrame() 返回的已是场景局部帧，
 * 与 SUBTITLE_SEGMENTS（= tts_timing.json 的 segments，局部帧）直接匹配。
 * 禁止再做「全局帧 − 场景起点」换算——那是根部挂载层/旧模板的口径
 * （gotchas「逐句驱动动画」节已标注适用范围，勿照搬旧示例）。
 */
const useSI = (sceneIndex: number): number => {
  const frame = useCurrentFrame();
  const subs = SUBTITLE_SEGMENTS.filter(([si]) => si === sceneIndex);
  for (let i = 0; i < subs.length; i++) {
    const [, start, end] = subs[i];
    if (frame >= start && frame < end) return i;
  }
  return subs.length; // 所有句子播完
};

// 指定句子的起点帧（场景内局部帧）——Reveal/Keyword 的入场动画起算点
const sentenceStartFrame = (sceneIndex: number, sentenceIndex: number): number => {
  const subs = SUBTITLE_SEGMENTS.filter(([si]) => si === sceneIndex);
  const clamped = Math.min(sentenceIndex, subs.length - 1);
  return subs[clamped]?.[1] ?? 0;
};

// at 越界告警（渲染每帧都跑 render，Set 去重防刷屏）。可见性实测（4.0.522）：
// render CLI 转发页面 console 并带文件行号；still CLI 不转发——静帧门发现
// 元素缺失时，跑一次 --frames=单帧 render 看日志或直接核对分镜表句索引
const _atRangeWarned = new Set<string>();
const warnIfAtOutOfRange = (sceneIndex: number, at: number): void => {
  const count = SUBTITLE_SEGMENTS.filter(([si]) => si === sceneIndex).length;
  if (count === 0 || at <= count - 1) return;
  const key = `${sceneIndex}:${at}`;
  if (_atRangeWarned.has(key)) return;
  _atRangeWarned.add(key);
  console.warn(
    `Reveal/Keyword at={${at}} 越界：场景 ${sceneIndex} 只有 ${count} 句（有效 at 为 0-${count - 1}），该元素将永不入场——核对分镜表句索引`,
  );
};

// ============ 逐句入场 + 关键词高亮 ============

/** 逐句入场（Sentence-Reveal）：at = 句索引，随旁白逐句入场——主流程默认要求 */
const Reveal: React.FC<{
  at: number;
  sceneIndex: number;
  children: React.ReactNode;
}> = ({ at, sceneIndex, children }) => {
  const cur = useSI(sceneIndex);
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  warnIfAtOutOfRange(sceneIndex, at);
  if (cur < at) {
    return <div style={{ opacity: 0 }}>{children}</div>;
  }
  // 入场动画从「该句起点帧」起算——若从 0 起算，后入场元素会瞬间出现在终态
  const start = sentenceStartFrame(sceneIndex, at);
  const s = spring({
    frame: Math.max(0, frame - start),
    fps,
    config: { damping: 100, stiffness: 120 },
  });
  return (
    <div
      style={{
        opacity: s,
        transform: `translateY(${interpolate(s, [0, 1], [25, 0])}px)`,
      }}
    >
      {children}
    </div>
  );
};

/**
 * 关键词高亮（屏显与旁白分离：只高亮关键词，不上整句）。
 * 纯 remotion 实现（scaleX/透明度，低开销）。要手绘圈注等花式标注可用
 * @remotion/rough-notation——见 remotion-official/remotion-markup/text-highlights.md
 * （沙箱无网装不了新包，本模板不依赖它）。
 */
const Keyword: React.FC<{
  at: number;
  sceneIndex: number;
  children: React.ReactNode;
}> = ({ at, sceneIndex, children }) => {
  const cur = useSI(sceneIndex);
  const frame = useCurrentFrame();
  const start = sentenceStartFrame(sceneIndex, at);
  warnIfAtOutOfRange(sceneIndex, at);
  const p =
    cur < at
      ? 0
      : interpolate(frame - start, [0, 15], [0, 1], {
          extrapolateLeft: 'clamp',
          extrapolateRight: 'clamp',
        });
  return (
    <span style={{ position: 'relative', display: 'inline-block', padding: '0 10px' }}>
      <span
        style={{
          position: 'absolute',
          inset: 0,
          backgroundColor: COLORS.neonBlue,
          opacity: 0.28,
          borderRadius: 6,
          transform: `scaleX(${p})`,
          transformOrigin: 'left center',
        }}
      />
      <span style={{ position: 'relative', fontWeight: 700, color: p > 0.4 ? COLORS.white : COLORS.gray }}>
        {children}
      </span>
    </span>
  );
};

// ============ 动态背景 + 进度指示 ============

/**
 * 动态背景：渐变光球（radial-gradient + sin）+ 移动网格（背景位平移）。
 * 均为 gotchas「动态背景的性能权衡」表 ✅ 级（每帧 1 次计算）；
 * 浮动粒子 ❌ 禁用（每帧 N 次位置计算，沙箱内存受限）。
 */
const DynamicBackground: React.FC = () => {
  const frame = useCurrentFrame();
  const orbAX = 300 + Math.sin(frame / 75) * 180;
  const orbBY = 760 + Math.cos(frame / 90) * 140;
  const gridX = (frame * 0.8) % 80;
  const gridY = (frame * 0.4) % 80;
  return (
    <AbsoluteFill style={{ backgroundColor: COLORS.bg }}>
      <div
        style={{
          position: 'absolute',
          left: orbAX - 450,
          top: -350,
          width: 900,
          height: 900,
          borderRadius: '50%',
          background: `radial-gradient(circle, ${COLORS.neonBlue}55 0%, transparent 65%)`,
        }}
      />
      <div
        style={{
          position: 'absolute',
          left: 1450 - orbAX * 0.5,
          top: orbBY - 300,
          width: 800,
          height: 800,
          borderRadius: '50%',
          background: `radial-gradient(circle, ${COLORS.neonPurple}44 0%, transparent 65%)`,
        }}
      />
      <div
        style={{
          position: 'absolute',
          inset: -80,
          backgroundImage: `linear-gradient(${COLORS.neonBlue}14 1px, transparent 1px), linear-gradient(90deg, ${COLORS.neonBlue}14 1px, transparent 1px)`,
          backgroundSize: '80px 80px',
          backgroundPosition: `${gridX}px ${gridY}px`,
          opacity: 0.5,
        }}
      />
    </AbsoluteFill>
  );
};

/** 场景进度指示：顶部细进度条（scaleX transform，低开销）+ k/N 角标 */
const SceneProgress: React.FC<{
  sceneIndex: number;
  totalScenes: number;
  durationInFrames: number;
}> = ({ sceneIndex, totalScenes, durationInFrames }) => {
  const frame = useCurrentFrame();
  const progress = interpolate(frame, [0, Math.max(1, durationInFrames - 1)], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  return (
    <>
      <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: 6 }}>
        <div style={{ height: '100%', width: '100%', backgroundColor: 'rgba(148, 163, 184, 0.25)' }} />
        <div
          style={{
            position: 'absolute',
            top: 0,
            left: 0,
            height: '100%',
            width: '100%',
            backgroundColor: COLORS.neonBlue,
            transform: `scaleX(${progress})`,
            transformOrigin: 'left center',
          }}
        />
      </div>
      <div
        style={{
          position: 'absolute',
          top: 18,
          right: 36,
          fontSize: 20,
          color: COLORS.gray,
          letterSpacing: 4,
        }}
      >
        {String(sceneIndex + 1).padStart(2, '0')} / {String(totalScenes).padStart(2, '0')}
      </div>
    </>
  );
};

/**
 * 场景外壳：动态背景 + 进度指示 + 内容区。
 * 内容区 padding 底部 100px = 字幕预留区（gotchas 满屏布局表口径），
 * 屏显要点只在内容区内排布，不侵字幕区。
 */
const SceneShell: React.FC<{
  sceneIndex: number;
  totalScenes: number;
  durationInFrames: number;
  children: React.ReactNode;
}> = ({ sceneIndex, totalScenes, durationInFrames, children }) => (
  <AbsoluteFill>
    <DynamicBackground />
    <SceneProgress
      sceneIndex={sceneIndex}
      totalScenes={totalScenes}
      durationInFrames={durationInFrames}
    />
    <div
      style={{
        position: 'absolute',
        inset: 0,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '20px 60px 100px',
      }}
    >
      {children}
    </div>
  </AbsoluteFill>
);

type SceneProps = {
  sceneIndex: number;
  totalScenes: number;
  durationInFrames: number;
};

// ============ SUBTITLE COMPONENTS ============

const Subtitle: React.FC<{ text: string }> = ({ text }) => {
  return (
    <div style={{
      position: "absolute",
      bottom: 50,
      left: 100,
      right: 100,
      display: "flex",
      justifyContent: "center",
    }}>
      <div style={{
        color: "#ffffff",
        fontSize: 36,
        lineHeight: 1.6,
        textAlign: "center",
        fontWeight: 500,
        textShadow: "0 0 8px rgba(0,0,0,0.9), 0 0 16px rgba(0,0,0,0.7), 2px 2px 4px rgba(0,0,0,0.8)",
        maxWidth: 1400,
      }}>
        {text}
      </div>
    </div>
  );
};

// 场景内字幕：在 <Sequence> 内使用，useCurrentFrame() 为局部帧，
// 与 SUBTITLE_SEGMENTS 的场景内帧号直接匹配
const SceneSubtitle: React.FC<{ sceneIndex: number }> = ({ sceneIndex }) => {
  const frame = useCurrentFrame();
  const seg = SUBTITLE_SEGMENTS.find(
    ([si, startFrame, endFrame]) =>
      si === sceneIndex && frame >= startFrame && frame < endFrame,
  );
  if (!seg) return null;
  return <Subtitle text={seg[3]} />;
};

// ============ SCENE COMPONENTS（版式 = 分镜表版式列） ============

/** 场景 0（版式：标题页）——分镜表视觉事件：句1→大标题入场；句2→全称逐词高亮 */
const IntroScene: React.FC<SceneProps> = ({ sceneIndex, totalScenes, durationInFrames }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame, fps, config: { damping: 100 } });
  return (
    <SceneShell
      sceneIndex={sceneIndex}
      totalScenes={totalScenes}
      durationInFrames={durationInFrames}
    >
      <h1
        style={{
          fontSize: 76,
          color: COLORS.white,
          margin: 0,
          transform: `scale(${s})`,
          textShadow: '0 0 24px rgba(0, 212, 255, 0.35)',
        }}
      >
        Your Video Title
      </h1>
      {/* 句2 起：全称逐词高亮（关键词，非整句——屏显与旁白分离） */}
      <Reveal at={1} sceneIndex={sceneIndex}>
        <p style={{ fontSize: 26, color: COLORS.gray, marginTop: 24, letterSpacing: 1 }}>
          <Keyword at={1} sceneIndex={sceneIndex}>Multi-Agent</Keyword>
          <Keyword at={2} sceneIndex={sceneIndex}>Interactive</Keyword>
          <Keyword at={3} sceneIndex={sceneIndex}>Classroom</Keyword>
        </p>
      </Reveal>
    </SceneShell>
  );
};

/** 要点卡片（3 列布局尺寸对齐 gotchas 表：520px / 32px 40px / r16；辉光脉动 ✅ 级开销） */
const Card: React.FC<{ title: string; description: string }> = ({ title, description }) => {
  const frame = useCurrentFrame();
  const pulse = 0.5 + 0.5 * Math.sin(frame / 20);
  return (
    <div style={{
      backgroundColor: COLORS.cardBg,
      border: `1px solid ${COLORS.cardBorder}`,
      borderRadius: 16,
      padding: "32px 40px",
      width: 520,
      boxShadow: `0 0 ${8 + pulse * 16}px rgba(0, 212, 255, ${0.12 + pulse * 0.18})`,
    }}>
      <h3 style={{ color: COLORS.white, fontSize: 28, margin: "0 0 12px" }}>{title}</h3>
      <p style={{ color: COLORS.gray, fontSize: 20, margin: 0 }}>{description}</p>
    </div>
  );
};

/** 场景 1（版式：要点逐出）——分镜表视觉事件：句2→卡片1；句4→卡片2；句6→卡片3 */
const MainScene: React.FC<SceneProps> = ({ sceneIndex, totalScenes, durationInFrames }) => (
  <SceneShell
    sceneIndex={sceneIndex}
    totalScenes={totalScenes}
    durationInFrames={durationInFrames}
  >
    <h2 style={{ fontSize: 56, color: COLORS.white, margin: "0 0 48px" }}>三个核心能力</h2>
    <div style={{ display: "flex", gap: 36 }}>
      <Reveal at={1} sceneIndex={sceneIndex}>
        <Card title="能力 1" description="关键词 ≤12 字" />
      </Reveal>
      <Reveal at={3} sceneIndex={sceneIndex}>
        <Card title="能力 2" description="关键词 ≤12 字" />
      </Reveal>
      <Reveal at={5} sceneIndex={sceneIndex}>
        <Card title="能力 3" description="关键词 ≤12 字" />
      </Reveal>
    </div>
  </SceneShell>
);

/** 场景 2（版式：结尾 CTA）——分镜表视觉事件：句1→CTA 标题入场；句3→行动项高亮 */
const ConclusionScene: React.FC<SceneProps> = ({ sceneIndex, totalScenes, durationInFrames }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = spring({ frame, fps, config: { damping: 100 } });
  return (
    <SceneShell
      sceneIndex={sceneIndex}
      totalScenes={totalScenes}
      durationInFrames={durationInFrames}
    >
      <h2
        style={{
          fontSize: 60,
          color: COLORS.white,
          margin: "0 0 32px",
          opacity: enter,
          transform: `translateY(${(1 - enter) * 20}px)`,
        }}
      >
        下一步做什么？
      </h2>
      <Reveal at={2} sceneIndex={sceneIndex}>
        <p style={{ fontSize: 28, color: COLORS.gray, margin: 0 }}>
          访问 <Keyword at={2} sceneIndex={sceneIndex}>example.com</Keyword> 获取完整文档
        </p>
      </Reveal>
    </SceneShell>
  );
};

// ============ MAIN VIDEO COMPONENT ============

const SCENE_COMPONENTS: Record<string, React.FC<SceneProps>> = {
  intro: IntroScene,
  main: MainScene,
  conclusion: ConclusionScene,
};

const VideoNameVideo: React.FC = () => {
  // 场景起点累加（全局帧）。场景用 <Sequence> 包裹后，
  // 场景组件内 useCurrentFrame() 为局部帧——不要再做
  // 「全局帧 − 场景起点」的手动换算，那是本模板旧版的坑。
  let acc = 0;
  const sceneOffsets = SCENES.map((scene) => {
    const offset = acc;
    acc += scene.duration;
    return offset;
  });

  return (
    <AbsoluteFill>
      {/* Scenes + per-scene subtitles（局部帧约定，见文件头） */}
      {SCENES.map((scene, i) => {
        const SceneComp = SCENE_COMPONENTS[scene.name] ?? IntroScene;
        return (
          <Sequence
            key={scene.name}
            from={sceneOffsets[i]}
            durationInFrames={scene.duration}
          >
            <SceneComp
              sceneIndex={i}
              totalScenes={SCENES.length}
              durationInFrames={scene.duration}
            />
            <SceneSubtitle sceneIndex={i} />
          </Sequence>
        );
      })}

      {/* Audio tracks */}
      {AUDIO_FILES.map((file, i) => (
        <Sequence
          key={file}
          from={sceneOffsets[i]}
          durationInFrames={SCENES[i].duration}
        >
          <Audio src={staticFile(`audio/video-name/${file}`)} />
        </Sequence>
      ))}

      {/* BGM：bgm.mp3 放 public/ 下；文件缺失时 staticFile 渲染期抛错——未备文件则删除本行或用 getStaticFiles 守卫（同款见 DataDrivenComposition.tsx:235） */}
      <Audio src={staticFile("bgm.mp3")} volume={0.15} loop />
    </AbsoluteFill>
  );
};

export default VideoNameVideo;
