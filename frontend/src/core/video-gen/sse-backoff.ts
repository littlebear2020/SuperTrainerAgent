/**
 * SSE 重连退避（useTaskEvents）。
 * 原生 EventSource 固定 ~3s 重连无退避 —— 服务器/网络故障期每分钟 ~20 次新 TCP
 * 连接（vg_698c123a8ddd 渲染窗口 F12 刷屏 20+ 条 ERR_CONNECTION_TIMED_OUT）。
 * 指数退避 + 上限封顶 + 最大次数放弃（任务状态轮询仍在，最终态不丢）。
 */
export const MAX_SSE_RETRIES = 8;

export function nextBackoffMs(attempt: number): number {
  return Math.min(1000 * 2 ** attempt, 30_000);
}
