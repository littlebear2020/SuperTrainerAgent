import React from 'react';
import type { ThemeDefinition } from '../../types/card';

type SvgBackgroundProps = {
  width: number;
  height: number;
  theme: ThemeDefinition;
  id: string;
};

export const SvgBackground: React.FC<SvgBackgroundProps> = ({ width, height, theme, id }) => {
  const gradientId = `bg-gradient-${id}`;
  const glowId = `glow-${id}`;

  return (
    <g>
      <defs>
        <linearGradient id={gradientId} x1="0%" y1="0%" x2="100%" y2="100%">
          {theme.background.gradientStops.map((stop, i) => (
            <stop key={i} offset={`${stop.offset * 100}%`} stopColor={stop.color} />
          ))}
        </linearGradient>
        <radialGradient id={glowId} cx="50%" cy="30%" r="60%">
          <stop offset="0%" stopColor={theme.glow.color} stopOpacity={theme.glow.intensity} />
          <stop offset="100%" stopColor={theme.glow.color} stopOpacity={0} />
        </radialGradient>
      </defs>

      {/* Main background */}
      <rect width={width} height={height} fill={`url(#${gradientId})`} />

      {/* Decorative glow orb top */}
      <ellipse
        cx={width * 0.3}
        cy={height * 0.15}
        rx={width * 0.35}
        ry={height * 0.2}
        fill={`url(#${glowId})`}
        opacity={0.5}
      />

      {/* Decorative glow orb bottom */}
      <ellipse
        cx={width * 0.75}
        cy={height * 0.85}
        rx={width * 0.3}
        ry={height * 0.18}
        fill={`url(#${glowId})`}
        opacity={0.3}
      />

      {/* Subtle grid pattern */}
      <g opacity={0.04} stroke="#ffffff" strokeWidth={0.5}>
        {Array.from({ length: Math.floor(width / 60) }, (_, i) => (
          <line key={`v${i}`} x1={i * 60} y1={0} x2={i * 60} y2={height} />
        ))}
        {Array.from({ length: Math.floor(height / 60) }, (_, i) => (
          <line key={`h${i}`} x1={0} y1={i * 60} x2={width} y2={i * 60} />
        ))}
      </g>
    </g>
  );
};
