/**
 * PR3（2026-11-24 前端可靠性批）纵切 A：切线程时 run 历史整体重置——messages /
 * hasMore / loading / loadedRunIds 都不跨线程残留；同一线程重渲染不重复请求。
 * 夹具见 history-test-harness（必须是第一个 import）。
 */
import {
  flush,
  harness,
  historyApi,
  settleHistory,
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

describe("useThreadHistory：切线程重置", () => {
  beforeEach(() => {
    resetHistoryHarness();
    harness().runsByThread.set("t-1", [run("run-2"), run("run-1")]);
    harness().runsByThread.set("t-2", [run("run-b"), run("run-a")]);
    harness().responder = (runId) => {
      if (runId === "run-2") return [runPage("run-2", ["m3", "m4"])];
      if (runId === "run-1") return [runPage("run-1", ["m1"])];
      if (runId === "run-b") return [runPage("run-b", ["mb"])];
      if (runId === "run-a") return [runPage("run-a", ["ma"])];
      throw new Error(`unknown run ${runId}`);
    };
  });

  afterEach(async () => {
    await unmountHistory();
  });

  test("切线程后 messages / hasMore / loadedRunIds 全部重置，B 只加载自己的最新 run", async () => {
    const api = await mountHistory("t-1");
    await settleHistory(() => api.loadMore());
    expect(messageIds(historyApi())).toEqual(["m1", "m3", "m4"]);
    expect(historyApi().hasMore).toBe(false);

    await renderHistory("t-2");
    expect(messageIds(historyApi())).toEqual(["mb"]);
    expect(historyApi().hasMore).toBe(true);
    expect([...historyApi().loadedRunIds]).toEqual(["run-b"]);
    expect(historyApi().messageRunIds.get("mb")).toBe("run-b");
    expect(historyApi().messageRunIds.has("m3")).toBe(false);

    await settleHistory(() => historyApi().loadMore());
    expect(messageIds(historyApi())).toEqual(["ma", "mb"]);
    expect(historyApi().hasMore).toBe(false);
  });

  test("loadMore 向前取紧邻的旧 run（从新到旧），不会跳到最旧的留下空洞", async () => {
    harness().runsByThread.set("t-3", [
      run("run-3"),
      run("run-2"),
      run("run-1"),
    ]);
    harness().responder = (runId) => [runPage(runId, [`m-${runId}`])];
    const api = await mountHistory("t-3");
    expect(messageIds(historyApi())).toEqual(["m-run-3"]);

    await settleHistory(() => api.loadMore());
    expect(messageIds(historyApi())).toEqual(["m-run-2", "m-run-3"]);
    await settleHistory(() => api.loadMore());
    expect(messageIds(historyApi())).toEqual(["m-run-1", "m-run-2", "m-run-3"]);
    expect(historyApi().hasMore).toBe(false);
  });

  test("A 线程的重取在切到 B 之后才写分片：不清空 B 的历史", async () => {
    const api = await mountHistory("t-1");
    expect(messageIds(historyApi())).toEqual(["m3", "m4"]);
    // 重取要等 runs 列表刷新；在它 await 期间切到 t-2
    const refetch = api.refetchRun("run-2").catch(() => undefined);
    await renderHistory("t-2");
    expect(messageIds(historyApi())).toEqual(["mb"]);
    await settleHistory(() => refetch);
    // A 的迟到写入落在这里：B 的历史仍在，没有被重置成空分片
    expect(messageIds(historyApi())).toEqual(["mb"]);
    expect(historyApi().hasMore).toBe(true);
    await settleHistory(() => historyApi().loadMore());
    expect(messageIds(historyApi())).toEqual(["ma", "mb"]);
    expect(historyApi().hasMore).toBe(false);
  });

  test("同一线程重渲染不重复请求 run messages", async () => {
    await mountHistory("t-1");
    const before = harness().messageCalls.length;
    await renderHistory("t-1");
    await renderHistory("t-1");
    expect(harness().messageCalls.length).toBe(before);
    expect(messageIds(historyApi())).toEqual(["m3", "m4"]);
  });

  test("切线程时在途的 loading 不残留到新线程", async () => {
    const api = await mountHistory("t-1");
    const slow = api.loadMore();
    await renderHistory("t-2");
    const apiB = historyApi();
    expect(apiB.loading).toBe(false);
    await slow.catch(() => undefined);
    await flush();
    expect(historyApi().loading).toBe(false);
  });
});
