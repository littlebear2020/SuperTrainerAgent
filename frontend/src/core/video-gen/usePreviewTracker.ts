"use client";

import { useCallback, useState } from "react";

// 纯函数（可单测，无需 testing-library —— 项目刻意未装 @testing-library/react）
export function trackFrame(pending: Set<number>, i: number): Set<number> {
  const n = new Set(pending);
  n.add(i);
  return n;
}

export function releaseFrame(pending: Set<number>, i: number): Set<number> {
  const n = new Set(pending);
  n.delete(i);
  return n;
}

/**
 * M1-3: per-frame preview pending tracker。
 *
 * 一个共享的 usePreviewFrame (React Query v5) 实例只跟踪最新一次 mutate() ——
 * 连点 #1/#2/#3 时，点 #2 会让 #1 的 isPending/variables 假，spinner 消失
 * （HTTP 请求未取消，图终会出，但视觉上像"停了/串行"）。改用本 tracker 的 Set
 * 驱动每帧 spinner：点 #2 track(2) 不影响 #1 的 pending.has(1)，各帧独立转圈到
 * 各自 onSettled release。
 */
export function usePreviewTracker() {
  const [pending, setPending] = useState<Set<number>>(new Set());
  const track = useCallback(
    (i: number) => setPending((s) => trackFrame(s, i)),
    [],
  );
  const release = useCallback(
    (i: number) => setPending((s) => releaseFrame(s, i)),
    [],
  );
  return { pending, track, release };
}
