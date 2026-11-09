import type { BGMTrack } from "./types";

/** BGM 选择器搜索过滤（名称 + tags，大小写不敏感）。纯函数以便 node 环境单测。 */
export function filterBgmTracks(tracks: BGMTrack[], query: string): BGMTrack[] {
  const q = query.trim().toLowerCase();
  if (!q) return tracks;
  return tracks.filter(
    (t) =>
      t.name.toLowerCase().includes(q) ||
      (t.tags ?? []).some((tag) => tag.toLowerCase().includes(q)),
  );
}

/** 80 → "1分20秒"；41 → "41秒"；空值 → ""。 */
export function formatBgmDuration(seconds: number | null | undefined): string {
  if (!seconds || seconds <= 0) return "";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return m > 0 ? `${m}分${s.toString().padStart(2, "0")}秒` : `${s}秒`;
}

/**
 * 任务详情里的 BGM 展示名（2026-07-31）：payload 存的是裸 id，直接显示对用户无意义。
 *
 * 2026-07-31 删掉 3 首旧曲后，老任务 payload 仍指向 ambient_calm 等已下架 id，
 * 后端 `_resolve_bgm_or_warn` 会静默降级成「不带 BGM」并只写服务端日志 ——
 * 用户视角是「我的 BGM 莫名消失」。此处把这种情况显式标出来。
 *
 * `tracks` 为空（曲库还在加载）时不判定缺失，避免误报。
 */
export function resolveBgmLabel(
  bgmId: string | null | undefined,
  tracks: BGMTrack[],
): { label: string; missing: boolean } {
  if (!bgmId) return { label: "", missing: false };
  const hit = tracks.find((t) => t.id === bgmId);
  if (hit) return { label: hit.name, missing: false };
  if (tracks.length === 0) return { label: bgmId, missing: false };
  return { label: `${bgmId}（曲目已下架，本片无背景音乐）`, missing: true };
}
