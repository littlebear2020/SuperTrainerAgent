import React from 'react';
import type { CardData, CardSection } from '../../types/card';
import { resolveTheme } from '../../themes/index';
import { SvgBackground } from './SvgBackground';
import { SvgGlassPanel } from './SvgGlassPanel';
import { wrapSvgText } from '../../utils/text';

type Props = { cardData: CardData; width?: number; height?: number };

// ── Text measurement helpers ─────────────────────────────────
const textH = (text: string, maxW: number, fs: number, lh: number) =>
  wrapSvgText(text, maxW, fs).length * fs * lh;

const listH = (
  items: string[],
  maxW: number,
  fs: number,
  lh: number,
  gap: number,
) => items.reduce((s, t) => s + textH(t, maxW, fs, lh) + gap, -gap);

// ── Density levels ───────────────────────────────────────────
//   0 = luxury   (very little content → large fonts, generous spacing)
//   1 = comfort  (moderate content → balanced)
//   2 = compact  (lots of content → tighter)
//   3 = tight    (very dense → minimal spacing)
type DensityLevel = 0 | 1 | 2 | 3;

interface LayoutConfig {
  titleFs: number;
  subFs: number;
  ptFs: number;
  ptLH: number;
  ptGap: number;
  secPad: number;
  secTitleH: number;
  metricH: number;
  metricGap: number;
  afterDivider: number;
  afterMetrics: number;
  singleColMaxW: number;
}

const DENSITY_PRESETS: LayoutConfig[] = [
  // 0 – luxury (sparse content)
  {
    titleFs: 52, subFs: 24, ptFs: 28, ptLH: 1.72, ptGap: 26,
    secPad: 36, secTitleH: 44, metricH: 100, metricGap: 16,
    afterDivider: 40, afterMetrics: 28, singleColMaxW: 920,
  },
  // 1 – comfort (normal content)
  {
    titleFs: 48, subFs: 22, ptFs: 24, ptLH: 1.65, ptGap: 18,
    secPad: 28, secTitleH: 40, metricH: 88, metricGap: 16,
    afterDivider: 32, afterMetrics: 24, singleColMaxW: 1000,
  },
  // 2 – compact (dense content)
  {
    titleFs: 44, subFs: 20, ptFs: 21, ptLH: 1.55, ptGap: 12,
    secPad: 22, secTitleH: 36, metricH: 78, metricGap: 12,
    afterDivider: 24, afterMetrics: 18, singleColMaxW: 1100,
  },
  // 3 – tight (very dense)
  {
    titleFs: 38, subFs: 18, ptFs: 18, ptLH: 1.45, ptGap: 8,
    secPad: 18, secTitleH: 32, metricH: 70, metricGap: 10,
    afterDivider: 20, afterMetrics: 14, singleColMaxW: 1200,
  },
];

// ── Content analysis ─────────────────────────────────────────
function analyzeContent(
  sections: CardSection[],
  metricsCount: number,
  hasSub: boolean,
  hasFooter: boolean,
): { totalPoints: number; totalChars: number; factors: number } {
  const totalPoints = sections.reduce((s, sec) => s + sec.points.length, 0);
  const totalChars = sections.reduce(
    (s, sec) => s + sec.points.reduce((ss, p) => ss + p.length, 0),
    0,
  );
  // Factors that increase visual density
  let factors = 0;
  if (hasSub) factors += 0.5;
  if (hasFooter) factors += 0.5;
  factors += metricsCount * 0.5;
  factors += sections.length > 2 ? 1 : 0;
  return { totalPoints, totalChars, factors };
}

function classifyDensity(analysis: {
  totalPoints: number;
  totalChars: number;
  factors: number;
}): DensityLevel {
  const { totalPoints, totalChars, factors } = analysis;
  const score = totalPoints * 20 + totalChars * 0.3 + factors * 40;

  if (score < 100) return 0;  // luxury
  if (score < 250) return 1;  // comfort
  if (score < 500) return 2;  // compact
  return 3;                    // tight
}

// ── Main component ───────────────────────────────────────────
export const SvgKnowledgeCard: React.FC<Props> = ({
  cardData,
  width = 1920,
  height = 1080,
}) => {
  const theme = resolveTheme(cardData.theme);
  const id = cardData.id;

  // Layout constants (fixed regardless of density)
  const M = 32;
  const PAD = 64;
  const RX = 28;
  const px = M, py = M;
  const pw = width - M * 2;
  const ph = height - M * 2;
  const cx = px + PAD;
  const cw = pw - PAD * 2;
  const cb = py + ph - PAD;

  const accent = theme.text.accent;
  const accent2 = '#a78bfa';
  const accent3 = '#34d399';
  const ff = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';

  // ── Build sections ──
  const sections: CardSection[] = cardData.sections || [];
  if (sections.length === 0) {
    if (cardData.points.length > 0)
      sections.push({ title: '核心特性', points: cardData.points });
    if (cardData.tags && cardData.tags.length > 0)
      sections.push({ title: '关键词', points: cardData.tags });
  }
  const metrics = cardData.metrics || [];
  const footer = cardData.footer;

  // ── Classify content density ──
  const analysis = analyzeContent(
    sections,
    metrics.length,
    !!cardData.subtitle,
    !!footer,
  );
  let density = classifyDensity(analysis);

  // ── Compute layout for a given density level ──
  // Returns null if content overflows (used for auto-downgrade)
  function tryLayout(dl: DensityLevel) {
    const cfg = DENSITY_PRESETS[dl];
    const hasSub = !!cardData.subtitle;
    const iconText = cardData.icon || '📋';
    const bulletW = Math.ceil(cfg.ptFs * 1.4);

    // Header
    const headerTop = py + PAD;
    const titleBl = headerTop + cfg.titleFs;
    const subBl = hasSub ? titleBl + 14 + cfg.subFs : 0;
    const headerEnd = hasSub ? subBl + 10 : titleBl + 10;
    const dividerY = headerEnd + 14;
    const dividerH = 2;

    // Metrics
    let y = dividerY + dividerH + cfg.afterDivider;
    const metricsY = y;
    if (metrics.length > 0) y += cfg.metricH + cfg.afterMetrics;

    // Footer reserve
    const footerH = footer ? 72 : 32;
    const availH = cb - y - footerH;

    // Column strategy
    const colGap = 24;
    const numCols =
      sections.length >= 3 ? 3 : sections.length >= 2 ? 2 : 1;
    const colW =
      numCols === 1
        ? Math.min(cw, cfg.singleColMaxW)
        : (cw - (numCols - 1) * colGap) / numCols;

    // Section text width
    const secTextW = colW - bulletW - cfg.secPad * 2;
    const secFrameH = cfg.secTitleH + cfg.secPad * 2;

    const sectionHeights = sections.map((sec) => {
      const lh = listH(sec.points, secTextW, cfg.ptFs, cfg.ptLH, cfg.ptGap);
      return Math.max(lh + secFrameH, cfg.secTitleH + cfg.secPad * 4);
    });

    const totalSecH = sectionHeights.reduce((s, h) => s + h, 0);

    // Overflow check – always allow it (content will be clipped rather than crash)
    if (totalSecH > availH + 2 && dl < 3) return null; // try denser

    // Distribute extra space
    const extra = availH - totalSecH;
    const finalSecHs =
      extra > 0
        ? sectionHeights.map((h) => h + Math.floor(extra / sections.length))
        : sectionHeights;

    const secYs: number[] = [];
    let sY = y;
    for (let i = 0; i < sections.length; i++) {
      secYs.push(sY);
      sY += finalSecHs[i];
    }

    const footerLineY = cb - footerH + 10;
    const footerTextY = footerLineY + 26;

    return {
      cfg,
      hasSub,
      iconText,
      bulletW,
      headerTop,
      titleBl,
      subBl,
      dividerY,
      dividerH,
      metricsY,
      colW,
      numCols,
      secTextW,
      secFrameH,
      finalSecHs,
      secYs,
      footerLineY,
      footerTextY,
    };
  }

  // ── Auto-downgrade: try each density until content fits ──
  let layout = tryLayout(density);
  while (layout === null && density < 3) {
    density = (density + 1) as DensityLevel;
    layout = tryLayout(density);
  }
  // Fallback to tightest if still null (shouldn't happen normally)
  if (!layout) layout = tryLayout(3 as DensityLevel)!;

  const {
    cfg,
    hasSub,
    iconText,
    bulletW,
    headerTop,
    titleBl,
    subBl,
    dividerY,
    dividerH,
    metricsY,
    colW,
    numCols,
    secTextW,
    secFrameH,
    finalSecHs,
    secYs,
    footerLineY,
    footerTextY,
  } = layout;

  // ── Icon geometry ──
  const iconR = Math.ceil(cfg.titleFs * 0.54);
  const iconCx = cx + iconR + 4;
  const titleX = iconCx + iconR + 16;

  // ── RENDER ──
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
    >
      <defs>
        <linearGradient id={`hdr-${id}`} x1="0%" y1="0%" x2="100%" y2="0%">
          <stop offset="0%" stopColor={accent} stopOpacity={0.9} />
          <stop offset="50%" stopColor={accent2} stopOpacity={0.5} />
          <stop offset="100%" stopColor={accent} stopOpacity={0.05} />
        </linearGradient>
        <linearGradient id={`brand-${id}`} x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor={accent} stopOpacity={0.7} />
          <stop offset="100%" stopColor={accent2} stopOpacity={0.4} />
        </linearGradient>
      </defs>

      <SvgBackground width={width} height={height} theme={theme} id={id} />
      <SvgGlassPanel x={px} y={py} width={pw} height={ph} rx={RX} theme={theme} id={id} />

      {/* ═══════ HEADER ═══════ */}
      <circle
        cx={iconCx}
        cy={headerTop + cfg.titleFs * 0.55}
        r={iconR}
        fill="rgba(96,165,250,0.08)"
        stroke="rgba(96,165,250,0.2)"
        strokeWidth={1.5}
      />
      <text
        x={iconCx}
        y={headerTop + cfg.titleFs * 0.55 + cfg.titleFs * 0.18}
        textAnchor="middle"
        fill={accent}
        fontSize={Math.round(cfg.titleFs * 0.48)}
        fontFamily={ff}
      >
        {iconText}
      </text>

      {/* Title — supports multi-line wrapping */}
      {(() => {
        const titleMaxW = cw - (titleX - cx) - 20;
        const titleLines = wrapSvgText(cardData.title, titleMaxW, cfg.titleFs, 'bold');
        return titleLines.map((line, li) => (
          <text
            key={li}
            x={titleX}
            y={titleBl + li * cfg.titleFs * 1.2}
            fill={theme.text.primary}
            fontSize={cfg.titleFs}
            fontWeight="bold"
            fontFamily={ff}
          >
            {line}
          </text>
        ));
      })()}

      {/* Subtitle */}
      {hasSub && (() => {
        const titleLines = wrapSvgText(
          cardData.title,
          cw - (titleX - cx) - 20,
          cfg.titleFs,
          'bold',
        );
        const subY = titleBl + (titleLines.length - 1) * cfg.titleFs * 1.2 + 16 + cfg.subFs;
        return (
          <text x={titleX} y={subY} fill={theme.text.secondary}
            fontSize={cfg.subFs} fontFamily={ff}>
            {cardData.subtitle}
          </text>
        );
      })()}

      {/* Divider */}
      <rect
        x={cx}
        y={dividerY}
        width={Math.min(cw * 0.35, 420)}
        height={dividerH}
        rx={1}
        fill={`url(#hdr-${id})`}
      />
      <rect
        x={cx + Math.min(cw * 0.35, 420)}
        y={dividerY}
        width={cw - Math.min(cw * 0.35, 420)}
        height={dividerH}
        rx={1}
        fill="rgba(255,255,255,0.08)"
      />

      {/* ═══════ METRICS ROW ═══════ */}
      {metrics.length > 0 && (
        <g>
          {metrics.map((m, i) => {
            const mw = (cw - (metrics.length - 1) * cfg.metricGap) / metrics.length;
            const mx = cx + i * (mw + cfg.metricGap);
            return (
              <g key={i}>
                <rect
                  x={mx} y={metricsY} width={mw} height={cfg.metricH}
                  rx={14} fill="rgba(255,255,255,0.04)"
                  stroke="rgba(255,255,255,0.08)" strokeWidth={1}
                />
                <rect
                  x={mx + mw * 0.3} y={metricsY + 2}
                  width={mw * 0.4} height={2} rx={1}
                  fill={accent} opacity={0.5}
                />
                <text x={mx + 24} y={metricsY + cfg.metricH * 0.42}
                  fontSize={Math.round(cfg.metricH * 0.3)} fontFamily={ff}>
                  {m.icon}
                </text>
                <text x={mx + 56} y={metricsY + cfg.metricH * 0.38}
                  fill={accent} fontSize={12} fontWeight="700"
                  fontFamily={ff} letterSpacing="0.08em">
                  {m.label.toUpperCase()}
                </text>
                <text x={mx + 56} y={metricsY + cfg.metricH * 0.65}
                  fill={theme.text.primary} fontSize={Math.round(cfg.metricH * 0.22)}
                  fontWeight="600" fontFamily={ff}>
                  {m.value}
                </text>
              </g>
            );
          })}
        </g>
      )}

      {/* ═══════ CONTENT SECTIONS ═══════ */}
      {sections.map((sec, si) => {
        const sx =
          numCols === 1
            ? cx + (cw - colW) / 2
            : cx + si * (colW + 24);
        const sh = finalSecHs[si];
        const secAccent = [accent, accent2, accent3][si % 3];

        const ptHeights = sec.points.map((p) =>
          textH(p, secTextW, cfg.ptFs, cfg.ptLH),
        );
        const totalListH = ptHeights.reduce(
          (s, h) => s + h + cfg.ptGap,
          -cfg.ptGap,
        );
        const extraInSec = sh - secFrameH - totalListH;
        const extraPerPt =
          extraInSec > 0
            ? extraInSec / Math.max(sec.points.length, 1)
            : 0;

        return (
          <g key={si}>
            {/* Section panel */}
            <rect
              x={sx} y={secYs[si]} width={colW} height={sh}
              rx={18} fill="rgba(255,255,255,0.025)"
              stroke="rgba(255,255,255,0.06)" strokeWidth={1}
            />

            {/* Accent bar */}
            <rect
              x={sx + cfg.secPad}
              y={secYs[si] + cfg.secPad}
              width={4} height={Math.round(cfg.secTitleH * 0.6)}
              rx={2} fill={secAccent}
            />

            {/* Section title */}
            <text
              x={sx + cfg.secPad + 16}
              y={secYs[si] + cfg.secPad + Math.round(cfg.secTitleH * 0.48)}
              fill={secAccent}
              fontSize={Math.round(cfg.ptFs * 0.75)}
              fontWeight="700" fontFamily={ff}
            >
              {sec.title}
            </text>

            {/* Points */}
            {sec.points.map((item, pi) => {
              const prevH = ptHeights
                .slice(0, pi)
                .reduce((s, h) => s + h + cfg.ptGap, 0);
              const ptY = secYs[si] + secFrameH + prevH + pi * extraPerPt;
              const lines = wrapSvgText(item, secTextW, cfg.ptFs);

              return (
                <g key={pi}>
                  <text
                    x={sx + cfg.secPad + 4}
                    y={ptY}
                    fill={secAccent}
                    fontSize={Math.round(cfg.ptFs * 0.78)}
                    fontFamily={ff}
                    opacity={0.85}
                  >
                    ▸
                  </text>
                  <text
                    x={sx + cfg.secPad + bulletW}
                    y={ptY}
                    fill="rgba(255,255,255,0.88)"
                    fontSize={cfg.ptFs}
                    fontWeight="500"
                    fontFamily={ff}
                  >
                    {lines.map((line, li) => (
                      <tspan
                        key={li}
                        x={sx + cfg.secPad + bulletW}
                        dy={li === 0 ? 0 : cfg.ptFs * cfg.ptLH}
                      >
                        {line}
                      </tspan>
                    ))}
                  </text>
                </g>
              );
            })}
          </g>
        );
      })}

      {/* ═══════ FOOTER ═══════ */}
      <line
        x1={cx} y1={footerLineY} x2={cx + cw} y2={footerLineY}
        stroke="rgba(255,255,255,0.06)" strokeWidth={1}
      />

      {footer ? (
        <g>
          <text x={cx} y={footerTextY} fill="rgba(255,255,255,0.3)"
            fontSize={14} fontFamily={ff}>
            {footer.text}
          </text>
          {footer.link && (
            <text x={cx + cw} y={footerTextY} fill="rgba(255,255,255,0.18)"
              fontSize={13} textAnchor="end" fontFamily={ff}>
              {footer.link}
            </text>
          )}
          {footer.badges &&
            footer.badges.map((badge, i) => {
              const badges = footer.badges!;
              const bw = badge.label.length * 10 + 28;
              let bx = cx;
              for (let j = 0; j < i; j++)
                bx += badges[j].label.length * 10 + 38;
              return (
                <g key={i}>
                  <rect
                    x={bx} y={footerTextY + 8} width={bw} height={26}
                    rx={13}
                    fill={badge.color || 'rgba(96,165,250,0.1)'}
                    stroke="rgba(255,255,255,0.1)" strokeWidth={1}
                  />
                  <text x={bx + bw / 2} y={footerTextY + 26}
                    textAnchor="middle" fill={accent}
                    fontSize={12} fontWeight="600" fontFamily={ff}>
                    {badge.label}
                  </text>
                </g>
              );
            })}
        </g>
      ) : (
        <text x={cx + cw} y={footerTextY} fill="rgba(255,255,255,0.12)"
          fontSize={12} textAnchor="end" fontFamily={ff}>
          Knowledge Card
        </text>
      )}

      {/* Brand badge — bottom-right */}
      <g opacity={0.35}>
        <rect
          x={px + pw - 52} y={py + ph - 52}
          width={36} height={36} rx={10}
          fill={`url(#brand-${id})`}
        />
        <text
          x={px + pw - 34} y={py + ph - 28}
          textAnchor="middle" fill="white"
          fontSize={16} fontWeight="bold" fontFamily={ff}
        >
          OC
        </text>
      </g>
    </svg>
  );
};

export function renderCardToSvgString(
  cardData: CardData,
  width = 1920,
  height = 1080,
): string {
  const { renderToStaticMarkup } = require('react-dom/server');
  return renderToStaticMarkup(
    React.createElement(SvgKnowledgeCard, { cardData, width, height }),
  );
}
