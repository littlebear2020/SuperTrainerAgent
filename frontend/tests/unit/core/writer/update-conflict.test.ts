import { afterEach, describe, expect, it, vi } from "vitest";

import { updateWriterDoc, WriterDocConflictError } from "@/core/writer/api";

describe("updateWriterDoc conflict (B2-T2)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("passes base_version through and throws WriterDocConflictError on 409", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({ detail: "conflict" }),
      text: async () => "conflict",
    });
    vi.stubGlobal("fetch", fetchMock);
    await expect(
      updateWriterDoc("doc1", {
        content_json: "# a",
        base_version: 3,
      }),
    ).rejects.toBeInstanceOf(WriterDocConflictError);
    const body = JSON.parse(fetchMock.mock.calls[0]![1]!.body as string);
    expect(body.base_version).toBe(3);
  });
});
