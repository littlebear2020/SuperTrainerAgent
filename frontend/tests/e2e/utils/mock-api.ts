/**
 * Shared mock helpers for E2E tests.
 *
 * Intercepts all LangGraph / Backend API endpoints so tests can run without
 * a real backend.  Each test file imports `mockLangGraphAPI` and
 * `handleRunStream` from here.
 */

import type { Page, Route } from "@playwright/test";

// ---------------------------------------------------------------------------
// Constants — deterministic IDs used across tests
// ---------------------------------------------------------------------------

export const MOCK_THREAD_ID = "00000000-0000-0000-0000-000000000001";
export const MOCK_THREAD_ID_2 = "00000000-0000-0000-0000-000000000002";
export const MOCK_RUN_ID = "00000000-0000-0000-0000-000000000099";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type MockThread = {
  thread_id: string;
  title?: string;
  created_at?: string;
  updated_at?: string;
  agent_name?: string;
  project_type?: string;
};

export type MockAgent = {
  name: string;
  description?: string;
  system_prompt?: string;
  skills?: string[] | null;
  suggestion_prompts?: { label: string; prompt: string }[] | null;
};

export type MockSkill = {
  name: string;
  description?: string;
  enabled?: boolean;
};

export type MockAPIOptions = {
  threads?: MockThread[];
  agents?: MockAgent[];
  skills?: MockSkill[];
  tokenUsageEnabled?: boolean;
};

export interface MockLangGraphAPI {
  /**
   * `POST /threads` 请求里的 thread_id（页面预生成的 UUID），按创建顺序。
   * 后端会照单采用客户端给的 thread_id，mock 同样回显——用例以此断言地址栏，
   * 不再依赖固定的 MOCK_THREAD_ID。
   */
  readonly createdThreadIds: string[];
}

/** run stream 请求对应的线程 id：`/threads/{id}/runs/stream`，取不到时回落固定值。 */
export function runStreamThreadId(route: Route): string {
  const match = /\/threads\/([^/?#]+)\/runs\/stream/.exec(
    route.request().url(),
  );
  return match?.[1] ?? MOCK_THREAD_ID;
}

/** `/…/threads/{id}` 这类以线程 id 结尾的路径取其 id。 */
function pathThreadId(route: Route): string {
  const match = /\/threads\/([^/?#]+)/.exec(route.request().url());
  return match?.[1] ?? MOCK_THREAD_ID;
}

// ---------------------------------------------------------------------------
// mockLangGraphAPI
// ---------------------------------------------------------------------------

/**
 * Mock all LangGraph API endpoints that the frontend calls on page load and
 * during message sending.  Without these mocks the pages would hang waiting
 * for a real backend.
 */
export function mockLangGraphAPI(
  page: Page,
  options?: MockAPIOptions,
): MockLangGraphAPI {
  const threads = options?.threads ?? [];
  const agents = options?.agents ?? [];
  const skills = options?.skills ?? [];
  const tokenUsageEnabled = options?.tokenUsageEnabled ?? false;
  const createdThreadIds: string[] = [];

  // Thread search — sidebar thread list & chats list page
  void page.route("**/api/langgraph/threads/search", (route) => {
    const request = route.request().postDataJSON() as {
      limit?: number;
      offset?: number;
    };
    const offset = request.offset ?? 0;
    const limit = request.limit ?? 50;
    const allThreads = threads.map((t) => ({
      thread_id: t.thread_id,
      created_at: t.created_at ?? "2025-01-01T00:00:00Z",
      updated_at: t.updated_at ?? t.created_at ?? "2025-01-01T00:00:00Z",
      metadata: {
        ...(t.agent_name ? { agent_name: t.agent_name } : {}),
        ...(t.project_type ? { project_type: t.project_type } : {}),
      },
      status: "idle",
      values: { title: t.title ?? "Untitled" },
    }));
    const body = allThreads.slice(offset, offset + Math.max(0, limit));
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });

  // Thread create — called when user sends first message in a new chat.
  // 回显请求里的 thread_id（页面预生成的 UUID），与后端「Optional thread ID」一致。
  void page.route("**/api/langgraph/threads", (route) => {
    if (route.request().method() === "POST") {
      const payload = route.request().postData() ?? "";
      let requested: string | undefined;
      try {
        requested = (JSON.parse(payload) as { thread_id?: string }).thread_id;
      } catch {
        requested = undefined;
      }
      const threadId = requested ?? MOCK_THREAD_ID;
      createdThreadIds.push(threadId);
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          thread_id: threadId,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          metadata: {},
          status: "idle",
          values: {},
        }),
      });
    }
    return route.fallback();
  });

  // Thread update (PATCH) — metadata update after creation
  void page.route("**/api/langgraph/threads/*", (route) => {
    if (route.request().method() === "PATCH") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ thread_id: pathThreadId(route) }),
      });
    }
    return route.fallback();
  });

  // Thread history — useStream fetches state history on mount
  void page.route("**/api/langgraph/threads/*/history", (route) => {
    const url = route.request().url();

    // For threads that exist in our mock data, return history with messages
    const matchingThread = threads.find((t) => url.includes(t.thread_id));
    if (matchingThread) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            values: {
              title: matchingThread.title ?? "Untitled",
              messages: [
                {
                  type: "human",
                  id: `msg-human-${matchingThread.thread_id}`,
                  content: [{ type: "text", text: "Previous question" }],
                },
                {
                  type: "ai",
                  id: `msg-ai-${matchingThread.thread_id}`,
                  content: `Response in thread ${matchingThread.title ?? matchingThread.thread_id}`,
                },
              ],
            },
            next: [],
            metadata: {},
            created_at: "2025-01-01T00:00:00Z",
            parent_config: null,
          },
        ]),
      });
    }

    // New threads — empty history
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: "[]",
    });
  });

  // Thread state — getState for individual thread
  void page.route("**/api/langgraph/threads/*/state", (route) => {
    if (route.request().method() === "GET") {
      const url = route.request().url();
      const matchingThread = threads.find((t) => url.includes(t.thread_id));
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          values: {
            title: matchingThread?.title ?? "Untitled",
            messages: matchingThread
              ? [
                  {
                    type: "human",
                    id: `msg-human-${matchingThread.thread_id}`,
                    content: [{ type: "text", text: "Previous question" }],
                  },
                  {
                    type: "ai",
                    id: `msg-ai-${matchingThread.thread_id}`,
                    content: `Response in thread ${matchingThread.title ?? matchingThread.thread_id}`,
                  },
                ]
              : [],
          },
          next: [],
          metadata: {},
          created_at: "2025-01-01T00:00:00Z",
        }),
      });
    }
    return route.fallback();
  });

  // The URL carries a query string (e.g. `?limit=10&offset=0`), which Playwright
  // glob `*` does NOT cross, so we match with a regex anchored to `/runs`
  // followed by `?` or end-of-string.  This must NOT match `/runs/stream`.
  void page.route(/\/api\/langgraph\/threads\/[^/]+\/runs(\?|$)/, (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: "[]",
      });
    }
    return route.fallback();
  });

  // Run stream — returns a minimal SSE response with an AI message
  void page.route("**/api/langgraph/runs/stream", handleRunStream);
  void page.route("**/api/langgraph/threads/*/runs/stream", handleRunStream);

  // Skills list — target guides suppress this legacy area; non-target regression uses it.
  void page.route("**/api/skills", (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          skills: skills.map((skill) => ({ enabled: true, ...skill })),
        }),
      });
    }
    return route.fallback();
  });

  // Models list — model picker dropdown
  void page.route("**/api/models", (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          models: [],
          token_usage: { enabled: tokenUsageEnabled },
        }),
      });
    }
    return route.fallback();
  });

  void page.route("**/api/threads/*/token-usage", (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          thread_id: pathThreadId(route),
          total_input_tokens: 600_000,
          total_output_tokens: 387_654,
          total_tokens: 987_654,
        }),
      });
    }
    return route.fallback();
  });

  // Backend suggestions API remains available for compatibility; chat never calls it automatically.
  void page.route("**/api/threads/*/suggestions", (route) => {
    if (route.request().method() === "POST") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ suggestions: [] }),
      });
    }
    return route.fallback();
  });

  // Agents list — sidebar & gallery page
  void page.route("**/api/agents", (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ agents }),
      });
    }
    return route.fallback();
  });

  // Individual agent — agent chat page
  void page.route("**/api/agents/*", (route) => {
    if (route.request().method() === "GET") {
      const url = route.request().url();
      const agent = agents.find((a) => url.endsWith(`/api/agents/${a.name}`));
      if (agent) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(agent),
        });
      }
    }
    return route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Agent not found" }),
    });
  });

  return { createdThreadIds };
}

// ---------------------------------------------------------------------------
// handleRunStream
// ---------------------------------------------------------------------------

/**
 * Build a minimal SSE stream that the LangGraph SDK can parse.
 * The stream returns a single AI message: "Hello from DeerFlow!".
 * metadata 与 Content-Location 回显请求路径里的线程 id（SDK 的 onCreated 用它）。
 */
export function handleRunStream(route: Route) {
  const threadId = runStreamThreadId(route);
  const events = [
    {
      event: "metadata",
      data: { run_id: MOCK_RUN_ID, thread_id: threadId },
    },
    {
      event: "values",
      data: {
        messages: [
          {
            type: "human",
            id: "msg-human-1",
            content: [{ type: "text", text: "Hello" }],
          },
          {
            type: "ai",
            id: "msg-ai-1",
            content: "Hello from DeerFlow!",
          },
        ],
      },
    },
    { event: "end", data: {} },
  ];

  const body = events
    .map((e) => `event: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`)
    .join("");

  return route.fulfill({
    status: 200,
    contentType: "text/event-stream",
    headers: {
      "Content-Location": `/threads/${threadId}/runs/${MOCK_RUN_ID}`,
    },
    body,
  });
}

export function createTokenUsageRunStreamHandler(
  turns: readonly (readonly Record<string, unknown>[])[],
  options?: { terminalEvent?: "end" | "error" },
) {
  let requestCount = 0;

  return (route: Route) => {
    requestCount += 1;
    const runId = `token-usage-run-${requestCount}`;
    const threadId = runStreamThreadId(route);
    const events = [
      {
        event: "metadata",
        data: { run_id: runId, thread_id: threadId },
      },
      {
        event: "values",
        data: { messages: turns.slice(0, requestCount).flat() },
      },
      options?.terminalEvent === "error"
        ? { event: "error", data: { error: "Token usage test stream error" } }
        : { event: "end", data: {} },
    ];
    const body = events
      .map(
        (event) =>
          `event: ${event.event}\ndata: ${JSON.stringify(event.data)}\n\n`,
      )
      .join("");

    return route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      headers: {
        "Content-Location": `/threads/${threadId}/runs/${runId}`,
      },
      body,
    });
  };
}
