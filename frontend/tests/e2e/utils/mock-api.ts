/**
 * Shared mock helpers for E2E tests.
 *
 * Intercepts all LangGraph / Backend API endpoints so tests can run without
 * a real backend.  Each test file imports `mockLangGraphAPI` and
 * `handleRunStream` from here.
 */

import { createHash } from "node:crypto";

import type { Message } from "@langchain/langgraph-sdk";
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

export type MockArtifactFile = {
  path: string;
  name?: string;
  media_kind?: "video" | "image" | "audio" | "document" | "other";
  size?: number | null;
  modified_at?: string | null;
  exists?: boolean;
};

export type MockArtifactSummary = {
  thread_id: string;
  delivery_state:
    | "delivered"
    | "not_delivered"
    | "partial"
    | "missing"
    | "unknown";
  files?: MockArtifactFile[];
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

/** 一个 run 的最小描述（`runs` 选项按 newest-first 给出，与真实 API 一致）。 */
export type MockRun = {
  run_id: string;
  /** 归属线程；缺省对全部线程可见。 */
  thread_id?: string;
  status?: RunStatus;
  created_at?: string;
  updated_at?: string;
};

export type RunStatus =
  | "pending"
  | "running"
  | "success"
  | "error"
  | "timeout"
  | "interrupted";

function toRunResponse(run: MockRun, threadId: string) {
  return {
    run_id: run.run_id,
    thread_id: threadId,
    assistant_id: "lead_agent",
    created_at: run.created_at ?? "2025-01-01T00:00:00Z",
    updated_at: run.updated_at ?? run.created_at ?? "2025-01-01T00:00:00Z",
    status: run.status ?? "success",
    metadata: {},
    multitask_strategy: "reject",
    kwargs: {},
  };
}

export type MockAPIOptions = {
  threads?: MockThread[];
  agents?: MockAgent[];
  skills?: MockSkill[];
  tokenUsageEnabled?: boolean;
  /** 线程的 run 列表（newest-first）。缺省返回空列表。 */
  runs?: MockRun[];
  /** 按 runId 登记的历史消息（seq 从 1 起），端点据此分页。 */
  runMessages?: Record<string, Message[]>;
  /**
   * 产物正文：原始路径 → 文件当前内容（`GET /api/threads/{id}/artifacts/{path}`）。
   * 未登记的路径返回 404。write_file 的 append / str_replace 读的就是这个端点。
   * `.skill` 包要按 `"<path>.skill/SKILL.md"` 登记——前端对 .skill 路径会自动追加
   * `/SKILL.md` 再请求。
   */
  artifacts?: Record<string, string>;
  /** 批量作品摘要；未播种的线程默认 unknown（无 checkpoint）。 */
  artifactSummaries?: MockArtifactSummary[];
};

export interface MockLangGraphAPI {
  /**
   * `POST /threads` 请求里的 thread_id（页面预生成的 UUID），按创建顺序。
   * 后端会照单采用客户端给的 thread_id，mock 同样回显——用例以此断言地址栏，
   * 不再依赖固定的 MOCK_THREAD_ID。
   */
  readonly createdThreadIds: string[];
  readonly artifactSummaryRequests: string[][];
  seedArtifactSummary(summary: MockArtifactSummary): void;
  failArtifactSummaries(status?: number): void;
  clearArtifactSummaryFailure(): void;
}

/** 路径里 `/threads/{id}` 的线程 id，取不到时回落固定值。 */
function threadIdFromUrl(url: string, fallback = MOCK_THREAD_ID): string {
  return /\/threads\/([^/?#]+)/.exec(url)?.[1] ?? fallback;
}

/**
 * run stream 请求对应的线程 id，取不到时回落固定值。无状态的 `/runs/stream` 路由路径里
 * 没有线程 id（SDK 把它放在请求体里），所以先读请求体，再看 `/threads/{id}/runs/stream`。
 */
export function runStreamThreadId(route: Route): string {
  const url = route.request().url();
  const fromBody = requestedThreadId(route.request().postData());
  if (fromBody) return fromBody;
  return /\/runs\/stream(?:\?|$)/.test(url)
    ? threadIdFromUrl(url)
    : MOCK_THREAD_ID;
}

/** 请求体里非空字符串的 thread_id（页面预生成的 UUID）；缺失或非法返回 undefined。 */
function requestedThreadId(postData: string | null): string | undefined {
  if (!postData) return undefined;
  try {
    const parsed = JSON.parse(postData) as { thread_id?: unknown };
    return typeof parsed.thread_id === "string" && parsed.thread_id !== ""
      ? parsed.thread_id
      : undefined;
  } catch {
    return undefined;
  }
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
  const artifactSummaryRequests: string[][] = [];
  const artifactSummaries = new Map(
    (options?.artifactSummaries ?? []).map((summary) => [
      summary.thread_id,
      summary,
    ]),
  );
  let failArtifactSummariesStatus: number | undefined;

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

  void page.route("**/api/works/artifact-summaries", (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    const request = route.request().postDataJSON() as {
      thread_ids?: string[];
    };
    const requestedIds = (request.thread_ids ?? []).map((id) => id.trim());
    if (
      requestedIds.length === 0 ||
      requestedIds.length > 50 ||
      requestedIds.some((id) => id.length === 0 || id.length > 128)
    ) {
      return route.fulfill({
        status: 422,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Invalid thread ID batch" }),
      });
    }
    const threadIds = [...new Set(requestedIds)];
    artifactSummaryRequests.push(threadIds);
    if (failArtifactSummariesStatus !== undefined) {
      const status = failArtifactSummariesStatus;
      return route.fulfill({
        status,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Mock artifact summaries failure" }),
      });
    }
    const body = threadIds.map((threadId) => {
      const seeded = artifactSummaries.get(threadId);
      return {
        thread_id: threadId,
        delivery_state: seeded?.delivery_state ?? "unknown",
        files: (seeded?.files ?? []).map((file) => ({
          path: file.path,
          name: file.name ?? file.path.split("/").pop() ?? file.path,
          media_kind: file.media_kind ?? "other",
          size: file.size ?? null,
          modified_at: file.modified_at ?? null,
          exists: file.exists ?? true,
        })),
      };
    });
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
      // 只有非空字符串才当作页面预生成的线程 id；缺失或非法时回落固定值
      const threadId =
        requestedThreadId(route.request().postData()) ?? MOCK_THREAD_ID;
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
        body: JSON.stringify({
          thread_id: threadIdFromUrl(route.request().url()),
        }),
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
      const threadId = threadIdFromUrl(route.request().url());
      // 与真实 API 一致：按线程返回。未声明 thread_id 的 run 对全部线程可见
      const list = options?.runs
        ? options.runs
            .filter((run) => !run.thread_id || run.thread_id === threadId)
            .map((run) => toRunResponse(run, run.thread_id ?? threadId))
        : [];
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(list),
      });
    }
    return route.fallback();
  });

  // 单 run 历史消息：按真实端点分页（首页不带游标 = 最新 limit 条、页内 seq 升序；
  // before_seq 向前取旧页）。只有 `runMessages` 里登记过的 run 才命中，其余交给用例自定的路由。
  void page.route(
    // 带不带查询串都命中（不带 = 后端默认 limit）：否则未命中的请求会落到真实网络上挂住用例
    /\/api\/threads\/[^/]+\/runs\/[^/?#]+\/messages(?:\?|$)/,
    (route) => {
      const url = new URL(route.request().url());
      const runId = url.pathname.split("/")[5] ?? "";
      const messages = options?.runMessages?.[runId];
      if (!messages) return route.fallback();
      const rawLimit = Number(url.searchParams.get("limit") ?? "50");
      const limit = Number.isFinite(rawLimit) && rawLimit > 0 ? rawLimit : 50;
      const beforeSeq = Number(url.searchParams.get("before_seq"));
      const ceiling =
        url.searchParams.has("before_seq") && Number.isFinite(beforeSeq)
          ? beforeSeq
          : Number.POSITIVE_INFINITY;
      const rows = messages.map((content, index) => ({
        run_id: runId,
        seq: index + 1,
        content,
        metadata: { caller: "user" },
        created_at: "2025-01-01T00:00:00Z",
      }));
      // 页内 seq 升序：从 ceiling 向前取 limit 条
      const eligible = rows.filter((row) => row.seq < ceiling);
      const page = eligible.slice(Math.max(0, eligible.length - limit));
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          data: page,
          has_more: eligible.length > page.length,
        }),
      });
    },
  );

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
          thread_id: threadIdFromUrl(route.request().url()),
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

  // 产物正文：路径逐段解码后按原始路径查登记的内容（与后端一致——前端对来自 tool
  // args 的原始路径按字面编码过，解码回来就是原始路径）
  void page.route(/\/api\/threads\/[^/]+\/artifacts(?:\/|$|\?)/, (route) => {
    // 没登记产物内容的用例保持原样（请求照旧落到别处）
    if (!options?.artifacts) return route.fallback();
    if (route.request().method() !== "GET") return route.fallback();
    const url = new URL(route.request().url());
    // 下载请求（attachment 响应头）不在本夹具的范围内：交还给用例自定的路由
    if (url.searchParams.has("download")) return route.fallback();
    // 产物路径里可能再有「/artifacts」（如 /mnt/outputs/artifacts/x.md）：只切掉端点前缀
    const marker = "/artifacts";
    const encodedPath = url.pathname.slice(
      url.pathname.indexOf(marker) + marker.length,
    );
    const filepath = encodedPath
      .split("/")
      .map((segment) => {
        try {
          return decodeURIComponent(segment);
        } catch {
          return segment;
        }
      })
      .join("/");
    const artifacts = options.artifacts;
    // 自有属性：路径恰好是 constructor / toString 一类时不会命中原型链上的成员
    if (!Object.hasOwn(artifacts, filepath)) {
      return route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Artifact not found" }),
      });
    }
    const content = artifacts[filepath]!;
    return route.fulfill({
      status: 200,
      contentType: "text/plain; charset=utf-8",
      // 与后端文本分支一致：ETag 里带正文的 sha256（前端拿它做 PUT 的乐观并发基线）
      headers: {
        etag: `"${createHash("sha256").update(content).digest("hex")}"`,
      },
      body: content,
    });
  });

  // 对外给只读快照，避免用例误改 mock 内部状态
  return {
    get createdThreadIds() {
      return [...createdThreadIds];
    },
    get artifactSummaryRequests() {
      return artifactSummaryRequests.map((ids) => [...ids]);
    },
    seedArtifactSummary(summary: MockArtifactSummary) {
      artifactSummaries.set(summary.thread_id, summary);
    },
    failArtifactSummaries(status = 404) {
      failArtifactSummariesStatus = status;
    },
    clearArtifactSummaryFailure() {
      failArtifactSummariesStatus = undefined;
    },
  };
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
