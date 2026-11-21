import { expect, it, vi } from "vitest";

import { useModels } from "@/core/models/hooks";

vi.mock("@tanstack/react-query", () => ({
  useQuery: () => ({
    data: {
      models: [],
      mode_defaults: { standard: "claude" },
      token_usage: { enabled: false },
    },
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

vi.mock("@/core/models/api", () => ({ loadModels: vi.fn() }));

it("useModels exposes mode defaults", () => {
  const result = useModels();

  expect(result.modeDefaults).toEqual({ standard: "claude" });
});
