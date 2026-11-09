import React from 'react';
import { useCurrentFrame, interpolate, Easing } from 'remotion';
import type { ThemeDefinition } from '../../types/card';

type GlassPanelProps = {
  theme: ThemeDefinition;
  children: React.ReactNode;
  frameStart?: number;
  scale?: number;
  exitStart?: number;
  totalFrames?: number;
};

const SHIMMER_WIDTH = 400;

export const GlassPanel: React.FC<GlassPanelProps> = ({
  theme,
  children,
  frameStart = 0,
  scale = 1,
  exitStart = 120,
  totalFrames = 150,
}) => {
  const frame = useCurrentFrame();

  const margin = Math.round(48 * scale);
  const padding = Math.round(48 * scale);
  const radius = Math.round(24 * scale);

  // Panel entrance: scale from 0.6 + heavy blur → sharp + overshoot
  const panelOpacity = interpolate(frame, [frameStart, frameStart + 30], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic),
  });

  const panelScale = interpolate(
    frame,
    [frameStart, frameStart + 15, frameStart + 30],
    [0.6, 1.08, 1],
    {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
    },
  );

  // Blur entrance: starts very blurry, sharpens to crisp
  const panelBlur = interpolate(frame, [frameStart, frameStart + 25], [20, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic),
  });

  // Exit animation
  const exitOpacity = interpolate(frame, [exitStart, totalFrames], [1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.in(Easing.cubic),
  });

  const exitScale = interpolate(frame, [exitStart, totalFrames], [1, 0.85], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.in(Easing.cubic),
  });

  // Glow pulse animation (continuous, stronger)
  const glowPulse = interpolate(frame, [0, 75, 150], [0.2, 0.6, 0.2]);

  // Light sweep / shimmer across panel surface
  const shimmerX = interpolate(frame, [frameStart + 15, frameStart + 70], [-SHIMMER_WIDTH, 2200], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.inOut(Easing.sin),
  });

  const shimmerOpacity = interpolate(frame, [frameStart + 15, frameStart + 35, frameStart + 55, frameStart + 70], [0, 0.25, 0.25, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // Border glow animation (stronger)
  const borderGlow = interpolate(frame, [0, 50, 100, 150], [0.1, 0.5, 0.1, 0.4], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  const finalOpacity = panelOpacity * exitOpacity;
  const finalScale = panelScale * exitScale;

  return (
    <div
      style={{
        position: 'absolute',
        top: margin,
        left: margin,
        right: margin,
        bottom: margin,
        padding,
        opacity: finalOpacity,
        transform: `scale(${finalScale})`,
        borderRadius: radius,
        background: theme.glass.background,
        border: `1px solid ${theme.glass.borderColor.replace(/[\d.]+\)$/, `${borderGlow})`)}`,
        backdropFilter: `blur(${theme.glass.blur}px)`,
        WebkitBackdropFilter: `blur(${theme.glass.blur}px)`,
        boxShadow: `${theme.glass.shadow}, 0 0 ${theme.glow.spread * 1.5}px ${theme.glow.color.replace(/[\d.]+\)$/, `${glowPulse})`)}`,
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
        boxSizing: 'border-box',
        filter: `blur(${panelBlur}px)`,
      }}
    >
      {/* Inner highlight - top edge */}
      <div
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          right: 0,
          height: '40%',
          borderRadius: `${radius}px ${radius}px 0 0`,
          background: 'linear-gradient(180deg, rgba(255,255,255,0.1) 0%, rgba(255,255,255,0) 100%)',
          pointerEvents: 'none',
        }}
      />

      {/* Light sweep shimmer effect */}
      <div
        style={{
          position: 'absolute',
          top: 0,
          left: shimmerX,
          width: SHIMMER_WIDTH,
          height: '100%',
          background: 'linear-gradient(90deg, transparent 0%, rgba(255,255,255,0.12) 40%, rgba(255,255,255,0.15) 50%, rgba(255,255,255,0.12) 60%, transparent 100%)',
          opacity: shimmerOpacity,
          pointerEvents: 'none',
          transform: 'skewX(-15deg)',
        }}
      />

      {/* Content */}
      <div style={{ position: 'relative', zIndex: 1, flex: 1 }}>{children}</div>
    </div>
  );
};
