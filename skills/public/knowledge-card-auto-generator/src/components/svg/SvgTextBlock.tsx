import React from 'react';
import { wrapSvgText } from '../../utils/text';

type SvgTextBlockProps = {
  x: number;
  y: number;
  maxWidth: number;
  text: string;
  fontSize: number;
  fill: string;
  fontWeight?: 'normal' | 'bold';
  fontFamily?: string;
  lineHeight?: number;
  anchor?: 'start' | 'middle';
};

export const SvgTextBlock: React.FC<SvgTextBlockProps> = ({
  x,
  y,
  maxWidth,
  text,
  fontSize,
  fill,
  fontWeight = 'normal',
  fontFamily = 'system-ui, -apple-system, sans-serif',
  lineHeight = 1.5,
  anchor = 'start',
}) => {
  const lines = wrapSvgText(text, maxWidth, fontSize, fontWeight);

  return (
    <text
      x={x}
      y={y}
      fill={fill}
      fontSize={fontSize}
      fontWeight={fontWeight}
      fontFamily={fontFamily}
      textAnchor={anchor}
    >
      {lines.map((line, i) => (
        <tspan
          key={i}
          x={anchor === 'middle' ? x : x}
          dy={i === 0 ? 0 : fontSize * lineHeight}
        >
          {line}
        </tspan>
      ))}
    </text>
  );
};
