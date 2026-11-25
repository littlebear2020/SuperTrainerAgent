/**
 * PR3（2026-11-24 前端可靠性批）纵切 A：run 历史按 run 分片、同 run 幂等重取、
 * 切线程不串台。夹具见 history-test-harness（必须是第一个 import）。
 */
import {
  deferred,
  flush,
  harness,
  historyApi,
  rejectsWithHistory,
  settleHistory,
  type RunPage,
  messageIds,
  mountHistory,
  renderHistory,
  resetHistoryHarness,
  run,
  runPage,
  unmountHistory,
} from "./history-test-harness";
// 夹具必须先于 vitest 求值（vi.mock 的提升规则），顺序与 import/order 相反
// eslint-disable-next-line import/order
import { afterEach, beforeEach, describe, expect, test } from "vitest";

const latest = runPage("run-2", ["m3", "m4"]);
const latestStale = runPage("run-2", ["m3"]);
const older = runPage("run-1", ["m1"]);

describe("useThreadHistory：newest-first 的 runs 按 run 分片加载", () => {
  beforeEach(() => {
    resetHistoryHarness();
    // 真实 API：newest-first
    harness().runsByThread.set("t-1", [run("run-2"), run("run-1")]);
    harness().responder = (runId) => {
      if (runId === "run-2") return [structuredClone(latest)];
      if (runId === "run-1") return [structuredClone(older)];
      throw new Error(`unknown run ${runId}`);
    };
  });

  afterEach(async () => {
    await unmountHistory();
  });

  test("首次只加载最新的 run，loadMore 向前一个 run，flatten 旧→新；runs.list 显式 limit=100", async () => {
    const api = await mountHistory("t-1");
    expect(harness().listCalls[0]).toEqual(["t-1", { limit: 100 }]);
    expect(messageIds(historyApi())).toEqual(["m3", "m4"]);
    expect(historyApi().hasMore).toBe(true);
    expect([...historyApi().loadedRunIds]).toEqual(["run-2"]);
    expect(historyApi().messageRunIds.get("m3")).toBe("run-2");

    await settleHistory(() => api.loadMore());
    expect(messageIds(historyApi())).toEqual(["m1", "m3", "m4"]);
    expect(historyApi().hasMore).toBe(false);
    expect([...historyApi().loadedRunIds]).toEqual(["run-2", "run-1"]);
    // 只请求了这两个 run，各一次
    expect(harness().messageCalls.map((c) => c.runId)).toEqual([
      "run-2",
      "run-1",
    ]);
  });

  test("onFinish 重取同一 run：新分片原子替换，补齐新增消息", async () => {
    const api = await mountHistory("t-1");
    expect(messageIds(historyApi())).toEqual(["m3", "m4"]);

    // 流式期间 run-2 追加了 m5：重取后整片替换
    const grown = runPage("run-2", ["m3", "m4", "m5"]);
    harness().responder = (runId) => [
      structuredClone(runId === "run-2" ? grown : older),
    ];
    await settleHistory(() => api.refetchRun("run-2"));
    expect(messageIds(historyApi())).toEqual(["m3", "m4", "m5"]);
  });

  test("同 run A/B 请求按 B→A 完成时仍保留 B 的最终分片", async () => {
    const api = await mountHistory("t-1");
    const slowA = deferred<RunPage[]>();
    const fresh = runPage("run-2", ["m3", "m4", "m5"]);
    let run2Requests = 0;
    harness().responder = (runId) => {
      if (runId !== "run-2") return [structuredClone(older)];
      run2Requests += 1;
      // A（先发）挂起；B（后发）立即返回新分片
      return run2Requests === 1
        ? slowA.promise
        : Promise.resolve([structuredClone(fresh)]);
    };

    const a = api.refetchRun("run-2").catch(() => undefined);
    await flush();
    expect(messageIds(historyApi())).toEqual(["m3", "m4"]);
    await settleHistory(() => api.refetchRun("run-2"));
    expect(messageIds(historyApi())).toEqual(["m3", "m4", "m5"]);

    slowA.resolve([structuredClone(latestStale)]);
    await a;
    await flush();
    // A 的旧结果不覆盖 B
    expect(messageIds(historyApi())).toEqual(["m3", "m4", "m5"]);
  });

  test("旧 run 失败后可重试，loadedRunIds 不提前写入", async () => {
    const api = await mountHistory("t-1");
    let fail = true;
    const responder = harness().responder!;
    harness().responder = (runId, query) => {
      if (runId === "run-1" && fail) throw new Error("boom 500");
      return responder(runId, query);
    };

    const error = await rejectsWithHistory(() => api.loadMore());
    expect(error).toMatchObject({ message: "boom 500" });
    expect(messageIds(historyApi())).toEqual(["m3", "m4"]);
    expect(historyApi().hasMore).toBe(true);
    expect([...historyApi().loadedRunIds]).not.toContain("run-1");

    fail = false;
    await settleHistory(() => api.loadMore());
    expect(messageIds(historyApi())).toEqual(["m1", "m3", "m4"]);
    expect(historyApi().hasMore).toBe(false);
  });

  test("重取时端点返回 500：按失败处理，已加载的分片不被清空、可重试", async () => {
    await mountHistory("t-1");
    expect(messageIds(historyApi())).toEqual(["m3", "m4"]);
    const goodResponder = harness().responder!;
    let fail = true;
    harness().responder = (runId, query) => {
      if (runId === "run-2" && fail) return { status: 500, detail: "boom" };
      return goodResponder(runId, query);
    };

    const error = await rejectsWithHistory(() =>
      historyApi().refetchRun("run-2"),
    );
    expect(error).toMatchObject({ message: /HTTP 500/ });
    await flush();
    // 不是「成功的空结果」：旧分片还在
    expect(messageIds(historyApi())).toEqual(["m3", "m4"]);

    fail = false;
    await settleHistory(() => historyApi().refetchRun("run-2"));
    expect(messageIds(historyApi())).toEqual(["m3", "m4"]);
  });

  test("A 慢历史未返回时切到 B：A 的响应不拼入 B", async () => {
    const api = await mountHistory("t-1");
    harness().runsByThread.set("t-2", [run("run-b")]);
    const slow = deferred<RunPage[]>();
    harness().responder = (runId) => {
      if (runId === "run-1") return slow.promise;
      if (runId === "run-b") return Promise.resolve([runPage("run-b", ["mb"])]);
      return Promise.resolve([structuredClone(latest)]);
    };

    const loading = api.loadMore().catch(() => undefined);
    await flush();
    await renderHistory("t-2");
    expect(messageIds(historyApi())).toEqual(["mb"]);

    slow.resolve([structuredClone(older)]);
    await loading.catch(() => undefined);
    await flush();
    expect(messageIds(historyApi())).toEqual(["mb"]);
    expect(historyApi().loadedRunIds.has("run-1")).toBe(false);
  });
});
