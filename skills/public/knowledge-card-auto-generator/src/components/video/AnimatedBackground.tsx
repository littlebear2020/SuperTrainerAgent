import React from 'react';
import { useCurrentFrame, interpolate, Easing } from 'remotion';
import type { ThemeDefinition } from '../../types/card';

type AnimatedBackgroundProps = {
  width: number;
  height: number;
  theme: ThemeDefinition;
  exitStart?: number;
  totalFrames?: number;
};

// Pre-computed particle data - BIG and BRIGHT for visibility
const PARTICLES = Array.from({ length: 30 }, (_, i) => ({
  id: i,
  x: [0.05, 0.15, 0.28, 0.38, 0.5, 0.58, 0.65, 0.72, 0.8, 0.9, 0.95,
    0.08, 0.22, 0.32, 0.45, 0.6, 0.7, 0.78, 0.85, 0.92, 0.18, 0.48, 0.68, 0.88, 0.55,
    0.12, 0.35, 0.75, 0.62, 0.42][i],
  y: [0.1, 0.28, 0.18, 0.38, 0.08, 0.48, 0.22, 0.58, 0.32, 0.15,
    0.42, 0.62, 0.78, 0.52, 0.88, 0.2, 0.68, 0.4, 0.75, 0.92, 0.3, 0.6, 0.82, 0.45, 0.72,
    0.55, 0.35, 0.65, 0.85, 0.12][i],
  size: [6, 4, 8, 5, 7, 4, 6, 5, 8, 4, 6, 5, 7, 4, 6, 8, 5, 7, 4, 6,
    5, 7, 4, 6, 8, 5, 7, 4, 6, 5][i],
  speed: [0.6, 0.8, 0.4, 1.0, 0.7, 0.5, 0.9, 0.3, 0.8, 0.6, 0.5, 0.7, 0.4, 0.9, 0.6,
    0.8, 0.5, 0.7, 0.3, 0.9, 0.6, 0.4, 0.8, 0.5, 0.7, 0.6, 0.9, 0.4, 0.8, 0.5][i],
  delay: [0, 8, 4, 12, 6, 16, 3, 10, 7, 14, 2, 9, 5, 13, 1, 11, 8, 15, 3, 6,
    10, 4, 12, 7, 2, 14, 5, 9, 11, 3][i],
}));

export const AnimatedBackground: React.FC<AnimatedBackgroundProps> = ({
  width,
  height,
  theme,
  exitStart = 120,
  totalFrames = 150,
}) => {
  const frame = useCurrentFrame();

  // Background: start from pure black, flash to visible
  const bgBrightness = interpolate(frame, [0, 8, 20], [0, 0.3, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic),
  });

  // Background exit
  const bgExitBrightness = interpolate(frame, [exitStart, totalFrames], [1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.in(Easing.cubic),
  });

  const finalBgBrightness = bgBrightness * bgExitBrightness;

  // Glow orb slow movement with sine wave path
  const glowX = interpolate(frame, [0, 150], [width * 0.2, width * 0.4], {
    easing: Easing.inOut(Easing.sin),
  });
  const glowY = interpolate(frame, [0, 150], [height * 0.1, height * 0.15], {
    easing: Easing.inOut(Easing.sin),
  });

  // Second glow orb movement
  const glow2X = interpolate(frame, [0, 150], [width * 0.8, width * 0.65], {
    easing: Easing.inOut(Easing.sin),
  });
  const glow2Y = interpolate(frame, [0, 150], [height * 0.9, height * 0.85], {
    easing: Easing.inOut(Easing.sin),
  });

  // Gradient angle shift (wider range for visible effect)
  const gradientAngle = interpolate(frame, [0, 150], [120, 180], {
    easing: Easing.inOut(Easing.sin),
  });

  // Glow pulse (stronger)
  const glowPulse1 = interpolate(frame, [0, 50, 100, 150], [0.3, 0.7, 0.3, 0.6], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const glowPulse2 = interpolate(frame, [0, 40, 80, 120, 150], [0.2, 0.5, 0.15, 0.4, 0.25], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  const primary = theme.background.primary;
  const secondary = theme.background.secondary;

  return (
    <div
      style={{
        position: 'absolute',
        top: 0,
        left: 0,
        width,
        height,
      }}
    >
      {/* Pure black start, then reveal */}
      <div
        style={{
          position: 'absolute',
          inset: 0,
          background: '#000',
          opacity: 1 - finalBgBrightness,
          zIndex: 10,
        }}
      />

      {/* Main gradient background */}
      <div
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          width: '100%',
          height: '100%',
          opacity: finalBgBrightness,
          background: `linear-gradient(${gradientAngle}deg, ${primary} 0%, ${secondary} 40%, ${primary} 100%)`,
        }}
      />

      {/* Glow orb top-left with pulse */}
      <div
        style={{
          position: 'absolute',
          left: glowX - width * 0.35,
          top: glowY - height * 0.2,
          width: width * 0.7,
          height: height * 0.4,
          borderRadius: '50%',
          background: theme.glow.color,
          filter: `blur(${theme.glow.spread * 3}px)`,
          opacity: glowPulse1 * finalBgBrightness,
        }}
      />

      {/* Glow orb bottom-right with pulse */}
      <div
        style={{
          position: 'absolute',
          left: glow2X - width * 0.25,
          top: glow2Y - height * 0.15,
          width: width * 0.6,
          height: height * 0.35,
          borderRadius: '50%',
          background: theme.glow.color,
          filter: `blur(${theme.glow.spread * 3}px)`,
          opacity: glowPulse2 * finalBgBrightness,
        }}
      />

      {/* Subtle grid overlay */}
      <div
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          width: '100%',
          height: '100%',
          backgroundImage: `
            linear-gradient(rgba(255,255,255,0.03) 1px, transparent 1px),
            linear-gradient(90deg, rgba(255,255,255,0.03) 1px, transparent 1px)
          `,
          backgroundSize: '60px 60px',
          opacity: finalBgBrightness,
        }}
      />

      {/* Floating particles - BIG and GLOWING */}
      {PARTICLES.map((p) => {
        const particleStartFrame = 3 + p.delay;

        // Particle entrance
        const particleOpacity = interpolate(frame, [particleStartFrame, particleStartFrame + 20], [0, 1], {
          extrapolateLeft: 'clamp',
          extrapolateRight: 'clamp',
          easing: Easing.out(Easing.cubic),
        });

        // Exit fade
        const particleExit = interpolate(frame, [exitStart, totalFrames], [1, 0], {
          extrapolateLeft: 'clamp',
          extrapolateRight: 'clamp',
        });

        // Drift movement (bigger)
        const driftX = interpolate(frame, [particleStartFrame, 150], [0, 40 * p.speed], {
          extrapolateLeft: 'clamp',
          extrapolateRight: 'clamp',
        });
        const driftY = interpolate(frame, [particleStartFrame, 150], [0, -60 * p.speed], {
          extrapolateLeft: 'clamp',
          extrapolateRight: 'clamp',
        });

        // Twinkle effect (more dramatic)
        const twinkle = interpolate(
          frame,
          [particleStartFrame, particleStartFrame + 25, particleStartFrame + 50],
          [0.3, 1, 0.3],
          { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' },
        );

        const finalOpacity = particleOpacity * particleExit * twinkle * finalBgBrightness;

        return (
          <div
            key={p.id}
            style={{
              position: 'absolute',
              left: p.x * width + driftX,
              top: p.y * height + driftY,
              width: p.size * 3,
              height: p.size * 3,
              borderRadius: '50%',
              background: theme.text.accent,
              opacity: finalOpacity,
              boxShadow: `0 0 ${p.size * 6}px ${theme.text.accent}, 0 0 ${p.size * 12}px ${theme.text.accent}50`,
            }}
          />
        );
      })}

      {/* Vignette overlay */}
      <div
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          width: '100%',
          height: '100%',
          background: `radial-gradient(ellipse at center, transparent 40%, ${primary}cc 100%)`,
          opacity: finalBgBrightness,
        }}
      />
    </div>
  );
};
