import { expect, test, vi } from "vitest";

import {
  fetchAllRunMessages,
  shouldClearOptimistic,
} from "@/core/threads/hooks";

/**
 * 真实 API：GET /threads/{tid}/runs/{rid}/messages 首页不带游标、返回**最新**的
 * limit 条（页内 seq 升序），`has_more` 时用本页第一条的 seq 作 `before_seq`
 * 继续向前取旧页。旧实现把首页当最早页、用 after_seq 向后翻——这里按真实契约断言。
 */
type Row = { seq: number; caller?: string };

function page(
  items: Row[],
  hasMore: boolean,
  runId = "r1",
): { data: Array<ReturnType<typeof row>>; has_more: boolean } {
  return {
    data: items.map((i) => row(i, runId)),
    has_more: hasMore,
  };
}

function row(i: Row, runId = "r1") {
  return {
    run_id: runId,
    seq: i.seq,
    content: { id: `m${i.seq}`, type: "human", content: `msg${i.seq}` },
    metadata: { caller: i.caller ?? "user" },
    created_at: "2026-07-27T00:00:00Z",
  };
}

const range = (from: number, to: number): Row[] =>
  Array.from({ length: to - from + 1 }, (_, i) => ({ seq: from + i }));

function fetchReturning(pages: Array<{ data: unknown; has_more: boolean }>) {
  const fetchImpl = vi.fn();
  for (const p of pages) {
    fetchImpl.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: () => Promise.resolve(p),
    });
  }
  return fetchImpl;
}

test("451 条：首页 252..451、before_seq 页 52..251、1..51，最终恰好 1..451", async () => {
  const fetchImpl = fetchReturning([
    page(range(252, 451), true),
    page(range(52, 251), true),
    page(range(1, 51), false),
  ]);

  const result = await fetchAllRunMessages("t1", "r1", fetchImpl as never);

  expect(result.map((m) => m.seq)).toEqual(range(1, 451).map((r) => r.seq));
  expect(fetchImpl).toHaveBeenCalledTimes(3);
  const urls = fetchImpl.mock.calls.map((call) => String(call[0]));
  expect(urls[0]).toMatch(/\/runs\/r1\/messages\?/);
  expect(urls[0]).not.toContain("before_seq");
  expect(urls[0]).not.toContain("after_seq");
  expect(urls[0]).toContain("limit=200");
  expect(urls[1]).toContain("before_seq=252");
  expect(urls[2]).toContain("before_seq=52");
});

test("超过原 25 页 / 5000 条上限（5001 条、26 页）不丢首条", async () => {
  // 首页 4802..5001（200 条），之后 24 整页 + 最后一页只有 seq 1：共 26 页
  const pages: Array<{ data: unknown; has_more: boolean }> = [
    page(range(4802, 5001), true),
  ];
  let cursor = 4802;
  for (let i = 0; i < 25; i++) {
    const from = Math.max(1, cursor - 200);
    pages.push(page(range(from, cursor - 1), from > 1));
    cursor = from;
  }
  const fetchImpl = fetchReturning(pages);

  const result = await fetchAllRunMessages("t1", "r1", fetchImpl as never);

  expect(result).toHaveLength(5001);
  expect(result[0]!.seq).toBe(1);
  expect(result.at(-1)!.seq).toBe(5001);
  expect(fetchImpl).toHaveBeenCalledTimes(26);
});

test("游标不前进 / 重复时显式失败，而不是死循环", async () => {
  // has_more 为真但返回的首页 seq 没有变小：before_seq 不前进 → 必须拒绝
  const fetchImpl = fetchReturning([
    page(range(252, 451), true),
    page(range(252, 451), true), // 重复页
  ]);

  await expect(
    fetchAllRunMessages("t1", "r1", fetchImpl as never),
  ).rejects.toThrow(/cursor did not advance/);
  // 失败前只发了必要的请求，没有无限翻页
  expect(fetchImpl.mock.calls.length).toBeLessThanOrEqual(3);
});

test("has_more 为真但页为空同样显式失败", async () => {
  const fetchImpl = fetchReturning([{ data: [], has_more: true }]);
  await expect(
    fetchAllRunMessages("t1", "r1", fetchImpl as never),
  ).rejects.toThrow(/cursor did not advance|empty/);
});

test("端点返回非 2xx：显式失败，不把错误响应体当成空历史", async () => {
  const fetchImpl = vi.fn().mockResolvedValue({
    ok: false,
    status: 500,
    json: () => Promise.resolve({ detail: "boom" }),
  });
  await expect(
    fetchAllRunMessages("t1", "r1", fetchImpl as never),
  ).rejects.toThrow(/HTTP 500/);
});

test("页里出现非 number 的 seq：显式失败，不静默合并到同一个键下丢消息", async () => {
  const fetchImpl = fetchReturning([
    {
      data: [row({ seq: undefined } as never), row({ seq: 2 })],
      has_more: false,
    },
  ] as never[]);
  await expect(
    fetchAllRunMessages("t1", "r1", fetchImpl as never),
  ).rejects.toThrow(/seq/);
});

test("超过 500 页的安全上限：抛错而不是静默截断或无限翻页", async () => {
  const pages: Array<{ data: unknown; has_more: boolean }> = [];
  let cursor = 200 * 501;
  for (let i = 0; i < 501; i += 1) {
    const from = cursor - 199;
    pages.push(page(range(from, cursor), true));
    cursor = from - 1;
  }
  const fetchImpl = fetchReturning(pages);
  await expect(
    fetchAllRunMessages("t1", "r1", fetchImpl as never),
  ).rejects.toThrow(/500-page safety limit/);
  // 第 501 页不再请求：到上限就停
  expect(fetchImpl).toHaveBeenCalledTimes(500);
});

test("fetchAllRunMessages filters middleware-caller messages", async () => {
  const mixed = page(
    [{ seq: 1 }, { seq: 2, caller: "middleware:summarization" }],
    false,
  );
  const fetchImpl = fetchReturning([mixed]);
  const result = await fetchAllRunMessages("t1", "r1", fetchImpl as never);
  expect(result.map((m) => m.content.id)).toEqual(["m1"]);
});

test("shouldClearOptimistic compares tail id, not length", () => {
  // context 压缩后长度可能不变——尾部 id 变化才是服务器回包的可靠信号。
  const msgs = [{ id: "server-human-1" }] as never[];
  expect(shouldClearOptimistic("old-tail", msgs, 1)).toBe(true);
  expect(shouldClearOptimistic("server-human-1", msgs, 1)).toBe(false);
  expect(shouldClearOptimistic("old-tail", msgs, 0)).toBe(false);
});
