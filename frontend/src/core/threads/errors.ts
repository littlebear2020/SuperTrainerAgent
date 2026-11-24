/**
 * PR2（2026-11-24 前端可靠性批）：sendMessage 以 busy 拒绝时的文案。
 * busy 不弹提示（输入保留、用户等上一条收尾），与后端并发锁的提示共用同一句。
 */
export const SEND_BUSY_MESSAGE = "当前对话正在生成中，请等待完成后再发送";

/** PR2：受理前停止时 accepted 的拒绝文案（同样不弹提示）。 */
export const SEND_STOPPED_MESSAGE = "已停止发送";

/** 发送消息失败的用户友好文案映射；返回 null 表示走既有默认提示。 */
export function friendlySendErrorMessage(err: unknown): string | null {
  const status = (err as { status?: number } | null | undefined)?.status;
  let message = "";
  if (err instanceof Error) {
    message = err.message;
  } else if (typeof err === "string") {
    message = err;
  } else if (typeof err === "object" && err !== null) {
    const raw = Reflect.get(err, "message");
    if (typeof raw === "string") {
      message = raw;
    }
  }
  // 后端并发锁（RunManager reject 策略）：thread 已有 active run。
  // 灵感库重连键修复后常规路径已不可达，此处兜底真·并发场景。
  // SDK 错误对象形态不一：或带 status=409，或仅 message 含后端 detail 文案。
  if (status === 409 || message.includes("already has an active run")) {
    return SEND_BUSY_MESSAGE;
  }
  return null;
}
