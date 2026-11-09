import React from 'react';
import { useCurrentFrame, interpolate, Easing } from 'remotion';

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

const ACCENT = '#60a5fa';
const ACCENT2 = '#a78bfa';
const TEXT = '#ffffff';
const TEXT2 = '#a3bffa';
const FF = 'system-ui, -apple-system, sans-serif';

// Exit timing (portrait is 180 frames = 6 sec)
const EXIT_START = 160;
const TOTAL_FRAMES = 180;

/** Flow tags left-to-right with wrapping */
function flowTags(
  tags: string[],
  maxW: number,
  tagH: number,
  gap: number,
  fs: number,
): Array<{ x: number; y: number; w: number; text: string }> {
  const out: Array<{ x: number; y: number; w: number; text: string }> = [];
  let cx = 0, cy = 0;
  for (const t of tags) {
    const tw = estimateTextWidth(t, fs) + 20;
    if (cx + tw > maxW && cx > 0) {
      cx = 0;
      cy += tagH + gap;
    }
    out.push({ x: cx, y: cy, w: tw, text: t });
    cx += tw + gap;
  }
  return out;
}

function estimateTextWidth(text: string, fs: number): number {
  let w = 0;
  for (const ch of text) {
    w += ch.codePointAt(0)! > 0x2e80 ? fs : fs * 0.6;
  }
  return w;
}

// Pre-computed particles for portrait card
const PARTICLES = Array.from({ length: 12 }, (_, i) => ({
  id: i,
  x: [0.1, 0.3, 0.5, 0.7, 0.9, 0.15, 0.45, 0.65, 0.85, 0.25, 0.55, 0.78][i],
  y: [0.05, 0.2, 0.12, 0.3, 0.08, 0.45, 0.55, 0.15, 0.4, 0.65, 0.35, 0.5][i],
  size: [2, 3, 2, 2, 3, 2, 2, 3, 2, 3, 2, 2][i],
  speed: [0.3, 0.5, 0.2, 0.4, 0.3, 0.5, 0.25, 0.35, 0.45, 0.2, 0.4, 0.3][i],
  delay: [0, 8, 4, 12, 6, 16, 2, 10, 14, 5, 11, 7][i],
}));

// ── Component ─────────────────────────────────────────────
export const AnimatedOpenClawPortraitCard: React.FC<{
  width?: number;
  height?: number;
}> = ({ width = 390, height = 780 }) => {
  const frame = useCurrentFrame();

  // Canvas constants
  const PAD = 20;
  const CW = width - PAD * 2;
  const CARD_PAD = 18;
  const INNER_W = CW - CARD_PAD * 2;

  // ── Card-level entrance animations with spring easing ──
  const cardOpacity = interpolate(frame, [0, 20], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic),
  });

  const cardScale = interpolate(frame, [0, 10, 20], [0.92, 1.02, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // Card exit
  const cardExitOpacity = interpolate(frame, [EXIT_START, TOTAL_FRAMES], [1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.in(Easing.cubic),
  });
  const cardExitScale = interpolate(frame, [EXIT_START, TOTAL_FRAMES], [1, 0.96], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.in(Easing.cubic),
  });

  // ── Background gradient angle animation ──
  const gradientAngle = interpolate(frame, [0, 180], [135, 160], {
    easing: Easing.inOut(Easing.sin),
  });

  // ── Glow pulse ──
  const glowPulse = interpolate(frame, [0, 60, 120, 180], [0.15, 0.25, 0.15, 0.2], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // Second glow
  const glow2X = interpolate(frame, [0, 180], [width * 0.6, width * 0.55], {
    easing: Easing.inOut(Easing.sin),
  });
  const glow2Y = interpolate(frame, [0, 180], [height * 0.75, height * 0.8], {
    easing: Easing.inOut(Easing.sin),
  });

  // ── Header animations with spring ──
  const headerOpacity = interpolate(frame, [10, 30], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic),
  });
  const headerY = interpolate(frame, [10, 18, 30], [25, -3, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.quad),
  });

  // Header glow
  const headerGlow = interpolate(frame, [20, 35], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // ── Features card animation with spring ──
  const featCardOpacity = interpolate(frame, [25, 45], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic),
  });
  const featCardY = interpolate(frame, [25, 33, 45], [18, -2, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.quad),
  });

  // Features card shimmer
  const featShimmerX = interpolate(frame, [30, 80], [-60, width], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.inOut(Easing.sin),
  });
  const featShimmerOpacity = interpolate(frame, [30, 45, 65, 80], [0, 0.1, 0.1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // ── Keywords card animation with spring ──
  const kwCardOpacity = interpolate(frame, [70, 90], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic),
  });
  const kwCardY = interpolate(frame, [70, 78, 90], [18, -2, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.quad),
  });

  // ── Feature items staggered ──
  const featTitleFs = 15;
  const featFs = 14;
  const featLH = 1.5;

  // ── Keyword tags layout ──
  const kwTagFs = 12;
  const kwTagH = 28;
  const kwTagGap = 8;
  const kwTags = flowTags(KEYWORDS, INNER_W, kwTagH, kwTagGap, kwTagFs);

  // ── Footer animation ──
  const footerOpacity = interpolate(frame, [100, 120], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic),
  });

  // ── Accent divider grows ──
  const dividerWidth = interpolate(frame, [100, 115], [0, 100], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic),
  });

  return (
    <div
      style={{
        position: 'relative',
        width,
        height,
        overflow: 'hidden',
        fontFamily: FF,
        opacity: cardOpacity * cardExitOpacity,
        transform: `scale(${cardScale * cardExitScale})`,
      }}
    >
      {/* Background with animated gradient */}
      <div style={{
        position: 'absolute',
        inset: 0,
        background: `linear-gradient(${gradientAngle}deg, #0a1628 0%, #1a365d 50%, #0f2847 100%)`,
      }} />

      {/* Top-left glow with pulse */}
      <div style={{
        position: 'absolute',
        left: width * -0.1,
        top: height * -0.05,
        width: width * 0.8,
        height: height * 0.25,
        borderRadius: '50%',
        background: `radial-gradient(ellipse, rgba(96,165,250,${glowPulse}) 0%, rgba(96,165,250,0) 70%)`,
        filter: `blur(${width * 0.15}px)`,
      }} />

      {/* Bottom-right glow with movement */}
      <div style={{
        position: 'absolute',
        left: glow2X - width * 0.2,
        top: glow2Y - height * 0.08,
        width: width * 0.5,
        height: height * 0.15,
        borderRadius: '50%',
        background: 'radial-gradient(ellipse, rgba(167,139,250,0.15) 0%, rgba(167,139,250,0) 70%)',
        filter: `blur(${width * 0.12}px)`,
      }} />

      {/* Grid */}
      <div style={{
        position: 'absolute',
        inset: 0,
        backgroundImage: `linear-gradient(rgba(255,255,255,0.03) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.03) 1px, transparent 1px)`,
        backgroundSize: '40px 40px',
      }} />

      {/* Floating particles */}
      {PARTICLES.map((p) => {
        const pStart = 5 + p.delay;
        const pOpacity = interpolate(frame, [pStart, pStart + 12], [0, 0.6], {
          extrapolateLeft: 'clamp',
          extrapolateRight: 'clamp',
          easing: Easing.out(Easing.cubic),
        });
        const pExit = interpolate(frame, [EXIT_START, TOTAL_FRAMES], [1, 0], {
          extrapolateLeft: 'clamp',
          extrapolateRight: 'clamp',
        });
        const driftX = interpolate(frame, [pStart, 180], [0, 15 * p.speed], {
          extrapolateLeft: 'clamp',
          extrapolateRight: 'clamp',
        });
        const driftY = interpolate(frame, [pStart, 180], [0, -20 * p.speed], {
          extrapolateLeft: 'clamp',
          extrapolateRight: 'clamp',
        });
        const twinkle = interpolate(
          frame,
          [pStart, pStart + 15, pStart + 30, pStart + 45],
          [0.4, 0.9, 0.4, 0.8],
          { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' },
        );

        return (
          <div
            key={p.id}
            style={{
              position: 'absolute',
              left: p.x * width + driftX,
              top: p.y * height + driftY,
              width: p.size * 2,
              height: p.size * 2,
              borderRadius: '50%',
              background: ACCENT,
              opacity: pOpacity * pExit * twinkle,
              boxShadow: pOpacity * twinkle > 0.4
                ? `0 0 ${p.size * 2}px ${ACCENT}40`
                : 'none',
            }}
          />
        );
      })}

      {/* Vignette */}
      <div style={{
        position: 'absolute',
        inset: 0,
        background: 'radial-gradient(ellipse at center, transparent 50%, #0a162890 100%)',
      }} />

      {/* ═══ HEADER ═══ */}
      <div style={{
        position: 'relative',
        padding: `${PAD}px ${PAD}px 0`,
        opacity: headerOpacity,
        transform: `translateY(${headerY}px)`,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 28 }}>🦀</span>
          <span
            style={{
              color: TEXT,
              fontSize: 28,
              fontWeight: 'bold',
              textShadow: headerGlow > 0.5
                ? `0 0 15px rgba(96,165,250,0.5), 0 0 30px rgba(96,165,250,0.2)`
                : 'none',
            }}
          >
            OpenClaw
          </span>
        </div>
        <div style={{ color: TEXT2, fontSize: 13, marginTop: 6, marginLeft: 36 }}>
          开源 AI 助手框架 · 本地部署 · 隐私优先
        </div>
        <div style={{
          display: 'inline-block',
          marginTop: 10,
          marginLeft: 36,
          padding: '4px 14px',
          borderRadius: 12,
          background: 'rgba(96,165,250,0.12)',
          border: '1px solid rgba(96,165,250,0.25)',
          color: ACCENT,
          fontSize: 11,
        }}>
          Open Source · MIT License
        </div>
      </div>

      {/* ═══ FEATURES CARD ═══ */}
      <div style={{
        position: 'relative',
        margin: `14px ${PAD}px 0`,
        padding: CARD_PAD,
        borderRadius: 12,
        background: 'rgba(255,255,255,0.05)',
        border: '1px solid rgba(255,255,255,0.08)',
        opacity: featCardOpacity,
        transform: `translateY(${featCardY}px)`,
        overflow: 'hidden',
      }}>
        {/* Shimmer sweep */}
        <div style={{
          position: 'absolute',
          top: 0,
          left: featShimmerX,
          width: 80,
          height: '100%',
          background: 'linear-gradient(90deg, transparent, rgba(255,255,255,0.06), transparent)',
          opacity: featShimmerOpacity,
          pointerEvents: 'none',
          transform: 'skewX(-15deg)',
        }} />

        {/* Section title */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
          <div style={{
            width: 3,
            height: 16,
            borderRadius: 2,
            background: ACCENT,
            boxShadow: '0 0 6px rgba(96,165,250,0.4)',
          }} />
          <span style={{ color: ACCENT, fontSize: 15, fontWeight: 'bold' }}>核心特性</span>
        </div>

        {/* Feature items with spring slide-in */}
        {FEATURES.map((feat, i) => {
          const itemStart = 35 + i * 6;
          const itemOpacity = interpolate(frame, [itemStart, itemStart + 12], [0, 1], {
            extrapolateLeft: 'clamp',
            extrapolateRight: 'clamp',
            easing: Easing.out(Easing.cubic),
          });
          const itemX = interpolate(frame, [itemStart, itemStart + 5, itemStart + 12], [-20, 3, 0], {
            extrapolateLeft: 'clamp',
            extrapolateRight: 'clamp',
            easing: Easing.out(Easing.quad),
          });

          // Emoji bounce
          const emojiScale = interpolate(frame, [itemStart, itemStart + 4, itemStart + 8], [0, 1.3, 1], {
            extrapolateLeft: 'clamp',
            extrapolateRight: 'clamp',
          });

          // Exit
          const itemExit = interpolate(frame, [EXIT_START, TOTAL_FRAMES], [1, 0], {
            extrapolateLeft: 'clamp',
            extrapolateRight: 'clamp',
            easing: Easing.in(Easing.cubic),
          });
          const itemExitX = interpolate(frame, [EXIT_START, TOTAL_FRAMES], [0, 15], {
            extrapolateLeft: 'clamp',
            extrapolateRight: 'clamp',
            easing: Easing.in(Easing.cubic),
          });

          return (
            <div
              key={i}
              style={{
                opacity: itemOpacity * itemExit,
                transform: `translateX(${itemX + itemExitX}px)`,
                marginBottom: i < FEATURES.length - 1 ? 10 : 0,
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ fontSize: 18, display: 'inline-block', transform: `scale(${emojiScale})` }}>{feat.emoji}</span>
                <span style={{ color: TEXT, fontSize: featTitleFs, fontWeight: 600 }}>{feat.title}</span>
              </div>
              <div style={{ color: TEXT2, fontSize: featFs, lineHeight: featLH, marginLeft: 28, marginTop: 2 }}>
                {feat.desc}
              </div>
            </div>
          );
        })}
      </div>

      {/* ═══ KEYWORDS CARD ═══ */}
      <div style={{
        position: 'relative',
        margin: `14px ${PAD}px 0`,
        padding: CARD_PAD,
        borderRadius: 12,
        background: 'rgba(255,255,255,0.05)',
        border: '1px solid rgba(255,255,255,0.08)',
        opacity: kwCardOpacity,
        transform: `translateY(${kwCardY}px)`,
      }}>
        {/* Section title */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
          <div style={{
            width: 3,
            height: 16,
            borderRadius: 2,
            background: ACCENT2,
            boxShadow: '0 0 6px rgba(167,139,250,0.4)',
          }} />
          <span style={{ color: ACCENT2, fontSize: 15, fontWeight: 'bold' }}>关键词</span>
        </div>

        {/* Tags with pop-in */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: kwTagGap }}>
          {kwTags.map((tag, i) => {
            const tagStart = 90 + i * 4;
            const tagScale = interpolate(frame, [tagStart, tagStart + 3, tagStart + 6, tagStart + 9], [0, 1.15, 0.92, 1], {
              extrapolateLeft: 'clamp',
              extrapolateRight: 'clamp',
            });
            const tagOpacity = interpolate(frame, [tagStart, tagStart + 6], [0, 1], {
              extrapolateLeft: 'clamp',
              extrapolateRight: 'clamp',
              easing: Easing.out(Easing.cubic),
            });
            const tagGlow = interpolate(frame, [tagStart, tagStart + 6, tagStart + 15], [1, 0.4, 0], {
              extrapolateLeft: 'clamp',
              extrapolateRight: 'clamp',
            });

            // Exit
            const tagExit = interpolate(frame, [EXIT_START, TOTAL_FRAMES], [1, 0], {
              extrapolateLeft: 'clamp',
              extrapolateRight: 'clamp',
              easing: Easing.in(Easing.cubic),
            });
            const tagExitScale = interpolate(frame, [EXIT_START, TOTAL_FRAMES], [1, 0.8], {
              extrapolateLeft: 'clamp',
              extrapolateRight: 'clamp',
              easing: Easing.in(Easing.back(1.5)),
            });

            return (
              <span
                key={i}
                style={{
                  padding: `${(kwTagH - kwTagFs) / 2}px 10px`,
                  borderRadius: kwTagH / 2,
                  background: 'rgba(96,165,250,0.1)',
                  border: '1px solid rgba(255,255,255,0.08)',
                  color: TEXT,
                  fontSize: kwTagFs,
                  lineHeight: 1,
                  display: 'inline-block',
                  opacity: tagOpacity * tagExit,
                  transform: `scale(${tagScale * tagExitScale})`,
                  boxShadow: tagGlow > 0.3
                    ? `0 0 8px rgba(96,165,250,0.3)`
                    : 'none',
                }}
              >
                {tag.text}
              </span>
            );
          })}
        </div>
      </div>

      {/* ═══ FOOTER ═══ */}
      <div style={{
        position: 'relative',
        margin: `14px ${PAD}px 0`,
        opacity: footerOpacity,
      }}>
        <div style={{
          height: 1,
          width: `${dividerWidth}%`,
          background: `linear-gradient(90deg, ${ACCENT}B3 0%, ${ACCENT}0D 100%)`,
          marginBottom: 8,
        }} />
        <div style={{ textAlign: 'right', color: 'rgba(255,255,255,0.25)', fontSize: 11 }}>
          github.com/openclaw/openclaw
        </div>
      </div>
    </div>
  );
};
