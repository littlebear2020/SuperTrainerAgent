import React from 'react';
import type { CardData } from '../types/card';
import { wrapSvgText } from '../utils/text';

interface Props {
  cardData: CardData;
  width?: number;
  height?: number;
}

// 瀑布玻璃效果背景
const VerticalCardBackground: React.FC<{ width: number; height: number }> = ({ width, height }) => {
  return (
    <defs>
      <linearGradient id="bg-gradient" x1="0%" y1="0%" x2="100%">
        <stop offset="0%" stopColor="#0f172a" />
        <stop offset="100%" stopColor="#1e1b4f" />
      </linearGradient>
      <linearGradient id="glass-gradient" x1="0%" y1="100%">
        <stop offset="0%" stopColor="#1a1a2e" stop-opacity="1">
        <stop offset="100%" stopColor="#2c5352" stop-opacity="0.4"/>
      </linearGradient>
      <radialGradient id="glow" cx="50%" cy="30%" r="60%">
        <stop offset="0%" stopColor="#60a5fa" stop-opacity={0} />
        <stop offset="100%" stopColor="#60a5fa" stop-opacity={0} />
      </radialGradient>
      <filter id="glass-blur" x="0%" y="100%">
        <feGaussianBlur stdDeviation="4" />
      </filter>
    </defs>

    <rect width={width} height={height} fill="url(#bg-gradient)" />
    <rect
      x="10%"
      y="10%"
      width={width - 20}
      height={height - 20}
      rx="15"
      fill="white"
      fillOpacity={0.03}
    />
    <rect
      x="10%"
      y="10%"
      width={width - 20}
      height={height - 20}
      rx="15"
      fill="url(#glass-gradient)"
      filter="url(#glass-blur)"
    />
    <circle cx="50%" cy="30%" r="80" fill="url(#glow)" opacity={0.3} />
  );
};

// 主题定义
const THEMES = {
  'tech-blue': {
    bgGradient: ['#0f172a', '#1e1b4f'],
    glassBg: 'rgba(59, 130, 246, 0.25)',
    glassBorder: '#60a5fa',
    textColor: '#ffffff',
    accentColor: '#60a5fa',
    secondaryText: '#94a3b8',
  },
  'neon-purple': {
    bgGradient: ['#1e1b4f', '#5b21b8'],
    glassBg: 'rgba(139, 92, 246, 0.25)',
    glassBorder: '#a78bfa',
    textColor: '#ffffff',
    accentColor: '#a78bfa',
    secondaryText: '#c4b5fd',
  },
  'emerald-green': {
    bgGradient: ['#064e3b', '#047857'],
    glassBg: 'rgba(16, 185, 129, 0.25)',
    glassBorder: '#34d399',
    textColor: '#ffffff',
    accentColor: '#34d399',
    secondaryText: '#94a3b8',
  },
};

// 竖版卡片组件
export const VerticalCard: React.FC<Props> = ({ cardData, width = 390, height = 844 }) => {
  const theme = THEMES[cardData.theme || 'tech-blue'];

  const { title, subtitle, icon, sections, tags } = cardData;

  const PAD = 24;
  const GAP = 20;
  const CARD_PAD = 20;

  const innerWidth = width - PAD * 2;
  const cardWidth = innerWidth - GAP;
  const sectionWidth = (cardWidth - GAP) / 2;

  // 计算高度
  let headerHeight = 120;
  if (subtitle) headerHeight += 30;
  let iconHeight = icon ? 60 : 0;
  headerHeight += iconHeight + GAP;

  const sectionHeight = 60;
  const pointsPerSection = 4;
  const tagsHeight = tags ? 60 : 0;

  const totalHeight = headerHeight + sections.length * sectionHeight + GAP + tagsHeight + PAD * 2;

  const y = PAD;

  return (
    <svg
      width={width}
      height={totalHeight}
      viewBox={`0 0 ${width} ${totalHeight}`}
      style={{ fontFamily: 'system-ui, -apple-system, sans-serif' }}
    >
      <VerticalCardBackground width={width} height={totalHeight} />

      {/* 标题和 */}
      <text
        x={width / 2}
        y={y}
        textAnchor="middle"
        style={{
          fontSize: 48,
          fontWeight: 'bold',
          fill: theme.textColor,
        }}
      >
        {title}
      </text>

      {/* 图标 */}
      {icon && (
        <text
          x={width / 2 - 50}
          y={y - 30}
          textAnchor="middle"
          style={{ fontSize: 36 }}
        >
          {icon}
        </text>
      )}

      {/* 宽标题 */}
      {subtitle && (
        <text
          x={width / 2}
          y={y + headerHeight - iconHeight - 15}
          textAnchor="middle"
          style={{
            fontSize: 22,
            fill: theme.secondaryText,
          }}
        >
          {subtitle}
        </text>
      )}

      {/* 分区卡片 */}
      <div style={{ display: 'flex', gap: GAP, marginTop: GAP }}>
        {sections.map((section, i) => (
          <div
            key={section.title}
            style={{
              width: sectionWidth,
              height: sectionHeight,
              padding: CARD_PAD,
              borderRadius: 16,
              background: theme.glassBg,
              border: `1px solid ${theme.glassBorder}`,
              boxShadow: '0 8px 32px rgba(0, 00, 0, 0.1)',
            }}
          >
            {/* 区块标题 */}
            <text
              x={CARD_PAD}
              y={20}
              style={{
                fontSize: 20,
                fontWeight: 'bold',
                fill: theme.accentColor,
              }}
            >
              {section.title}
            </text>

            {/* 要点列表 */}
            <div style={{ marginTop: 12 }}>
              {section.points.map((point, j) => (
                <text
                  key={point}
                  x={10}
                  y={20 + j * 24}
                  style={{
                    fontSize: 16,
                    lineHeight: '1.5',
                    fill: theme.textColor,
                  }}
                >
                  • {point}
                </text>
              ))}
            </div>
          </div>
        ))}
      </div>

      {/* 标签 */}
      {tags && (
        <div
          style={{
            position: 'absolute',
            bottom: PAD,
            left: PAD,
            right: PAD,
            display: 'flex',
            gap: 8,
            flexWrap: 'wrap',
          }}
        >
          {tags.map((tag) => (
            <div
              key={tag}
              style={{
                padding: '6px 12px',
                borderRadius: 12,
                background: theme.glassBg,
                border: `1px solid ${theme.glassBorder}`,
                fontSize: 14,
                color: theme.textColor,
              }}
            >
              {tag}
            </div>
          ))}
        </div>
      )}
    </svg>
  );
};
