import React from 'react';
import {
  AbsoluteFill, Audio, Img, Sequence, staticFile,
  interpolate, spring, useCurrentFrame, useVideoConfig,
} from 'remotion';

// ============ 字幕数据（从 JSON import，不要内联中文） ============
// TTS 脚本会自动生成 src/subtitles.json，直接 import 即可
// import subtitlesData from './subtitles.json';

// ============ 场景时长（从 TTS 脚本输出复制） ============
const SCENES = [
  // { name: 'scene-01', duration: 1000 },
];

const AUDIO_FILES = [
  // '01-scene-01.mp3',
];

// ============ 配色 ============
const C = {
  bg: '#f8f9fa', white: '#ffffff', dark: '#2d2d2d', gray: '#666666',
  border: '#e0e0e0', red: '#cc0000', redBg: '#fff5f5',
  accent: '#1a3a5c', accentBg: '#eef3f8',
};

// ============ 通用组件 ============

const Logo: React.FC = () => (
  <Img src={staticFile('logo.png')} style={{
    position: 'absolute', top: 25, right: 40, height: 80, width: 'auto',
    objectFit: 'contain', opacity: 0.9,
  }} />
);

const BottomLine: React.FC = () => (
  <div style={{ position: 'absolute', bottom: 0, left: 0, right: 0, height: 6,
    background: `linear-gradient(90deg, ${C.red}, ${C.dark})` }} />
);

const SceneTitle: React.FC<{ title: string; subtitle?: string; color?: string }> = ({ title, subtitle, color = C.dark }) => {
  const frame = useCurrentFrame();
  const op = interpolate(frame, [0, 20], [0, 1], { extrapolateRight: 'clamp' });
  return (
    <div style={{ opacity: op, marginBottom: 30, textAlign: 'center' }}>
      <div style={{ fontSize: 48, fontWeight: 800, color, letterSpacing: 2 }}>{title}</div>
      {subtitle && <div style={{ fontSize: 24, color: C.gray, marginTop: 8 }}>{subtitle}</div>}
      <div style={{ width: 60, height: 3, backgroundColor: color, margin: '12px auto 0' }} />
    </div>
  );
};

const FadeIn: React.FC<{ children: React.ReactNode; delay?: number; duration?: number }> = ({ children, delay = 0, duration = 25 }) => {
  const frame = useCurrentFrame();
  const op = interpolate(frame, [delay, delay + duration], [0, 1], { extrapolateRight: 'clamp' });
  return <div style={{ opacity: op }}>{children}</div>;
};

// ============ 字幕叠加组件 ============
// ⚠️ 字幕数据必须通过 JSON import 加载，不要在 TSX 中内联含中文引号的字符串
// type SubtitleEntry = { text: string; startFrame: number; endFrame: number };
// const SUBTITLES: Record<number, SubtitleEntry[]> = subtitlesData;

const SubtitleOverlay: React.FC<{ sceneIndex: number; subtitles: Record<number, Array<{text: string; startFrame: number; endFrame: number}>> }> = ({ sceneIndex, subtitles }) => {
  const frame = useCurrentFrame();
  const subs = subtitles[sceneIndex];
  if (!subs) return null;

  const current = subs.find(s => frame >= s.startFrame && frame < s.endFrame);
  if (!current) return null;

  const fadeIn = interpolate(frame, [current.startFrame, current.startFrame + 5], [0, 1], { extrapolateRight: 'clamp' });
  const fadeOut = interpolate(frame, [current.endFrame - 5, current.endFrame], [1, 0], { extrapolateLeft: 'clamp' });
  const opacity = Math.min(fadeIn, fadeOut);

  return (
    <div style={{
      position: 'absolute', bottom: 60, left: 0, right: 0,
      display: 'flex', justifyContent: 'center', opacity,
    }}>
      <div style={{
        fontSize: 28, fontWeight: 600, color: '#000000',
        backgroundColor: 'rgba(255,255,255,0.7)',
        padding: '8px 24px', borderRadius: 6,
        maxWidth: 1600, textAlign: 'center', lineHeight: 1.5,
      }}>
        {current.text}
      </div>
    </div>
  );
};

// ============ 音频轨道 ============
const AudioTracks: React.FC = () => {
  let offset = 0;
  const offsets = SCENES.map((s) => { const o = offset; offset += s.duration; return o; });
  return (
    <>
      {AUDIO_FILES.map((file, i) => (
        <Sequence key={file} from={offsets[i]} durationInFrames={SCENES[i].duration}>
          <Audio src={staticFile(`audio/corporate-video/${file}`)} />
        </Sequence>
      ))}
    </>
  );
};

// ============ 场景组件（示例） ============

const Scene1Opening: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const scale = spring({ frame, fps, config: { damping: 100 } });
  return (
    <AbsoluteFill style={{ backgroundColor: C.white, justifyContent: 'center', alignItems: 'center' }}>
      <Logo /><BottomLine />
      <div style={{ transform: `scale(${scale})`, textAlign: 'center', padding: '0 100px' }}>
        <div style={{ fontSize: 72, fontWeight: 900, color: C.red, marginBottom: 30 }}>
          {/* 标题 */}
        </div>
      </div>
    </AbsoluteFill>
  );
};

// ============ 主组件 ============
const CorporateVideo: React.FC = () => {
  const sceneComponents = [
    Scene1Opening,
    // 添加所有场景组件...
  ];

  let offset = 0;
  return (
    <AbsoluteFill>
      {SCENES.map((scene, i) => {
        const from = offset;
        offset += scene.duration;
        const SceneComp = sceneComponents[i]; // ⚠️ 必须赋值给大写变量
        return (
          <Sequence key={scene.name} from={from} durationInFrames={scene.duration}>
            <SceneComp />
            {/* <SubtitleOverlay sceneIndex={i} subtitles={SUBTITLES} /> */}
          </Sequence>
        );
      })}
      <AudioTracks />
    </AbsoluteFill>
  );
};

export { CorporateVideo };
