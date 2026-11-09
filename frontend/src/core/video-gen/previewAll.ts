export type FrameLite = {
  index: number;
  visual_url?: string | null;
  preview_error?: string | null;
  preview_state?: string | null;
  local_visual_path?: string | null;
};

/**
 * 用户上传/替换的画面（visuals/frame_ 前缀，与后端 frame_has_user_visual 同判据）。
 *
 * 2026-07-29：上传帧的 visual_url 被显式置 None，恰好命中下面三个计数器原本的
 * `!f.visual_url` 条件。后端加守卫后这些帧不再进入批量预览，前端若不同步排除：
 * 计数显示「N 帧待预览」却永远点不动，且 unresolvedPreviewCount 永不归零
 * → 批量永不结案、5 分钟后误报超时。
 *
 * 用前缀而非「有 local_visual_path」——gateway 本地化的 AI 图是 visuals/img_NNNN，
 * 不能被误判成用户素材（否则重新预览对它们永久失效）。
 */
function isUserVisual(f: FrameLite): boolean {
  return (
    !!f.local_visual_path &&
    !f.visual_url &&
    f.local_visual_path.startsWith("visuals/frame_")
  );
}

/**
 * M1-2: 返回尚无 visual_url 的帧 index 列表（供"一键生成全部预览"按钮计数 +
 * 后端 /preview-all 由后端自行筛选 pending，前端只用于 UI 计数）。
 * 有 preview_error 的帧仍计入（点击批量=重试）；用户上传帧排除（见 isUserVisual）。
 */
export function pendingPreviewFrames(frames: FrameLite[]): number[] {
  return frames
    .filter((f) => !f.visual_url && !isUserVisual(f))
    .map((f) => f.index);
}

/**
 * 上次预览失败（有 preview_error 且无图）的帧数——批量按钮附文「失败 N 帧，点击重试」。
 * 有 visual_url 的帧即使曾有 error 也不算失败（图已在，无需重试）。
 * 上传帧同样不算：上传不清 preview_error（后端 upload_frame_visual 只写
 * local_visual_path/visual_type/visual_url），若仍计入，按钮会说「失败 1 帧，点击重试」
 * 而该帧已被批量排除 → 点了没反应的死胡同。
 */
export function failedPreviewCount(frames: FrameLite[]): number {
  return frames.filter(
    (f) => !f.visual_url && f.preview_error && !isUserVisual(f),
  ).length;
}

/**
 * 单帧预览已结案（终态）→ tracker 可 release 停转圈。
 * 2026-09-07 preview_state 状态机：只认终态 done/error；移除「visual_url 存在」腿——
 * 重生已有预览图的帧时旧 visual_url 在生成期间后端不清（UX 定案：失败仍显旧图），
 * 用它判结案会在点击瞬间误结案（spinner 秒停+假成功 toast+停轮询，新图永远拉不回）。
 * preview_error 腿保留作存量/批量兜底（批量路径不写 preview_state）。
 */
export function isPreviewResolved(f: FrameLite | undefined): boolean {
  return (
    !!f &&
    (f.preview_state === "done" ||
      f.preview_state === "error" ||
      !!f.preview_error)
  );
}

/**
 * 帧画面指纹（visual_url + local_visual_path 串接）——条目 34 旧后端探测基线。
 */
export function frameVisualFingerprint(f: FrameLite | undefined): string {
  return `${f?.visual_url ?? ""}|${f?.local_visual_path ?? ""}`;
}

/**
 * 旧后端（无 preview_state 状态机，2026-09-07 c69e5721a 之前）探测
 * （条目 34，2026-09-12 缺陷批）：新前端 + 旧后端混部时 isPreviewResolved 恒假
 * → spinner 转满 5min（PREVIEW_TIMEOUT_MS）后误报「预览超时」。
 *
 * 行为式探测（静态判「键缺失=旧后端」不可行——新后端上从未预览过的帧同样无
 * preview_state 键；可靠信号是行为式的）：跟踪开始时记画面指纹（baseline），
 * 轮询中**指纹变化**且 preview_state 持续缺席（新后端 POST 202 后立即写
 * running，routers/video_gen.py:1988）→ 旧后端已把新图写回 → 按画面已更新结案。
 * 同版部署（新后端）永远走 isPreviewResolved 正常结案，本分支零触发。
 */
export function isLegacyBackendPreviewUpdate(
  baseline: string | undefined,
  f: FrameLite | undefined,
): boolean {
  if (!f) return false;
  if (f.preview_state) return false; // 新后端：走正常状态机结案
  return baseline !== undefined && frameVisualFingerprint(f) !== baseline;
}

/**
 * 真正「未结案」的帧数（无 visual_url 且无 preview_error）——批量预览结案判定。
 * 与 isPreviewResolved 同语义（成功有图 OR 失败有 error 均算结案）。
 * 批量结案若用 pendingPreviewCount===0（!visual_url），失败帧会被永远计入
 * pending → 批量永不结案、5 分钟后误报超时（2026-07-28 评审修复）。
 */
export function unresolvedPreviewCount(frames: FrameLite[]): number {
  return frames.filter(
    (f) => !f.visual_url && !f.preview_error && !isUserVisual(f),
  ).length;
}

/** 后台预览超时兜底（进程重启等 preview_error 永远写不上的极端情况）。 */
export const PREVIEW_TIMEOUT_MS = 300_000;

export function isPreviewTimedOut(
  startedAt: number,
  now: number,
  timeoutMs: number = PREVIEW_TIMEOUT_MS,
): boolean {
  return now - startedAt > timeoutMs;
}

/**
 * 重试预览时乐观清除缓存中该帧的旧 preview_error（不可变更新）。
 *
 * 不修这个的话：后端只在生成成功后才 pop 旧 preview_error，重试进行中途旧值仍挂在帧上，
 * release effect 用缓存旧数据判 isPreviewResolved → 立即 true → spinner 秒停并弹出上一次
 * 陈旧错误 toast。track 时同步清掉后，isPreviewResolved 变 false，新结果随轮询到达再结案。
 */
export function clearFramePreviewError<T extends { frames?: FrameLite[] }>(
  data: T | undefined,
  index: number,
): T | undefined {
  if (!data?.frames) return data;
  return {
    ...data,
    frames: data.frames.map((f) =>
      f.index === index ? { ...f, preview_error: null } : f,
    ),
  };
}

/**
 * 重新生成预览时乐观清除缓存中该帧的旧 preview_state（不可变更新，2026-09-07）。
 *
 * 与 clearFramePreviewError 成对（track 双清）：上次尝试留下的 done/error 不清的话，
 * release effect 下一渲染周期即以缓存旧 state 判 isPreviewResolved → 立即 true →
 * 误结案（与旧 preview_error 同一类缺陷）。清掉后由后端 running→终态驱动正常结案。
 */
export function clearFramePreviewState<T extends { frames?: FrameLite[] }>(
  data: T | undefined,
  index: number,
): T | undefined {
  if (!data?.frames) return data;
  return {
    ...data,
    frames: data.frames.map((f) =>
      f.index === index ? { ...f, preview_state: null } : f,
    ),
  };
}
