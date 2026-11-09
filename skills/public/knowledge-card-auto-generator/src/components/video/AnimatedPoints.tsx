import React from 'react';
import { useCurrentFrame, interpolate, Easing } from 'remotion';
import type { ThemeDefinition } from '../../types/card';

type AnimatedPointsProps = {
  points: string[];
  theme: ThemeDefinition;
  frameStart?: number;
  staggerFrames?: number;
  scale?: number;
  exitStart?: number;
  totalFrames?: number;
};

export const AnimatedPoints: React.FC<AnimatedPointsProps> = ({
  points,
  theme,
  frameStart = 35,
  staggerFrames = 8,
  scale = 1,
  exitStart = 120,
  totalFrames = 150,
}) => {
  const frame = useCurrentFrame();

  const fs = Math.round(24 * scale);
  const gap = Math.round(14 * scale);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap }}>
      {points.map((point, index) => {
        const pointFrameStart = frameStart + index * staggerFrames;

        // Entrance: dramatic slide from left with blur
        const opacity = interpolate(frame, [pointFrameStart, pointFrameStart + 15], [0, 1], {
          extrapolateLeft: 'clamp',
          extrapolateRight: 'clamp',
          easing: Easing.out(Easing.cubic),
        });

        const translateX = interpolate(
          frame,
          [pointFrameStart, pointFrameStart + 8, pointFrameStart + 15],
          [-120 * scale, 8 * scale, 0],
          {
            extrapolateLeft: 'clamp',
            extrapolateRight: 'clamp',
          },
        );

        // Blur entrance for each point
        const pointBlur = interpolate(frame, [pointFrameStart, pointFrameStart + 12], [8, 0], {
          extrapolateLeft: 'clamp',
          extrapolateRight: 'clamp',
          easing: Easing.out(Easing.cubic),
        });

        // Bullet scale entrance with big bounce
        const bulletScale = interpolate(
          frame,
          [pointFrameStart, pointFrameStart + 4, pointFrameStart + 8, pointFrameStart + 12],
          [0, 1.5, 0.85, 1],
          {
            extrapolateLeft: 'clamp',
            extrapolateRight: 'clamp',
          },
        );

        // Bullet glow (strong burst then fade)
        const bulletGlow = interpolate(
          frame,
          [pointFrameStart + 3, pointFrameStart + 10, pointFrameStart + 25],
          [1, 0.6, 0.2],
          {
            extrapolateLeft: 'clamp',
            extrapolateRight: 'clamp',
          },
        );

        // Highlight sweep behind the point
        const highlightWidth = interpolate(
          frame,
          [pointFrameStart, pointFrameStart + 18],
          [0, 100],
          {
            extrapolateLeft: 'clamp',
            extrapolateRight: 'clamp',
            easing: Easing.out(Easing.cubic),
          },
        );

        const highlightOpacity = interpolate(
          frame,
          [pointFrameStart, pointFrameStart + 10, pointFrameStart + 18],
          [0.12, 0.12, 0],
          {
            extrapolateLeft: 'clamp',
            extrapolateRight: 'clamp',
          },
        );

        // Exit animation
        const exitOpacity = interpolate(frame, [exitStart, totalFrames], [1, 0], {
          extrapolateLeft: 'clamp',
          extrapolateRight: 'clamp',
          easing: Easing.in(Easing.cubic),
        });

        const exitTranslateX = interpolate(frame, [exitStart, totalFrames], [0, 60 * scale], {
          extrapolateLeft: 'clamp',
          extrapolateRight: 'clamp',
          easing: Easing.in(Easing.cubic),
        });

        return (
          <div
            key={index}
            style={{
              opacity: opacity * exitOpacity,
              transform: `translateX(${translateX + exitTranslateX}px)`,
              display: 'flex',
              alignItems: 'flex-start',
              gap,
              position: 'relative',
              filter: `blur(${pointBlur}px)`,
            }}
          >
            {/* Background highlight sweep */}
            <div
              style={{
                position: 'absolute',
                left: -10 * scale,
                top: -4 * scale,
                width: `${highlightWidth}%`,
                height: fs * 2,
                borderRadius: 8 * scale,
                background: theme.text.accent,
                opacity: highlightOpacity * exitOpacity,
              }}
            />

            {/* Bullet */}
            <span
              style={{
                color: theme.text.accent,
                fontSize: fs * 1.2,
                lineHeight: 1.6,
                fontFamily: 'system-ui, -apple-system, sans-serif',
                flexShrink: 0,
                transform: `scale(${bulletScale})`,
                display: 'inline-block',
                textShadow: bulletGlow > 0.4
                  ? `0 0 ${10 * scale}px ${theme.glow.color}, 0 0 ${20 * scale}px ${theme.glow.color}60`
                  : 'none',
              }}
            >
              {theme.pointBullet}
            </span>

            {/* Text */}
            <span
              style={{
                color: theme.text.primary,
                fontSize: fs,
                lineHeight: 1.6,
                fontFamily: 'system-ui, -apple-system, sans-serif',
              }}
            >
              {point}
            </span>
          </div>
        );
      })}
    </div>
  );
};
