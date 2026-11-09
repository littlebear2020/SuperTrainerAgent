/** 分镜导航条纯逻辑：帧布局（像素坐标）与播放时间->帧映射。 */

/** 每秒占多少像素（决定分镜条视觉密度）。 */
export const PX_PER_SECOND = 24;
/** 最小帧宽--超短帧也保持可点击且帧号/徽标可读（ThumbClip h-14=56px，96px 接近 16:9）。 */
export const MIN_FRAME_WIDTH_PX = 96;
/** 帧间距（gap-1=4px），计入 leftPx 保证播放头与帧像素同坐标系。 */
export const GAP_PX = 4;

export interface ClipLayout {
  frameIndex: number;
  startTime: number;
  duration: number;
  leftPx: number;
  widthPx: number;
}

/**
 * 计算每帧的像素布局。
 *
 * 像素模式不做归一化--总宽允许 >容器宽（正是溢出滚动的前提）。
 * gap 计入 leftPx，保证播放头像素与帧像素同坐标系。
 */
export function computeClipLayout(
  frames: { index: number; duration?: number }[],
  options?: { pxPerSecond?: number; minFrameWidthPx?: number; gapPx?: number },
): ClipLayout[] {
  const pxPerSecond = options?.pxPerSecond ?? PX_PER_SECOND;
  const minFrameWidthPx = options?.minFrameWidthPx ?? MIN_FRAME_WIDTH_PX;
  const gapPx = options?.gapPx ?? GAP_PX;

  const total = frames.reduce((s, f) => s + (f.duration ?? 0), 0);
  if (total <= 0) {
    // 无时长信息（如旧数据）：每帧给最小宽度兜底
    return frames.map((f, i) => ({
      frameIndex: f.index,
      startTime: 0,
      duration: 0,
      leftPx: i * (minFrameWidthPx + gapPx),
      widthPx: minFrameWidthPx,
    }));
  }

  let accPx = 0;
  let accTime = 0;
  return frames.map((f) => {
    const widthPx = Math.max((f.duration ?? 0) * pxPerSecond, minFrameWidthPx);
    const layout: ClipLayout = {
      frameIndex: f.index,
      startTime: accTime,
      duration: f.duration ?? 0,
      leftPx: accPx,
      widthPx,
    };
    accTime += f.duration ?? 0;
    accPx += widthPx + gapPx;
    return layout;
  });
}

/** 播放时间（秒）-> 所属帧 index；越界取末帧；空布局返 null。 */
export function timeToFrameIndex(
  layout: ClipLayout[],
  seconds: number,
): number | null {
  if (!layout.length) return null;
  for (const c of layout) {
    if (seconds >= c.startTime && seconds < c.startTime + c.duration) {
      return c.frameIndex;
    }
  }
  return layout[layout.length - 1]!.frameIndex;
}

/**
 * 播放时间（秒）-> 条带上的像素 X 坐标（分段线性映射，与帧渲染位置一致）。
 * gap 已计入 leftPx，播放头与帧边界自然对齐。
 */
export function timeToPx(layout: ClipLayout[], seconds: number): number {
  if (!layout.length) return 0;
  const last = layout[layout.length - 1]!;
  const total = last.startTime + last.duration;
  if (total <= 0) return 0;
  if (seconds >= total) {
    // 越界钉末帧右边缘
    return last.leftPx + last.widthPx;
  }
  for (const c of layout) {
    if (seconds >= c.startTime && seconds < c.startTime + c.duration) {
      const frac = c.duration > 0 ? (seconds - c.startTime) / c.duration : 0;
      return c.leftPx + frac * c.widthPx;
    }
  }
  return 0;
}

/** P2-9: 帧状态徽标--failed（preview_error）> reuse（image_reuse 冻结复用）> null。 */
export function frameBadge(frame: {
  visual_type?: string | null;
  preview_error?: string | null;
}): "reuse" | "failed" | null {
  if (frame.preview_error) return "failed";
  if (frame.visual_type === "image_reuse") return "reuse";
  return null;
}

/**
 * content-match T8③: 来源类型徽标（图/视/卡），渲染于缩略图右下角小角标。
 * 判定口径与后端 frame_ops.frames_to_regenerate 一致：ai_video→视频，
 * graphic→卡，ai_image→图；visual_source 缺省回落任务类型（taskType），
 * remotion（存量）与未知值不标。
 */
export function frameSourceBadge(
  frame: { visual_source?: string | null },
  taskType?: string,
): { kind: "image" | "video" | "card"; label: string } | null {
  const vs = frame.visual_source ?? taskType;
  if (vs === "ai_video") return { kind: "video", label: "视" };
  if (vs === "graphic") return { kind: "card", label: "文" }; // 图内文字镜（native-text）
  if (vs === "ai_image") return { kind: "image", label: "图" };
  return null;
}
