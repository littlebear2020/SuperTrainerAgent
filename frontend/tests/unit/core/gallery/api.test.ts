import { beforeEach, expect, test, vi } from "vitest";

vi.mock("@/core/api/fetcher", () => ({
  fetch: vi.fn(),
}));

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://localhost:8001",
}));

import { fetch } from "@/core/api/fetcher";
import { deleteCase, updateCase } from "@/core/gallery/api";

const fetchMock = vi.mocked(fetch);

beforeEach(() => {
  fetchMock.mockReset();
});

test("updateCase sends PATCH with JSON body to case endpoint", async () => {
  fetchMock.mockResolvedValueOnce(
    new Response(JSON.stringify({ id: "c1" }), { status: 200 }),
  );
  await updateCase("c1", { title: "新标题" });
  const [url, init] = fetchMock.mock.calls[0]!;
  expect(typeof url).toBe("string");
  expect(url as string).toContain("/api/gallery/cases/c1");
  expect(init?.method).toBe("PATCH");
  const body = init?.body;
  expect(typeof body).toBe("string");
  expect(JSON.parse(body as string)).toEqual({ title: "新标题" });
});

test("deleteCase sends DELETE and parses response", async () => {
  fetchMock.mockResolvedValueOnce(
    new Response(JSON.stringify({ deleted: true }), { status: 200 }),
  );
  await expect(deleteCase("c1")).resolves.toEqual({ deleted: true });
  expect(fetchMock.mock.calls[0]?.[1]?.method).toBe("DELETE");
});

test("updateCase surfaces backend error detail (never silent)", async () => {
  fetchMock.mockResolvedValueOnce(
    new Response(
      JSON.stringify({ detail: "Only the author can modify this case" }),
      {
        status: 403,
      },
    ),
  );
  await expect(updateCase("c1", { title: "x" })).rejects.toThrow(
    /Only the author/,
  );
});

test("updateCase forwards thumbnail in PATCH body", async () => {
  fetchMock.mockResolvedValueOnce(
    new Response(JSON.stringify({ id: "c1" }), { status: 200 }),
  );
  await updateCase("c1", { thumbnail: "/api/gallery/thumbnails/x.png" });
  const body = fetchMock.mock.calls[0]?.[1]?.body;
  expect(typeof body).toBe("string");
  expect(JSON.parse(body as string)).toEqual({
    thumbnail: "/api/gallery/thumbnails/x.png",
  });
});
