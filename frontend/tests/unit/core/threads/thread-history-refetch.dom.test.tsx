/**
 * PR3（2026-11-24 前端可靠性批）纵切 C：带新鲜度判定的补齐式重载 `refetchHistory`。
 * 用户显式触发：只重取「未成功加载、非终态、加载后已变化」的 run；终态且未变的旧 run
 * 跳过；在途请求先等它结束，不叠加；失败保留旧分片并可重试。
 * 夹具见 history-test-harness（必须是第一个 import）。
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
  resetHistoryHarness,
  run,
  runPage,
  unmountHistory,
} from "./history-test-harness";
// 夹具必须先于 vitest 求值（vi.mock 的提升规则），顺序与 import/order 相反
// eslint-disable-next-line import/order
import { afterEach, beforeEach, describe, expect, test } from "vitest";

const latestPage = runPage("run-3", ["m3-a", "m3-b"]);
const middlePage = runPage("run-2", ["m2"]);
const oldestPage = runPage("run-1", ["m1"]);

describe("useThreadHistory.refetchHistory：带新鲜度判定的补齐式重载", () => {
  let pages: Record<string, RunPage[]>;

  beforeEach(() => {
    resetHistoryHarness();
    // 真实 API：newest-first
    harness().runsByThread.set("t-1", [
      run("run-3", "running"),
      run("run-2"),
      run("run-1"),
    ]);
    pages = {
      "run-3": [structuredClone(latestPage)],
      "run-2": [structuredClone(middlePage)],
      "run-1": [structuredClone(oldestPage)],
    };
    harness().responder = (runId) => {
      const page = pages[runId];
      if (!page) throw new Error(`unknown run ${runId}`);
      return structuredClone(page);
    };
  });

  afterEach(async () => {
    await unmountHistory();
  });

  /** 把三个 run 都加载进来（首次只加载最新的，其余靠 loadMore）。 */
  async function loadAll() {
    const api = await mountHistory("t-1");
    await settleHistory(() => api.loadMore());
    await settleHistory(() => api.loadMore());
    expect(messageIds(historyApi())).toEqual(["m1", "m2", "m3-a", "m3-b"]);
  }

  function callsFor(runId: string): number {
    return harness().messageCalls.filter((call) => call.runId === runId).length;
  }

  test("终态且加载后未变的旧 run 不重取；加载时在跑、之后结束的 run 重取并补齐新增消息", async () => {
    await loadAll();
    expect(callsFor("run-1")).toBe(1);
    expect(callsFor("run-2")).toBe(1);
    expect(callsFor("run-3")).toBe(1);

    // run-3 在服务端结束并补了尾段；run-1 / run-2 没变
    harness().runsByThread.set("t-1", [
      run("run-3", "success", "2025-01-03T00:00:00Z"),
      { ...run("run-2", "success"), updated_at: "2025-01-01T00:01:00Z" },
      run("run-1"),
    ]);
    pages["run-3"] = [runPage("run-3", ["m3-a", "m3-b", "m3-c"])];

    await settleHistory(() => historyApi().refetchHistory());

    expect(callsFor("run-1")).toBe(1);
    // run-2 的 updated_at 变了：即便仍是终态也要重取（内容可能补过）
    expect(callsFor("run-2")).toBe(2);
    expect(callsFor("run-3")).toBe(2);
    expect(messageIds(historyApi())).toEqual([
      "m1",
      "m2",
      "m3-a",
      "m3-b",
      "m3-c",
    ]);
  });

  test("99 个终态且未变的旧 run 不重取，只有变化的那个重取", async () => {
    // newest-first：run-100 最新；run-1..run-99 都是终态且不会变
    const all = Array.from({ length: 100 }, (_, i) => {
      const runId = `run-${100 - i}`;
      return run(
        runId,
        runId === "run-100" ? "running" : "success",
        "2025-01-01T00:00:00Z",
      );
    });
    harness().runsByThread.set("t-1", all);
    pages = {};
    for (const item of all) {
      pages[item.run_id] = [runPage(item.run_id, [`m-${item.run_id}`])];
    }
    const api = await mountHistory("t-1");
    for (let i = 0; i < 99; i += 1) {
      await settleHistory(() => api.loadMore());
    }
    expect(historyApi().hasMore).toBe(false);

    harness().runsByThread.set("t-1", [
      run("run-100", "success", "2025-01-02T00:00:00Z"),
      ...all.slice(1),
    ]);
    pages["run-100"] = [runPage("run-100", ["m-run-100", "m-run-100-tail"])];
    const before = harness().messageCalls.length;

    await settleHistory(() => historyApi().refetchHistory());

    expect(harness().messageCalls.length).toBe(before + 1);
    expect(harness().messageCalls.at(-1)?.runId).toBe("run-100");
    expect(messageIds(historyApi()).slice(-2)).toEqual([
      "m-run-100",
      "m-run-100-tail",
    ]);
  });

  test("刷新失败的 run 保留旧分片、不被标记为新鲜，可再次重试", async () => {
    await loadAll();
    const goodResponder = harness().responder!;
    let failRun2 = true;
    harness().responder = (runId, query) => {
      if (runId === "run-2" && failRun2) throw new Error("boom 503");
      return goodResponder(runId, query);
    };
    // 让 run-2 与 run-1 都需要重取（updated_at 变了）
    harness().runsByThread.set("t-1", [
      run("run-3", "success", "2025-01-03T00:00:00Z"),
      { ...run("run-2"), updated_at: "2025-01-02T00:00:00Z" },
      { ...run("run-1"), updated_at: "2025-01-01T00:01:00Z" },
    ]);
    pages["run-1"] = [runPage("run-1", ["m1", "m1-bis"])];
    pages["run-2"] = [runPage("run-2", ["m2", "m2-bis"])];
    pages["run-3"] = [runPage("run-3", ["m3-a", "m3-b", "m3-c"])];

    const error = await rejectsWithHistory(() => historyApi().refetchHistory());
    expect(error).toMatchObject({ message: "boom 503" });
    // 以异常退出 act 时，重载期间排队的更新还没提交：等一拍再读快照
    await flush();
    // 失败的 run 保留旧分片；其余 run 照常补齐
    expect(messageIds(historyApi())).toEqual([
      "m1",
      "m1-bis",
      "m2",
      "m3-a",
      "m3-b",
      "m3-c",
    ]);

    failRun2 = false;
    await settleHistory(() => historyApi().refetchHistory());
    // 只重试了失败的那个：其他 run 已是新鲜
    expect(callsFor("run-2")).toBe(3);
    expect(callsFor("run-1")).toBe(2);
    expect(callsFor("run-3")).toBe(2);
    expect(messageIds(historyApi())).toEqual([
      "m1",
      "m1-bis",
      "m2",
      "m2-bis",
      "m3-a",
      "m3-b",
      "m3-c",
    ]);
  });

  test("该 run 已有在途加载：等它结束，不叠加请求", async () => {
    const api = await mountHistory("t-1");
    const slow = deferred<RunPage[]>();
    const goodResponder = harness().responder!;
    let hold = true;
    harness().responder = (runId, query) => {
      if (runId === "run-1" && hold) return slow.promise;
      return goodResponder(runId, query);
    };
    // run-1 的首次加载挂起（用户点了 Load more）
    const loading = api.loadMore().catch(() => undefined);
    await flush();
    expect(callsFor("run-1")).toBe(1);
    hold = false;

    let refetched: unknown = "pending";
    const refetch = historyApi()
      .refetchHistory()
      .then(
        () => {
          refetched = "done";
        },
        (error: unknown) => {
          refetched = error;
        },
      );
    await flush();
    // 在途请求没结束前，补齐式重载不结束
    expect(refetched).toBe("pending");

    slow.resolve([structuredClone(oldestPage)]);
    await settleHistory(() => loading);
    await settleHistory(() => refetch);
    expect(refetched).toBe("done");
    // 在途的那次加载已带着最新内容：没有为 run-1 再发一次；
    // 未加载的 run-2 与非终态的 run-3 照常补取
    expect(callsFor("run-1")).toBe(1);
    expect(callsFor("run-2")).toBe(1);
    expect(callsFor("run-3")).toBe(2);
    expect(messageIds(historyApi())).toEqual(["m1", "m2", "m3-a", "m3-b"]);
  });
});
