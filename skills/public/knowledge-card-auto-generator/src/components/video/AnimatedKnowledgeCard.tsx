import React from 'react';
import { useCurrentFrame, interpolate, Easing } from 'remotion';
import type { CardData } from '../../types/card';
import { resolveTheme } from '../../themes/index';
import { AnimatedBackground } from './AnimatedBackground';
import { GlassPanel } from './GlassPanel';
import { AnimatedTitle } from './AnimatedTitle';
import { AnimatedPoints } from './AnimatedPoints';

type AnimatedKnowledgeCardProps = {
  cardData: CardData;
  width?: number;
  height?: number;
};

const EXIT_START = 120;
const TOTAL_FRAMES = 150;

export const AnimatedKnowledgeCard: React.FC<AnimatedKnowledgeCardProps> = ({
  cardData,
  width = 1920,
  height = 1080,
}) => {
  const frame = useCurrentFrame();
  const theme = resolveTheme(cardData.theme);
  const scale = Math.min(width, height) / 1080;

  const tagStagger = 6;

  return (
    <div
      style={{
        position: 'relative',
        width,
        height,
        overflow: 'hidden',
        fontFamily: 'system-ui, -apple-system, sans-serif',
      }}
    >
      <AnimatedBackground
        width={width}
        height={height}
        theme={theme}
        exitStart={EXIT_START}
        totalFrames={TOTAL_FRAMES}
      />

      <GlassPanel
        theme={theme}
        frameStart={5}
        scale={scale}
        exitStart={EXIT_START}
        totalFrames={TOTAL_FRAMES}
      >
        <AnimatedTitle
          title={cardData.title}
          subtitle={cardData.subtitle}
          theme={theme}
          frameStart={15}
          scale={scale}
          exitStart={EXIT_START}
          totalFrames={TOTAL_FRAMES}
        />

        <div style={{ flex: 1 }}>
          <AnimatedPoints
            points={cardData.points}
            theme={theme}
            frameStart={35}
            scale={scale}
            exitStart={EXIT_START}
            totalFrames={TOTAL_FRAMES}
          />
        </div>

        {cardData.tags && cardData.tags.length > 0 && (
          <div
            style={{
              display: 'flex',
              gap: 12 * scale,
              marginTop: 'auto',
              paddingTop: 16 * scale,
            }}
          >
            {cardData.tags.map((tag, index) => {
              const tagStart = 80 + index * tagStagger;

              const tagScale = interpolate(
                frame,
                [tagStart, tagStart + 4, tagStart + 8, tagStart + 12],
                [0, 1.25, 0.88, 1],
                { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' },
              );

              const tagOpacity = interpolate(frame, [tagStart, tagStart + 8], [0, 1], {
                extrapolateLeft: 'clamp',
                extrapolateRight: 'clamp',
                easing: Easing.out(Easing.cubic),
              });

              const tagGlow = interpolate(
                frame,
                [tagStart, tagStart + 8, tagStart + 20],
                [1, 0.5, 0],
                { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' },
              );

              const tagExitOpacity = interpolate(frame, [EXIT_START, TOTAL_FRAMES], [1, 0], {
                extrapolateLeft: 'clamp',
                extrapolateRight: 'clamp',
                easing: Easing.in(Easing.cubic),
              });

              const tagExitScale = interpolate(frame, [EXIT_START, TOTAL_FRAMES], [1, 0.7], {
                extrapolateLeft: 'clamp',
                extrapolateRight: 'clamp',
                easing: Easing.in(Easing.back(1.5)),
              });

              return (
                <span
                  key={index}
                  style={{
                    padding: `${8 * scale}px ${18 * scale}px`,
                    borderRadius: 18 * scale,
                    background: theme.tagBackground,
                    border: `1px solid ${theme.tagBorderColor}`,
                    color: theme.text.accent,
                    fontSize: 16 * scale,
                    fontFamily: 'system-ui, -apple-system, sans-serif',
                    display: 'inline-block',
                    opacity: tagOpacity * tagExitOpacity,
                    transform: `scale(${tagScale * tagExitScale})`,
                    boxShadow: tagGlow > 0.2
                      ? `0 0 ${16 * scale}px ${theme.text.accent}50, 0 0 ${32 * scale}px ${theme.text.accent}25`
                      : 'none',
                  }}
                >
                  {tag}
                </span>
              );
            })}
          </div>
        )}
      </GlassPanel>
    </div>
  );
};
