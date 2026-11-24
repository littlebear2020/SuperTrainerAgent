/**
 * 提交受理 / 完成双生命周期（PR2，2026-11-24 前端可靠性批）。纯逻辑，node 档可测。
 *
 * 一次 `sendMessage` 产生一个 submission：
 * - `accepted`：上传完成且 SDK `onCreated` 后 resolve；busy、创建前失败、受理前停止
 *   则 reject（`SendRejectedError.kind`）。它就是 `sendMessage()` 的返回值——输入框
 *   只在它 resolve 后清空。
 * - `completion`：只在 hook 内部持有，由 SDK onFinish/onError/onStop 与取消结果驱动；
 *   永不 reject，所以无人观察也不会产生 unhandled rejection。
 *
 * 两个 Promise 都只 settle 一次，迟到的 accept / reject / complete 不生效。
 */

export type SendRejectionKind = "busy" | "stopped" | "failed";

const REJECTION_KINDS: ReadonlySet<string> = new Set<SendRejectionKind>([
  "busy",
  "stopped",
  "failed",
]);

export class SendRejectedError extends Error {
  readonly kind: SendRejectionKind;

  constructor(kind: SendRejectionKind, message: string, cause?: unknown) {
    super(message);
    this.name = "SendRejectedError";
    this.kind = kind;
    if (cause !== undefined) {
      (this as { cause?: unknown }).cause = cause;
    }
  }

  /** 从任意错误值读出 rejection kind；不是 SendRejectedError 时返回 null。 */
  static rejectionKind(value: unknown): SendRejectionKind | null {
    if (typeof value !== "object" || value === null) return null;
    if (!(value instanceof Error) || value.name !== "SendRejectedError") {
      return null;
    }
    const kind = Reflect.get(value, "kind");
    return typeof kind === "string" && REJECTION_KINDS.has(kind)
      ? (kind as SendRejectionKind)
      : null;
  }
}

export type SubmissionOutcome =
  | { status: "finished" }
  | { status: "failed"; error: unknown }
  | { status: "stopped" }
  /** 取消请求本身失败：不伪造 interrupted，留待恢复入口重试 */
  | { status: "cancelFailed"; error: unknown };

export type SubmissionState = "pending" | "accepted" | "rejected" | "finished";

export interface Submission {
  readonly id: number;
  /** 调用方请求的线程 id（新线程时是页面预生成的 UUID） */
  readonly requestedThreadId: string;
  /** hook 的 threadId 为 undefined 时发起——onSendError 的 URL 回退依据 */
  readonly wasNewThread: boolean;
  /** SDK onCreated 回传，受理后才有 */
  runId: string | undefined;
  threadId: string | undefined;
  /** onSend 已触发（上传完成、提交 run 前） */
  onSendFired: boolean;
  /** thread.submit 已入队：SDK 流收尾却从未 onCreated / onError 时的兜底结案依据 */
  submitted: boolean;
  readonly state: SubmissionState;
  readonly accepted: Promise<void>;
  readonly completion: Promise<SubmissionOutcome>;
  accept(): void;
  reject(kind: SendRejectionKind, message: string, cause?: unknown): void;
  complete(outcome: SubmissionOutcome): void;
}

export function createSubmission(
  id: number,
  requestedThreadId: string,
  wasNewThread: boolean,
): Submission {
  let state: SubmissionState = "pending";
  let acceptedSettled = false;
  let completionSettled = false;
  let resolveAccepted: () => void = () => undefined;
  let rejectAccepted: (error: unknown) => void = () => undefined;
  let resolveCompletion: (outcome: SubmissionOutcome) => void = () => undefined;

  const accepted = new Promise<void>((resolve, reject) => {
    resolveAccepted = resolve;
    rejectAccepted = reject;
  });
  const completion = new Promise<SubmissionOutcome>((resolve) => {
    resolveCompletion = resolve;
  });

  function settleCompletion(outcome: SubmissionOutcome) {
    if (completionSettled) return;
    completionSettled = true;
    resolveCompletion(outcome);
  }

  const submission: Submission = {
    id,
    requestedThreadId,
    wasNewThread,
    runId: undefined,
    threadId: undefined,
    onSendFired: false,
    submitted: false,
    get state() {
      return state;
    },
    accepted,
    completion,
    accept() {
      if (acceptedSettled) return;
      acceptedSettled = true;
      state = "accepted";
      resolveAccepted();
    },
    reject(kind, message, cause) {
      if (acceptedSettled) return;
      acceptedSettled = true;
      state = "rejected";
      rejectAccepted(new SendRejectedError(kind, message, cause));
      // 受理前失败 / 停止即同时收尾 completion，等待它的逻辑不会悬空
      settleCompletion(
        kind === "stopped"
          ? { status: "stopped" }
          : { status: "failed", error: cause ?? message },
      );
    },
    complete(outcome) {
      // 只对「已受理」的 submission 收尾：未受理或已被拒绝的 submission 不因迟到的
      // complete 变成 finished，也不会被隐式 accept（accepted 只由 accept / reject 决定）
      if (state !== "accepted") return;
      state = "finished";
      settleCompletion(outcome);
    },
  };
  return submission;
}
