import { afterEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({ fetch: vi.fn() }));

vi.mock("@/core/api/fetcher", () => ({ fetch: h.fetch }));
vi.mock("@/core/config", () => ({ getBackendBaseURL: () => "http://backend" }));

import { fetchArtifactSummaries } from "@/core/works/api";

const summaries = [
  {
    thread_id: "thread-1",
    delivery_state: "not_delivered",
    files: [],
  },
];

afterEach(() => {
  h.fetch.mockReset();
});

describe("fetchArtifactSummaries", () => {
  it("posts the requested IDs to the works summary endpoint", async () => {
    h.fetch.mockResolvedValue(
      new Response(JSON.stringify(summaries), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(
      fetchArtifactSummaries(["thread-1", "thread-2"]),
    ).resolves.toEqual(summaries);

    expect(h.fetch).toHaveBeenCalledWith(
      "http://backend/api/works/artifact-summaries",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ thread_ids: ["thread-1", "thread-2"] }),
      },
    );
  });

  it("preserves FastAPI error details", async () => {
    h.fetch.mockResolvedValue(
      new Response(JSON.stringify({ detail: "Thread not found" }), {
        status: 404,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(fetchArtifactSummaries(["foreign"])).rejects.toThrow(
      "Thread not found",
    );
  });

  it("rejects non-OK responses instead of returning an empty summary list", async () => {
    h.fetch.mockResolvedValue(new Response("unavailable", { status: 503 }));

    await expect(fetchArtifactSummaries(["thread-1"])).rejects.toThrow(
      "Failed to load artifact summaries (HTTP 503)",
    );
  });
});
