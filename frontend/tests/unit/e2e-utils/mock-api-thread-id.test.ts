import type { Route } from "@playwright/test";
import { expect, test } from "vitest";

import { MOCK_THREAD_ID, runStreamThreadId } from "../../e2e/utils/mock-api";

/**
 * PR3（2026-11-24 前端可靠性批）：mock 的 run stream 线程 id 解析。无状态的
 * `/runs/stream` 路由路径里没有线程 id——SDK 把它放在请求体里，必须先读请求体，
 * 否则页面预生成的 UUID 会被换成固定值，掩盖「回显预生成 id」的问题。
 */
function route(url: string, postData: string | null): Route {
  return {
    request: () => ({ url: () => url, postData: () => postData }),
  } as unknown as Route;
}

test("无状态 /runs/stream：线程 id 取请求体", () => {
  expect(
    runStreamThreadId(
      route(
        "http://localhost/api/langgraph/runs/stream",
        JSON.stringify({
          thread_id: "11111111-1111-4111-8111-111111111111",
          assistant_id: "lead_agent",
        }),
      ),
    ),
  ).toBe("11111111-1111-4111-8111-111111111111");
});

test("/threads/{id}/runs/stream：请求体缺失时取路径", () => {
  expect(
    runStreamThreadId(
      route(
        "http://localhost/api/langgraph/threads/t-from-path/runs/stream",
        null,
      ),
    ),
  ).toBe("t-from-path");
});

test("请求体与路径都没有线程 id 时回落固定值", () => {
  expect(
    runStreamThreadId(
      route("http://localhost/api/langgraph/runs/stream", "not json"),
    ),
  ).toBe(MOCK_THREAD_ID);
});
