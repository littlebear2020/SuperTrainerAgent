import React from 'react';
import { wrapSvgText } from '../../utils/text';

// ── OpenClaw product content ──────────────────────────────
const FEATURES = [
  { emoji: '⚡️', title: 'AI 助手网关', desc: '连接飞书/Discord/WhatsApp/Telegram 与大语言模型' },
  { emoji: '🔒', title: '数据本地处理', desc: '所有数据留在本机，隐私安全完全可控' },
  { emoji: '🚀', title: '一键安装', desc: '一行命令 + 交互式 onboard 配置向导，快速上手' },
  { emoji: '🤝', title: '多模型兼容', desc: '灵活接入通义千问、智谱GLM、DeepSeek 等国内大模型' },
  { emoji: '🔌', title: '插件系统', desc: '扩展搜索、日历、文件管理等能力' },
  { emoji: '⏰', title: '定时任务', desc: '支持 Cron 定时任务与 Heartbeat 心跳巡检' },
];

const KEYWORDS = [
  'Gateway + Agent + Plugin',
  '多渠道统一对接',
  '数据不出本机',
  '多模型自由切换',
  'Cron 定时任务',
  'Heartbeat 巡检',
  'openclaw doctor',
  'npm 一键安装',
  '开源社区驱动',
];

// ── Helpers ───────────────────────────────────────────────
const FF = 'system-ui, -apple-system, sans-serif';

/** Estimate pixel width: CJK = 1em, ASCII ≈ 0.6em */
function textWidth(text: string, fs: number): number {
  let w = 0;
  for (const ch of text) {
    w += ch.codePointAt(0)! > 0x2e80 ? fs : fs * 0.6;
  }
  return w;
}

/** Flow tags left-to-right with wrapping */
function flowTags(
  tags: string[],
  maxW: number,
  tagH: number,
  gap: number,
  fs: number,
): Array<{ x: number; y: number; w: number; text: string }> {
  const out: Array<{ x: number; y: number; w: number; text: string }> = [];
  let cx = 0,
    cy = 0;
  for (const t of tags) {
    const tw = textWidth(t, fs) + 20;
    if (cx + tw > maxW && cx > 0) {
      cx = 0;
      cy += tagH + gap;
    }
    out.push({ x: cx, y: cy, w: tw, text: t });
    cx += tw + gap;
  }
  return out;
}

// ── Component ─────────────────────────────────────────────
export const OpenClawPortraitCard: React.FC = () => {
  const W = 390;
  const PAD = 20;
  const CW = W - PAD * 2; // 350
  const CARD_PAD = 18;
  const INNER_W = CW - CARD_PAD * 2; // 314

  const ACCENT = '#60a5fa';
  const ACCENT2 = '#a78bfa';
  const TEXT = '#ffffff';
  const TEXT2 = '#a3bffa';

  // ════════════════════════════════════════════════════════
  //  LAYOUT — compute every Y position top-to-bottom
  // ════════════════════════════════════════════════════════
  let y = PAD; // 20

  // ── Header ──
  const titleFs = 28;
  const subFs = 13;
  const tagFs = 11;

  const iconX = PAD + 2;
  const titleX = PAD + 40;
  const titleY = y + titleFs; // 48
  y = titleY + 6;

  const subY = y + subFs;
  y = subY + 12;

  const tagPillText = 'Open Source · MIT License';
  const tagPillW = textWidth(tagPillText, tagFs) + 24;
  const tagPillTop = y;
  const tagTextY = y + tagFs + 4;
  y = tagPillTop + tagFs + 14;

  // ── Features card ──
  y += 4;
  const featCardY = y;
  const featIX = PAD + CARD_PAD; // inner X

  const featTitleFs = 15;
  const featFs = 14;
  const featLH = 1.5;
  const featDescW = INNER_W - 28; // emoji(24) + gap(4)

  const featSecTitleY = y + CARD_PAD + 18;
  y = featSecTitleY + 14;

  interface FeatItem {
    top: number;
    emoji: string;
    title: string;
    descLines: string[];
    descBaseY: number;
  }
  const featItems: FeatItem[] = [];

  for (const feat of FEATURES) {
    const top = y;
    const tby = y + featTitleFs;
    const lines = wrapSvgText(feat.desc, featDescW, featFs);
    const dby = tby + 6 + featFs;
    featItems.push({ top, emoji: feat.emoji, title: feat.title, descLines: lines, descBaseY: dby });
    y = dby + (lines.length - 1) * featFs * featLH + 12;
  }

  const featCardH = y - featCardY + CARD_PAD - 4;
  y = featCardY + featCardH + 14;

  // ── Keywords card ──
  const kwCardY = y;
  const kwIX = PAD + CARD_PAD;

  const kwSecTitleY = y + CARD_PAD + 18;
  const kwTagsStartY = kwSecTitleY + 14;

  const kwTagFs = 12;
  const kwTagH = 28;
  const kwTagGap = 8;
  const kwTags = flowTags(KEYWORDS, INNER_W, kwTagH, kwTagGap, kwTagFs);
  const kwTagsH = kwTags.length > 0 ? kwTags[kwTags.length - 1].y + kwTagH : 0;

  const kwCardH = CARD_PAD + 18 + 14 + kwTagsH + CARD_PAD;
  y = kwCardY + kwCardH + 14;

  // ── Footer ──
  const footerY = y + 8;
  const totalH = footerY + 16 + PAD;

  // ════════════════════════════════════════════════════════
  //  RENDER
  // ════════════════════════════════════════════════════════
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={W}
      height={totalH}
      viewBox={`0 0 ${W} ${totalH}`}
    >
      <defs>
        <linearGradient id="oc-bg" x1="0%" y1="0%" x2="50%" y2="100%">
          <stop offset="0%" stopColor="#0a1628" />
          <stop offset="50%" stopColor="#1a365d" />
          <stop offset="100%" stopColor="#0f2847" />
        </linearGradient>
        <radialGradient id="oc-glow1" cx="30%" cy="12%" r="60%">
          <stop offset="0%" stopColor="#60a5fa" stopOpacity={0.2} />
          <stop offset="100%" stopColor="#60a5fa" stopOpacity={0} />
        </radialGradient>
        <radialGradient id="oc-glow2" cx="70%" cy="85%" r="50%">
          <stop offset="0%" stopColor="#a78bfa" stopOpacity={0.12} />
          <stop offset="100%" stopColor="#a78bfa" stopOpacity={0} />
        </radialGradient>
        <linearGradient id="oc-divider" x1="0%" y1="0%" x2="100%" y2="0%">
          <stop offset="0%" stopColor="#60a5fa" stopOpacity={0.7} />
          <stop offset="100%" stopColor="#60a5fa" stopOpacity={0.05} />
        </linearGradient>
      </defs>

      {/* ── Background ── */}
      <rect width={W} height={totalH} fill="url(#oc-bg)" />
      <ellipse
        cx={W * 0.25} cy={totalH * 0.1}
        rx={W * 0.45} ry={totalH * 0.12}
        fill="url(#oc-glow1)"
      />
      <ellipse
        cx={W * 0.75} cy={totalH * 0.88}
        rx={W * 0.35} ry={totalH * 0.1}
        fill="url(#oc-glow2)"
      />

      {/* Grid */}
      <g opacity={0.03} stroke="#fff" strokeWidth={0.5}>
        {Array.from({ length: Math.ceil(W / 40) }, (_, i) => (
          <line key={`v${i}`} x1={i * 40} y1={0} x2={i * 40} y2={totalH} />
        ))}
        {Array.from({ length: Math.ceil(totalH / 40) }, (_, i) => (
          <line key={`h${i}`} x1={0} y1={i * 40} x2={W} y2={i * 40} />
        ))}
      </g>

      {/* ═══════ HEADER ═══════ */}
      {/* 🦀 icon */}
      <text x={iconX} y={titleY - 2} fontSize={28} fontFamily={FF}>
        🦀
      </text>

      {/* Title */}
      <text
        x={titleX} y={titleY}
        fill={TEXT} fontSize={titleFs} fontWeight="bold" fontFamily={FF}
      >
        OpenClaw
      </text>

      {/* Subtitle */}
      <text x={titleX} y={subY} fill={TEXT2} fontSize={subFs} fontFamily={FF}>
        开源 AI 助手框架 · 本地部署 · 隐私优先
      </text>

      {/* Tag pill */}
      <rect
        x={titleX} y={tagPillTop}
        width={tagPillW} height={tagFs + 10}
        rx={(tagFs + 10) / 2}
        fill="rgba(96,165,250,0.12)"
        stroke="rgba(96,165,250,0.25)"
        strokeWidth={0.5}
      />
      <text x={titleX + 12} y={tagTextY} fill={ACCENT} fontSize={tagFs} fontFamily={FF}>
        {tagPillText}
      </text>

      {/* ═══════ FEATURES CARD ═══════ */}
      <rect
        x={PAD} y={featCardY}
        width={CW} height={featCardH} rx={12}
        fill="rgba(255,255,255,0.05)"
        stroke="rgba(255,255,255,0.08)"
        strokeWidth={0.5}
      />

      {/* Accent bar + title */}
      <rect
        x={featIX}
        y={featCardY + CARD_PAD + 2}
        width={3} height={16} rx={1.5}
        fill={ACCENT}
      />
      <text
        x={featIX + 10} y={featSecTitleY}
        fill={ACCENT} fontSize={15} fontWeight="bold" fontFamily={FF}
      >
        核心特性
      </text>

      {/* Feature items */}
      {featItems.map((item, i) => (
        <g key={i}>
          {/* Emoji */}
          <text
            x={featIX + 2}
            y={item.top + featTitleFs}
            fontSize={18} fontFamily={FF}
          >
            {item.emoji}
          </text>
          {/* Title */}
          <text
            x={featIX + 28}
            y={item.top + featTitleFs}
            fill={TEXT} fontSize={featTitleFs} fontWeight="600" fontFamily={FF}
          >
            {item.title}
          </text>
          {/* Description */}
          <text
            x={featIX + 28}
            y={item.descBaseY}
            fill={TEXT2} fontSize={featFs} fontFamily={FF}
          >
            {item.descLines.map((line, li) => (
              <tspan
                key={li}
                x={featIX + 28}
                dy={li === 0 ? 0 : featFs * featLH}
              >
                {line}
              </tspan>
            ))}
          </text>
        </g>
      ))}

      {/* ═══════ KEYWORDS CARD ═══════ */}
      <rect
        x={PAD} y={kwCardY}
        width={CW} height={kwCardH} rx={12}
        fill="rgba(255,255,255,0.05)"
        stroke="rgba(255,255,255,0.08)"
        strokeWidth={0.5}
      />

      {/* Accent bar + title */}
      <rect
        x={kwIX}
        y={kwCardY + CARD_PAD + 2}
        width={3} height={16} rx={1.5}
        fill={ACCENT2}
      />
      <text
        x={kwIX + 10} y={kwSecTitleY}
        fill={ACCENT2} fontSize={15} fontWeight="bold" fontFamily={FF}
      >
        关键词
      </text>

      {/* Keyword tags */}
      {kwTags.map((tag, i) => (
        <g key={i}>
          <rect
            x={kwIX + tag.x}
            y={kwTagsStartY + tag.y}
            width={tag.w} height={kwTagH}
            rx={kwTagH / 2}
            fill="rgba(96,165,250,0.1)"
            stroke="rgba(255,255,255,0.08)"
            strokeWidth={0.5}
          />
          <text
            x={kwIX + tag.x + tag.w / 2}
            y={kwTagsStartY + tag.y + kwTagH / 2 + kwTagFs * 0.35}
            fill={TEXT}
            fontSize={kwTagFs}
            fontFamily={FF}
            textAnchor="middle"
          >
            {tag.text}
          </text>
        </g>
      ))}

      {/* ═══════ FOOTER ═══════ */}
      <line
        x1={PAD} y1={footerY - 6}
        x2={PAD + CW} y2={footerY - 6}
        stroke="url(#oc-divider)"
        strokeWidth={1}
        opacity={0.4}
      />
      <text
        x={W - PAD} y={footerY + 10}
        fill="rgba(255,255,255,0.25)"
        fontSize={11} fontFamily={FF} textAnchor="end"
      >
        github.com/openclaw/openclaw
      </text>
    </svg>
  );
};

export function renderOpenClawPortrait(): string {
  const { renderToStaticMarkup } = require('react-dom/server');
  return renderToStaticMarkup(React.createElement(OpenClawPortraitCard));
}
