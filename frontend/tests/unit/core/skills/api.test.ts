import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://backend",
}));

import { enableSkill } from "@/core/skills/api";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("enableSkill（DEF-2：非 2xx 必须 reject）", () => {
  it("rejects on 500 with the server detail", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "Failed to update skill" }), {
        status: 500,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(enableSkill("writer", false)).rejects.toThrow(
      "Failed to update skill",
    );
  });

  it("rejects on unparseable body with the fallback + status", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("internal server error", { status: 500 }),
    );

    await expect(enableSkill("writer", false)).rejects.toThrow(
      "技能开关保存失败 (HTTP 500)",
    );
  });

  it("returns the parsed body on success", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ name: "writer", enabled: false }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const result = await enableSkill("writer", false);

    expect(result).toEqual({ name: "writer", enabled: false });
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("http://backend/api/skills/writer");
    expect(init?.method).toBe("PUT");
    expect(JSON.parse(init?.body as string)).toEqual({ enabled: false });
  });
});
