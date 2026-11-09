import React from 'react';
import type { ThemeDefinition } from '../../types/card';

type SvgGlassPanelProps = {
  x: number;
  y: number;
  width: number;
  height: number;
  rx?: number;
  theme: ThemeDefinition;
  id: string;
};

export const SvgGlassPanel: React.FC<SvgGlassPanelProps> = ({
  x,
  y,
  width,
  height,
  rx = 24,
  theme,
  id,
}) => {
  const blurFilterId = `glass-blur-${id}`;
  const shadowFilterId = `glass-shadow-${id}`;

  return (
    <g>
      <defs>
        {/* Glass blur filter - simulates frosted glass */}
        <filter id={blurFilterId} x="-5%" y="-5%" width="110%" height="110%">
          <feGaussianBlur in="SourceGraphic" stdDeviation={theme.glass.blur * 0.5} />
        </filter>

        {/* Shadow filter */}
        <filter id={shadowFilterId} x="-10%" y="-10%" width="120%" height="130%">
          <feDropShadow dx={0} dy={4} stdDeviation={12} floodColor="#000000" floodOpacity={0.37} />
        </filter>
      </defs>

      {/* Glow border effect */}
      <rect
        x={x - 1}
        y={y - 1}
        width={width + 2}
        height={height + 2}
        rx={rx + 1}
        fill="none"
        stroke={theme.glow.color}
        strokeWidth={1.5}
        opacity={0.3}
      />

      {/* Glass panel background */}
      <rect
        x={x}
        y={y}
        width={width}
        height={height}
        rx={rx}
        fill={theme.glass.background}
        stroke={theme.glass.borderColor}
        strokeWidth={1}
        filter={`url(#${shadowFilterId})`}
      />

      {/* Inner highlight - top edge */}
      <rect
        x={x + 1}
        y={y + 1}
        width={width - 2}
        height={height * 0.4}
        rx={rx}
        fill="url(#none)"
        opacity={0.05}
      >
        <linearGradient id={`inner-highlight-${id}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#ffffff" stopOpacity={0.15} />
          <stop offset="100%" stopColor="#ffffff" stopOpacity={0} />
        </linearGradient>
      </rect>
    </g>
  );
};
