import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  createSubmission,
  SendRejectedError,
  type SubmissionOutcome,
} from "@/core/threads/submission";

describe("submission 双生命周期（纯逻辑）", () => {
  let unhandled: unknown[];
  const onUnhandled = (reason: unknown) => {
    unhandled.push(reason);
  };

  beforeEach(() => {
    unhandled = [];
    process.on("unhandledRejection", onUnhandled);
  });

  afterEach(() => {
    process.off("unhandledRejection", onUnhandled);
  });

  async function flush() {
    for (let i = 0; i < 10; i++) {
      await Promise.resolve();
    }
    await new Promise((resolve) => setTimeout(resolve, 0));
  }

  test("accepted 只在 accept() 后 settle 一次，迟到的 accept / reject 不生效", async () => {
    const submission = createSubmission(1, "thread-1", false);
    const acceptedSpy = vi.fn();
    const rejectedSpy = vi.fn();
    void submission.accepted.then(acceptedSpy, rejectedSpy);

    await flush();
    expect(acceptedSpy).not.toHaveBeenCalled();
    expect(submission.state).toBe("pending");

    submission.accept();
    submission.accept();
    submission.reject("failed", "late");
    await flush();

    expect(acceptedSpy).toHaveBeenCalledTimes(1);
    expect(rejectedSpy).not.toHaveBeenCalled();
    expect(submission.state).toBe("accepted");
    await expect(submission.accepted).resolves.toBeUndefined();
  });

  test("reject 后 accepted 以 SendRejectedError(kind) reject，之后 accept 不改写", async () => {
    const submission = createSubmission(2, "thread-2", true);
    const cause = new Error("upstream");
    submission.reject("busy", "上一次发送仍在途");
    submission.reject("failed", "late");
    submission.accept();

    expect(submission.state).toBe("rejected");
    await expect(submission.accepted).rejects.toMatchObject({
      name: "SendRejectedError",
      kind: "busy",
      message: "上一次发送仍在途",
    });

    const other = createSubmission(3, "thread-3", false);
    other.reject("failed", "创建失败", cause);
    await expect(other.accepted).rejects.toBeInstanceOf(SendRejectedError);
    await expect(other.accepted).rejects.toMatchObject({ cause });
  });

  test("completion 只 settle 一次，迟到的 complete 不生效", async () => {
    const submission = createSubmission(4, "thread-4", false);
    submission.accept();
    const seen: SubmissionOutcome[] = [];
    void submission.completion.then((outcome) => seen.push(outcome));

    submission.complete({ status: "stopped" });
    submission.complete({ status: "finished" });
    submission.complete({ status: "failed", error: new Error("late") });
    await flush();

    expect(seen).toEqual([{ status: "stopped" }]);
    expect(submission.state).toBe("finished");
    await expect(submission.completion).resolves.toEqual({ status: "stopped" });
  });

  test("受理前 reject 即同时收尾 completion，completion 永不 reject", async () => {
    const stopped = createSubmission(5, "thread-5", false);
    const failed = createSubmission(6, "thread-6", false);
    const error = new Error("创建前失败");
    void stopped.accepted.catch(() => undefined);
    void failed.accepted.catch(() => undefined);

    stopped.reject("stopped", "已停止");
    failed.reject("failed", "创建前失败", error);

    await expect(stopped.completion).resolves.toEqual({ status: "stopped" });
    await expect(failed.completion).resolves.toMatchObject({
      status: "failed",
      error,
    });
    await flush();
    expect(unhandled).toEqual([]);
  });

  test("completion 无人观察时也不产生 unhandled rejection", async () => {
    const submission = createSubmission(7, "thread-7", false);
    void submission.accepted.catch(() => undefined);
    submission.reject("stopped", "已停止");
    submission.complete({ status: "finished" });
    await flush();
    expect(unhandled).toEqual([]);
  });

  test("rejectionKind 从任意错误值读出 kind", () => {
    const err = new SendRejectedError("failed", "x");
    expect(SendRejectedError.rejectionKind(err)).toBe("failed");
    expect(SendRejectedError.rejectionKind(new Error("plain"))).toBeNull();
    expect(SendRejectedError.rejectionKind("busy")).toBeNull();
    expect(SendRejectedError.rejectionKind(null)).toBeNull();
  });

  test("id / threadId / wasNewThread 如实记录", () => {
    const a = createSubmission(1, "t-a", true);
    const b = createSubmission(2, "t-b", false);
    expect([a.id, a.requestedThreadId, a.wasNewThread]).toEqual([
      1,
      "t-a",
      true,
    ]);
    expect([b.id, b.requestedThreadId, b.wasNewThread]).toEqual([
      2,
      "t-b",
      false,
    ]);
  });
});
