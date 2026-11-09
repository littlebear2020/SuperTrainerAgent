/**
 * 本地帧缩略图版本号（2026-07-27 预览缓存不刷新回归修复）。
 *
 * 预览本地化（18cfffcd8）后同名文件被覆盖（visuals/img_NNNN.png）且
 * local_visual_path 保持——`?v=` 若只挂 state 版本号，预览完成后 src 不变、
 * <img> 不重取，用户看到旧图。frame.visual_url 每次重生成必变（新 TOS URL），
 * 用它派生版本号：url 变 → 版本变 → 浏览器重取；url 不变 → 版本稳定 → 缓存有效。
 * 上传路径（不产生新 URL）用 uploadTick 兜底（调用方在上传成功时 +1）。
 *
 * 实现取 URL 尾段而非 hash——TOS 签名参数在 URL 尾部且每次重生成必变；
 * 32 位 hash 理论碰撞即版本不变=回归重现，尾段截取零碰撞成本（评审 P1-4）。
 */
export function visualUrlVersion(
  url: string | null | undefined,
  uploadTick: number,
): string {
  if (!url) {
    return `tick-${uploadTick}`;
  }
  // 尾段去非字母数字字符（&/= 等会污染 query 解析）
  const tail = url.slice(-32).replace(/[^a-zA-Z0-9]/g, "");
  return `${tail}-${uploadTick}`;
}
