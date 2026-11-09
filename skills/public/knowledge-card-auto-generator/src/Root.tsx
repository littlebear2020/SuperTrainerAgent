import React from 'react';
import { Composition, Still, registerRoot } from 'remotion';
import { AnimatedKnowledgeCard } from './components/video/AnimatedKnowledgeCard';
import { AnimatedOpenClawPortraitCard } from './components/video/AnimatedOpenClawPortraitCard';
import { SvgKnowledgeCard } from './components/svg/SvgKnowledgeCard';
import type { CardData } from './types/card';
import './index.css';

// ── Landscape (1920×1080) ──
const LANDSCAPE_W = 1920;
const LANDSCAPE_H = 1080;
const VIDEO_FPS = 30;
const VIDEO_DURATION = 5;

const defaultCardData: CardData = {
  id: 'default',
  title: 'Knowledge Card',
  points: ['Point 1', 'Point 2', 'Point 3'],
  theme: 'tech-blue',
  tags: ['demo', 'example'],
};

// ── Portrait (390×780) ──
const PORTRAIT_W = 390;
const PORTRAIT_H = 780;

const PORTRAIT_FPS = 30;
const PORTRAIT_DURATION = 6;

export const RemotionRoot: React.FC = () => {
  return (
    <>
      {/* Landscape knowledge card — video */}
      <Composition
        id="KnowledgeCardVideo"
        component={AnimatedKnowledgeCard}
        width={LANDSCAPE_W}
        height={LANDSCAPE_H}
        fps={VIDEO_FPS}
        durationInFrames={VIDEO_FPS * VIDEO_DURATION}
        defaultProps={{ cardData: defaultCardData }}
      />

      {/* Landscape knowledge card — still image */}
      <Still
        id="KnowledgeCardStill"
        component={SvgKnowledgeCard}
        width={LANDSCAPE_W}
        height={LANDSCAPE_H}
        defaultProps={{ cardData: defaultCardData }}
      />

      {/* Portrait OpenClaw card — video */}
      <Composition
        id="OpenClawPortraitVideo"
        component={AnimatedOpenClawPortraitCard}
        width={PORTRAIT_W}
        height={PORTRAIT_H}
        fps={PORTRAIT_FPS}
        durationInFrames={PORTRAIT_FPS * PORTRAIT_DURATION}
        defaultProps={{}}
      />
    </>
  );
};

registerRoot(RemotionRoot);
