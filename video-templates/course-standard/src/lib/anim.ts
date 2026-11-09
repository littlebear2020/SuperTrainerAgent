import { Easing, interpolate } from "remotion";

/**
 * 入场动效工具。所有动效都在 30 帧内落定，
 * 保证 frame=60 取静帧时画面已完全稳定。
 */

/**
 * 动效工具包：transitionsOn 时全部退化为恒等样式（版式自带淡入淡出
 * 与 TransitionSeries 转场叠加会退化为闪切——T2 红线 2）。
 * 用法：const A = animKit(transitionsOn); ...A.rise(frame, 8)。
 */
export const animKit = (disabled: boolean) =>
  disabled
    ? {
        rise: (): React.CSSProperties => ({}),
        fade: (): React.CSSProperties => ({}),
        slideX: (): React.CSSProperties => ({}),
        popBullet: (): React.CSSProperties => ({}),
        progress: (): number => 1,
      }
    : {
        rise,
        fade,
        slideX,
        popBullet,
        progress,
      };

const clamp = {
  extrapolateLeft: "clamp",
  extrapolateRight: "clamp",
} as const;

/** 上移淡入（页面元素默认入场） */
export const rise = (
  frame: number,
  delay: number,
  duration = 22,
  dy = 36,
): React.CSSProperties => {
  const p = interpolate(frame, [delay, delay + duration], [0, 1], {
    ...clamp,
    easing: Easing.out(Easing.cubic),
  });
  return { opacity: p, transform: `translateY(${(1 - p) * dy}px)` };
};

/** 纯淡入 */
export const fade = (
  frame: number,
  delay: number,
  duration = 20,
): React.CSSProperties => {
  const p = interpolate(frame, [delay, delay + duration], [0, 1], clamp);
  return { opacity: p };
};

/** 横向滑入（dx>0 从右来，dx<0 从左来） */
export const slideX = (
  frame: number,
  delay: number,
  duration = 24,
  dx = 60,
): React.CSSProperties => {
  const p = interpolate(frame, [delay, delay + duration], [0, 1], {
    ...clamp,
    easing: Easing.out(Easing.cubic),
  });
  return { opacity: p, transform: `translateX(${(1 - p) * dx}px)` };
};

/** bullet 回弹：横向入场带一次小幅过冲后归位（克制的回弹感） */
export const popBullet = (
  frame: number,
  delay: number,
  duration = 20,
  dx = 48,
): React.CSSProperties => {
  const p = interpolate(frame, [delay, delay + duration], [0, 1], {
    ...clamp,
    easing: Easing.out(Easing.cubic),
  });
  const over = interpolate(p, [0, 0.7, 1], [0, 1.06, 1], clamp);
  return { opacity: p, transform: `translateX(${(1 - over) * dx}px)` };
};

/** 0→1 进度值（用于柱子 scaleY 升起等场景） */
export const progress = (
  frame: number,
  delay: number,
  duration = 24,
): number => {
  return interpolate(frame, [delay, delay + duration], [0, 1], {
    ...clamp,
    easing: Easing.out(Easing.cubic),
  });
};
