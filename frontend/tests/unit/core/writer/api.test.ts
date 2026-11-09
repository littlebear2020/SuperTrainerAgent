/**
 * Writer create-doc API contract tests.
 *
 * Mirrors tests/unit/core/voice-twin/hooks.test.ts: mock the fetcher + config,
 * then assert createWriterDoc's request shape, success parsing (id preserved
 * for navigation), and error propagation (backend `detail` surfaces so the
 * hook's onError → toast.error shows a meaningful message).
 *
 * The bug this guards: new-doc-dialog's create mutation had no onError, so a
 * 403/503/500 was swallowed by React Query and the user saw "nothing happens,
 * no navigation". These tests pin the contract that errors throw with detail.
 */

import { expect, test, vi, beforeEach } from "vitest";

vi.mock("@/core/api/fetcher", () => ({
  fetch: vi.fn(),
}));

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://localhost:8001",
}));

import { fetch } from "@/core/api/fetcher";
import {
  createWriterDoc,
  streamAgentEdit,
  streamAssist,
  updateWriterDoc,
} from "@/core/writer/api";

const mockFetch = vi.mocked(fetch);

/** Cast a partial mock to a Response (vitest mocks don't need a real Response). */
function asRes(obj: Record<string, unknown>): Response {
  return obj as unknown as Response;
}

beforeEach(() => {
  mockFetch.mockReset();
});

// ── Success: id preserved so router.push can navigate ─────────────────────

test("createWriterDoc POSTs to /api/writer/docs and returns parsed doc with id", async () => {
  mockFetch.mockResolvedValueOnce(
    asRes({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve({
          id: "doc_abc123",
          user_id: "default",
          title: "未命名文章",
          content_json: "# 标题\n",
          doc_type: "article",
        }),
    }),
  );

  const doc = await createWriterDoc({
    title: "未命名文章",
    content_json: "# 标题\n",
    doc_type: "article",
  });

  const url = mockFetch.mock.calls[0]![0] as string;
  const options = mockFetch.mock.calls[0]![1] as Record<string, unknown>;
  expect(url).toBe("http://localhost:8001/api/writer/docs");
  expect(options.method).toBe("POST");
  expect(JSON.parse(options.body as string)).toEqual({
    title: "未命名文章",
    content_json: "# 标题\n",
    doc_type: "article",
  });

  // id must survive parsing — this is what router.push('/workspace/writer/${id}') needs
  expect(doc.id).toBe("doc_abc123");
  expect(doc.doc_type).toBe("article");
});

// ── streamAssist doc_type forwarding (Task W2) ─────────────────────────────

test("streamAssist forwards doc_type in POST body to /api/ai/assist", async () => {
  mockFetch.mockResolvedValueOnce(asRes({ ok: true, status: 200 }));

  // Drain the generator — it returns early because the mock response has no body.
  const chunks: string[] = [];
  for await (const chunk of streamAssist({
    op: "polish",
    text: "test",
    doc_type: "article",
  })) {
    chunks.push(chunk);
  }
  expect(chunks).toEqual([]);

  const url = mockFetch.mock.calls[0]![0] as string;
  const options = mockFetch.mock.calls[0]![1] as Record<string, unknown>;
  expect(url).toBe("http://localhost:8001/api/ai/assist");
  expect(options.method).toBe("POST");
  expect(JSON.parse(options.body as string)).toHaveProperty(
    "doc_type",
    "article",
  );
});

// ── Error propagation: backend detail surfaces (the fix for silent failure) ─

test("createWriterDoc throws with backend detail on 403 CSRF missing", async () => {
  mockFetch.mockResolvedValueOnce(
    asRes({
      ok: false,
      status: 403,
      statusText: "Forbidden",
      json: () =>
        Promise.resolve({
          detail: "CSRF token missing. Include X-CSRF-Token header.",
        }),
    }),
  );

  await expect(
    createWriterDoc({ title: "t", content_json: "", doc_type: "general" }),
  ).rejects.toThrow("CSRF token missing. Include X-CSRF-Token header.");
});

test("createWriterDoc throws with backend detail on 503 service unavailable", async () => {
  mockFetch.mockResolvedValueOnce(
    asRes({
      ok: false,
      status: 503,
      statusText: "Service Unavailable",
      json: () => Promise.resolve({ detail: "Writer service not available" }),
    }),
  );

  await expect(
    createWriterDoc({ title: "t", content_json: "", doc_type: "general" }),
  ).rejects.toThrow("Writer service not available");
});

test("createWriterDoc falls back to generic message with HTTP status when detail absent", async () => {
  mockFetch.mockResolvedValueOnce(
    asRes({
      ok: false,
      status: 500,
      statusText: "Internal Server Error",
      json: () => Promise.reject(new Error("not json")),
    }),
  );

  await expect(
    createWriterDoc({ title: "t", content_json: "", doc_type: "general" }),
  ).rejects.toThrow("Failed to create document (HTTP 500)");
});

// ── streamAgentEdit proactive-search removal (Task W3) ─────────────────────
//
// Spec §8.6: the hidden "请先使用搜索工具获取最新资料和案例…" prepend for
// article/case must be removed — search is now explicit toggle-only (the web
// hint from dataSources, kept). These tests pin that the heavy-channel prompt
// no longer auto-prepends the search instruction.

/** Drain streamAgentEdit to completion (mock fetch returns ok+no body → early return). */
async function drainAgentEdit(opts: Parameters<typeof streamAgentEdit>[0]) {
  mockFetch.mockResolvedValueOnce(asRes({ ok: true, status: 200 }));
  const chunks: unknown[] = [];
  for await (const chunk of streamAgentEdit(opts)) {
    chunks.push(chunk);
  }
  return chunks;
}

/** Extract the built prompt from the captured fetch call's POST body. */
function getAgentEditPrompt(): string {
  const body = mockFetch.mock.calls[0]![1] as Record<string, unknown>;
  const parsed = JSON.parse(body.body as string) as {
    input: { messages: { content: string }[] };
  };
  return parsed.input.messages[0]!.content;
}

test("streamAgentEdit does NOT prepend proactive-search instruction for article", async () => {
  await drainAgentEdit({
    instruction: "润色这段文字",
    markdown: "# 测试标题\n\n正文内容",
    mode: "polish",
    contentType: "article",
  });

  const prompt = getAgentEditPrompt();
  expect(prompt).not.toContain("请先使用搜索工具获取最新资料和案例");
  // Content-type tag is still injected (only the search prepend is removed)
  expect(prompt).toContain("[类型: article]");
});

test("streamAgentEdit does NOT prepend proactive-search instruction for case", async () => {
  await drainAgentEdit({
    instruction: "润色这段文字",
    markdown: "# 案例标题\n\n背景内容",
    mode: "polish",
    contentType: "case",
  });

  const prompt = getAgentEditPrompt();
  expect(prompt).not.toContain("请先使用搜索工具获取最新资料和案例");
  expect(prompt).toContain("[类型: case]");
});

test("streamAgentEdit keeps web-toggle hint when dataSources includes web", async () => {
  await drainAgentEdit({
    instruction: "润色这段文字",
    markdown: "# 测试标题\n\n正文内容",
    mode: "polish",
    contentType: "article",
    dataSources: ["web"],
  });

  const prompt = getAgentEditPrompt();
  // The explicit web-toggle hint (dataSources path) must remain
  expect(prompt).toContain("请参考网络搜索结果来补充最新信息");
});

// ── updateWriterDoc forwards force_snapshot (Task W11) ────────────────────

test("updateWriterDoc forwards force_snapshot in the PUT body when set", async () => {
  mockFetch.mockResolvedValueOnce(
    asRes({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve({
          id: "doc_1",
          user_id: "default",
          title: "Test",
          content_json: "# new",
          doc_type: "general",
        }),
    }),
  );

  await updateWriterDoc("doc_1", {
    content_json: "# new content",
    force_snapshot: true,
  });

  const url = mockFetch.mock.calls[0]![0] as string;
  const options = mockFetch.mock.calls[0]![1] as Record<string, unknown>;
  expect(url).toBe("http://localhost:8001/api/writer/docs/doc_1");
  expect(options.method).toBe("PUT");
  expect(JSON.parse(options.body as string)).toEqual({
    content_json: "# new content",
    force_snapshot: true,
  });
});

test("updateWriterDoc omits force_snapshot when not provided", async () => {
  mockFetch.mockResolvedValueOnce(
    asRes({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve({
          id: "doc_2",
          user_id: "default",
          title: "Test",
          content_json: "{}",
          doc_type: "general",
        }),
    }),
  );

  await updateWriterDoc("doc_2", { content_json: "just text" });

  const options = mockFetch.mock.calls[0]![1] as Record<string, unknown>;
  const body = JSON.parse(options.body as string) as Record<string, unknown>;
  expect(body.content_json).toBe("just text");
  expect(body).not.toHaveProperty("force_snapshot");
});
