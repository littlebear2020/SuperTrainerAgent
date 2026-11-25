/**
 * PR3（2026-11-24 前端可靠性批）：长会话恢复的纯逻辑——流错误分类、断线判定、
 * 取消等待的上限。hook（useThreadStream）只做接线。
 */

/**
 * 显式取消（wait=true）要等服务端把 run 置为终态才返回；run 卡在不响应 interrupt
 * 的工具里、或网络半开时，没有上限的等待会让 busy 锁永远不放。到期按失败结案
 * （保留可重试的错误），底层请求本身不强拆——服务端对重复取消幂等。
 */
export const CANCEL_WAIT_LIMIT_MS = 30_000;

export type StreamErrorKind =
  /** 用户停止 / SDK 自己的超时：不是错误。 */
  | "aborted"
  /** run 在服务端已不存在（重启 / 多 worker / 清理）：重连没有意义。 */
  | "stale_run"
  /** 连接类：网络断开、网关 5xx——run 可能还在服务端跑，值得重连。 */
  | "connection"
  /** 业务 4xx 等：重连只会再失败一次。 */
  | "business";

// 只认网络栈 / 网关的固定形态；unavailable、terminated 这类通用词不算（后端业务
// 错误「Tool unavailable」「Run terminated by policy」不该弹出重连入口）
const CONNECTION_ERROR_PATTERN =
  /failed to fetch|fetch failed|load failed|networkerror|network error|econnreset|econnrefused|socket hang up|etimedout|err_internet_disconnected|err_network_changed|err_connection_|err_name_not_resolved|service unavailable|bad gateway|gateway timeout/i;

function httpStatus(error: unknown): number | undefined {
  if (typeof error !== "object" || error === null) return undefined;
  const status = Reflect.get(error, "status");
  return typeof status === "number" ? status : undefined;
}

function messageOf(error: unknown): string {
  if (typeof error === "string") return error;
  if (error instanceof Error) return error.message;
  if (typeof error === "object" && error !== null) {
    const message = Reflect.get(error, "message");
    if (typeof message === "string") return message;
  }
  return "";
}

/**
 * 流错误分类。SDK 的错误对象形态不一：fetch 的网络失败是 TypeError、HTTP 失败带
 * `status` 或只在 message 里写 `HTTP 404: ...`、后端 detail 嵌在 `error` 里。
 */
export function classifyStreamError(
  error: unknown,
  context: { hasRun?: boolean } = {},
): StreamErrorKind {
  if (
    typeof error === "object" &&
    error !== null &&
    (Reflect.get(error, "name") === "AbortError" ||
      Reflect.get(error, "name") === "TimeoutError")
  ) {
    return "aborted";
  }
  const status = httpStatus(error);
  const message = messageOf(error);
  // 只认「run 不存在」：别的资源（Thread / Agent / Tool）的 "not found" 是业务错误，
  // 不能静默吞掉、也不能顺手清掉 rejoin key。「Run」与「not found」之间不跨冒号：
  // `Run failed: Tool 'x' not found` 是 run 里面的工具找不到，失败原因要照常提示。
  // 裸 404（HTTP 404: Not Found）只有在这个流确实属于某个 run 时才算 stale run——
  // 没有 run 的 404 是线程 / 页面不存在
  if (/\brun\b[^:]*\bnot found\b/i.test(message)) {
    return "stale_run";
  }
  if (context.hasRun && (status === 404 || /HTTP\s*404\b/i.test(message))) {
    return "stale_run";
  }
  if (status !== undefined && status >= 500 && status < 600) {
    return "connection";
  }
  if (/HTTP\s*5\d\d\b/i.test(message)) return "connection";
  if (CONNECTION_ERROR_PATTERN.test(message)) return "connection";
  return "business";
}

/** 后端 run 终态：pending / running 之外都算结束（没有独立的 cancelled）。 */
export function isTerminalRunStatus(status: string | undefined): boolean {
  return (
    status === "success" ||
    status === "error" ||
    status === "timeout" ||
    status === "interrupted"
  );
}

/**
 * 「流断了、但 run 可能还在服务端跑」——只有这种情况才给重连入口。
 * run 的状态未知（刚创建、runs 列表里还没有）时按可能仍在运行处理：重连遇到 404
 * 走 stale run 路径，代价只是一次失败的 join。
 */
export function isRecoverableDisconnect({
  error,
  isLoading,
  hasRun,
  runStatus,
}: {
  error: unknown;
  isLoading: boolean;
  /** 当前流所属的 run 是否已知（没有 runId 无从重连）。 */
  hasRun: boolean;
  runStatus: string | undefined;
}): boolean {
  if (!hasRun || isLoading || error == null) return false;
  if (classifyStreamError(error) !== "connection") return false;
  return runStatus === undefined || !isTerminalRunStatus(runStatus);
}

/** 给 Promise 加等待上限：到期以 `makeError()` 拒绝，原 Promise 的结果不再被采用。 */
export function withTimeout<T>(
  promise: Promise<T>,
  ms: number,
  makeError: () => Error,
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(makeError()), ms);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error: unknown) => {
        clearTimeout(timer);
        reject(error instanceof Error ? error : new Error(String(error)));
      },
    );
  });
}
