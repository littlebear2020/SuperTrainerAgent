import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { loadModels } from "@/core/models/api";

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://localhost:8001",
}));

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockResolvedValue({
    ok: true,
    json: async () => ({
      models: [
        {
          id: "claude",
          name: "claude",
          model: "anthropic/claude",
          display_name: "Claude",
          modes: ["standard", "ultra"],
        },
      ],
      mode_defaults: { standard: "claude" },
      token_usage: { enabled: true },
    }),
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

it("loadModels preserves mode membership and defaults", async () => {
  const result = await loadModels();

  expect(result.models[0]?.modes).toEqual(["standard", "ultra"]);
  expect(result.mode_defaults).toEqual({ standard: "claude" });
});
