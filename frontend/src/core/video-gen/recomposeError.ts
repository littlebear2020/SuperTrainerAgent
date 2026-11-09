/**
 * 从 recompose/re-render 409 错误文案解析 1-based 帧号 -> 0-based index 数组。
 *
 * Task 2 统一文案后只有顿号格式（``帧 1、2、5``）。
 * 旧 ``帧 [2, 5]`` Python list repr 格式也做防御性兼容。
 * 解析失败返回空数组（保底行为 = 现状 toast 原样展示）。
 */
export function parseUnavailableFrames(errMsg: string): number[] {
  if (!errMsg) return [];

  const results: number[] = [];

  // 主格式：帧 1、2、5（中文顿号连接，Task 2 统一后只有这种）
  // 匹配 "帧" 后跟空格 + 数字 + （顿号/空格/等） + 数字...
  const primary = /帧\s*([\d、\s]+等?)/.exec(errMsg);
  if (primary) {
    const nums = primary[1]!.match(/\d+/g);
    if (nums) {
      for (const n of nums) {
        const idx = parseInt(n, 10) - 1; // 1-based -> 0-based
        if (idx >= 0 && !results.includes(idx)) results.push(idx);
      }
    }
    if (results.length) return results;
  }

  // 防御：旧格式 帧 [2, 5]（Python list repr）
  const legacy = /帧\s*\[([\d\s,]+)\]/.exec(errMsg);
  if (legacy) {
    const nums = legacy[1]!.match(/\d+/g);
    if (nums) {
      for (const n of nums) {
        const idx = parseInt(n, 10) - 1;
        if (idx >= 0 && !results.includes(idx)) results.push(idx);
      }
    }
    return results;
  }

  return [];
}
