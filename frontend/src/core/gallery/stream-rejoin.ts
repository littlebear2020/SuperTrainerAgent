/**
 * 灵感库 instantiate 的 run 由服务端创建，浏览器从未调用 thread.submit() →
 * useStream(reconnectOnMount) 在 sessionStorage 找不到 "lg:stream:{threadId}"，
 * 聊天页不 joinStream → 用户看不到任何进度，重发撞 409（2026-07-31「假死」修复）。
 *
 * 跳转聊天页前写入该键，聊天页 mount 时 SDK 自动 joinStream 接续。
 * 键/值格式是 @langchain/langgraph-sdk 内部约定（纯 run_id 字符串），不得更改。
 */
export const STREAM_REJOIN_KEY_PREFIX = "lg:stream:";

export function seedStreamRejoinKey(
  threadId: string,
  runId: string | undefined,
): void {
  if (!runId || typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(
      `${STREAM_REJOIN_KEY_PREFIX}${threadId}`,
      runId,
    );
  } catch {
    // sessionStorage 不可用（隐私模式等）——退化为旧行为：任务照跑但进度不可见
  }
}
