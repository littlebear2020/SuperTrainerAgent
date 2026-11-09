/**
 * 2026-10-20 Task 7：确认渲染弹窗的费用/成片时长格式化（纯函数）。
 *
 * 只给区间、不给伪造精确金额；无 point 时显示「暂无法预估费用」；
 * 空/负值不显示；partial 追加 token 计费说明；始终提示以实际账单为准。
 */

import type { VideoCostEstimate } from "./types";

export type CostEstimateBasis = VideoCostEstimate["basis"];
/** 单一事实源 = types.ts 的 VideoCostEstimate（后端响应形状）。 */
export type CostEstimateData = VideoCostEstimate;

export interface FormattedCostEstimate {
  durationLine: string | null;
  costLine: string;
  spentLine: string | null;
  partialNote: string | null;
  disclaimer: string;
  unavailable: boolean;
}

const CURRENCY_SYMBOLS: Record<string, string> = { CNY: "¥" };
const UNPRICED_LABELS: Record<string, string> = {
  image: "图片",
  video: "视频",
  tts: "配音",
  transition: "转场",
};

function symbol(currency?: string): string {
  const code = (currency ?? "CNY").toUpperCase();
  return CURRENCY_SYMBOLS[code] ?? code;
}

function positive(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? value
    : null;
}

export function formatCostEstimate(
  estimate: CostEstimateData | null | undefined,
): FormattedCostEstimate {
  const disclaimer = "以实际账单为准";
  if (!estimate) {
    return {
      durationLine: null,
      costLine: "暂无法预估",
      spentLine: null,
      partialNote: null,
      disclaimer,
      unavailable: true,
    };
  }

  const unit = symbol(estimate.currency);
  const nShots = positive(estimate.n_shots);
  const videoSeconds = positive(estimate.video_seconds);
  const durationLine =
    nShots != null && videoSeconds != null
      ? `预计成片 ≈ ${Math.round(nShots)} 镜 × ${Math.round(videoSeconds / nShots)} 秒 = ${Math.round(videoSeconds)} 秒`
      : null;

  const point = positive(estimate.point_cny);
  const low = positive(estimate.low_cny);
  const high = positive(estimate.high_cny);
  let costLine: string;
  if (point === null) {
    costLine = "暂无法预估费用";
  } else if (low !== null && high !== null) {
    costLine =
      Math.round(low) === Math.round(high)
        ? `预计费用 ${unit}${Math.round(low)}`
        : `预计费用 ${unit}${Math.round(low)}–${Math.round(high)}`;
  } else {
    costLine = `预计费用 约${unit}${Math.round(point)}`;
  }

  const spent = positive(estimate.spent_cny);
  const spentLine =
    spent !== null ? `草稿已花费 ${unit}${spent.toFixed(2)}` : null;
  const unpricedLabels = (estimate.unpriced ?? []).map(
    (item) => UNPRICED_LABELS[item] ?? item,
  );
  let partialNote: string | null = null;
  if (estimate.basis === "partial") {
    partialNote =
      unpricedLabels.length > 0
        ? `${unpricedLabels.join("/")} 未按精确单价计价，预估为参考值`
        : "部分项目未按精确单价计价，预估为参考值";
  }

  return {
    durationLine,
    costLine,
    spentLine,
    partialNote,
    disclaimer,
    unavailable: point === null,
  };
}

/** 只在确认弹窗打开且是 ViMax 任务时请求预估（关闭弹窗不发请求）。 */
export function shouldRequestCostEstimate(
  confirmOpen: boolean,
  taskType: string | null | undefined,
): boolean {
  return confirmOpen && taskType === "vimax";
}
