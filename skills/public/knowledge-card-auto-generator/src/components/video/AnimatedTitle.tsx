import React from 'react';
import { useCurrentFrame, interpolate, Easing } from 'remotion';
import type { ThemeDefinition } from '../../types/card';

type AnimatedTitleProps = {
  title: string;
  subtitle?: string;
  theme: ThemeDefinition;
  frameStart?: number;
  scale?: number;
  exitStart?: number;
  totalFrames?: number;
};

export const AnimatedTitle: React.FC<AnimatedTitleProps> = ({
  title,
  subtitle,
  theme,
  frameStart = 10,
  scale = 1,
  exitStart = 120,
  totalFrames = 150,
}) => {
  const frame = useCurrentFrame();

  const titleFs = Math.round(42 * scale);
  const subFs = Math.round(22 * scale);

  // Title entrance: big slide + blur to sharp
  const titleOpacity = interpolate(frame, [frameStart, frameStart + 25], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic),
  });

  const titleTranslateY = interpolate(
    frame,
    [frameStart, frameStart + 12, frameStart + 25],
    [120 * scale, -8 * scale, 0],
    {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
    },
  );

  // Title blur entrance
  const titleBlur = interpolate(frame, [frameStart, frameStart + 20], [15, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic),
  });

  // Title glow effect (strong, dramatic)
  const titleGlow = interpolate(frame, [frameStart + 5, frameStart + 20, frameStart + 40], [0, 1, 0.6], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // Subtitle entrance
  const subtitleOpacity = interpolate(frame, [frameStart + 12, frameStart + 30], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic),
  });

  const subtitleTranslateY = interpolate(
    frame,
    [frameStart + 12, frameStart + 22, frameStart + 30],
    [80 * scale, -5 * scale, 0],
    {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
    },
  );

  const subtitleBlur = interpolate(frame, [frameStart + 12, frameStart + 25], [10, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic),
  });

  // Accent divider: grows from left to right with glow burst
  const dividerWidth = interpolate(frame, [frameStart + 8, frameStart + 35], [0, 120 * scale], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic),
  });

  const dividerGlow = interpolate(frame, [frameStart + 25, frameStart + 40, frameStart + 60], [0, 1, 0.4], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // Exit animations
  const exitOpacity = interpolate(frame, [exitStart, totalFrames], [1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.in(Easing.cubic),
  });

  const exitTranslateY = interpolate(frame, [exitStart, totalFrames], [0, -50 * scale], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.in(Easing.cubic),
  });

  return (
    <div style={{ marginBottom: 16 * scale }}>
      <div
        style={{
          opacity: titleOpacity * exitOpacity,
          transform: `translateY(${titleTranslateY + exitTranslateY}px)`,
          filter: `blur(${titleBlur}px)`,
        }}
      >
        <h1
          style={{
            color: theme.text.primary,
            fontSize: titleFs,
            fontWeight: 700,
            fontFamily: 'system-ui, -apple-system, sans-serif',
            margin: 0,
            lineHeight: 1.3,
            textShadow: `0 0 ${30 * scale * titleGlow}px ${theme.glow.color}, 0 0 ${60 * scale * titleGlow}px ${theme.glow.color}60`,
          }}
        >
          {title}
        </h1>
      </div>

      {subtitle && (
        <div
          style={{
            opacity: subtitleOpacity * exitOpacity,
            transform: `translateY(${subtitleTranslateY + exitTranslateY}px)`,
            filter: `blur(${subtitleBlur}px)`,
          }}
        >
          <p
            style={{
              color: theme.text.secondary,
              fontSize: subFs,
              fontFamily: 'system-ui, -apple-system, sans-serif',
              margin: `${10 * scale}px 0 0`,
              lineHeight: 1.4,
            }}
          >
            {subtitle}
          </p>
        </div>
      )}

      {/* Accent divider - grows from left with glow burst */}
      <div
        style={{
          marginTop: 18 * scale,
          width: dividerWidth,
          height: 4 * scale,
          borderRadius: 2 * scale,
          background: `linear-gradient(90deg, ${theme.text.accent}, ${theme.text.accent}60)`,
          opacity: titleOpacity * exitOpacity,
          boxShadow: dividerGlow > 0.3
            ? `0 0 ${12 * scale * dividerGlow}px ${theme.text.accent}80, 0 0 ${24 * scale * dividerGlow}px ${theme.text.accent}40`
            : 'none',
        }}
      />
    </div>
  );
};
