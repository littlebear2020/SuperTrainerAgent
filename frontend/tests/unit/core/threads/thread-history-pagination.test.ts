import { expect, test, vi } from "vitest";

import {
  fetchAllRunMessages,
  shouldClearOptimistic,
} from "@/core/threads/hooks";

function page(items: Array<{ seq: number; caller: string }>, hasMore: boolean) {
  return {
    data: items.map((i) => ({
      run_id: "r1",
      seq: i.seq,
      content: { id: `m${i.seq}`, type: "human", content: `msg${i.seq}` },
      metadata: { caller: i.caller },
      created_at: "2026-07-27T00:00:00Z",
    })),
    has_more: hasMore,
  };
}

test("fetchAllRunMessages paginates with after_seq until has_more is false", async () => {
  const first = page(
    Array.from({ length: 200 }, (_, i) => ({ seq: i + 1, caller: "user" })),
    true,
  );
  const second = page([{ seq: 201, caller: "user" }], false);
  const fetchImpl = vi
    .fn()
    .mockResolvedValueOnce({ json: () => Promise.resolve(first) })
    .mockResolvedValueOnce({ json: () => Promise.resolve(second) });

  const result = await fetchAllRunMessages("t1", "r1", fetchImpl as never);
  expect(result).toHaveLength(201);
  expect(fetchImpl).toHaveBeenCalledTimes(2);
  // 第二页必须带 after_seq=200 游标
  expect(String(fetchImpl.mock.calls[1]?.[0])).toContain("after_seq=200");
  expect(String(fetchImpl.mock.calls[1]?.[0])).toContain("limit=200");
});

test("fetchAllRunMessages filters middleware-caller messages", async () => {
  const mixed = page(
    [
      { seq: 1, caller: "user" },
      { seq: 2, caller: "middleware:summarization" },
    ],
    false,
  );
  const fetchImpl = vi
    .fn()
    .mockResolvedValueOnce({ json: () => Promise.resolve(mixed) });
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
