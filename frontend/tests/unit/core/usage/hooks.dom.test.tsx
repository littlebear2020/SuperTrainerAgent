import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchPersonalUsage } from "@/core/usage/api";
import { usePersonalUsage } from "@/core/usage/hooks";
import type { PersonalUsageResponse } from "@/core/usage/types";

const h = vi.hoisted(() => ({ userId: "user-1" }));

vi.mock("@/core/auth/AuthProvider", () => ({
  useAuth: () => ({ user: { id: h.userId }, isAdmin: false }),
}));
vi.mock("@/core/usage/api", () => ({ fetchPersonalUsage: vi.fn() }));

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function response(cost: number): PersonalUsageResponse {
  return {
    window: { days: 7, start_date: "2026-11-15", end_date: "2026-11-21" },
    summary: { cost, currency: "CNY", cost_source: "real", total_tokens: 100 },
    tasks: [],
    total_tasks: 0,
    has_more: false,
  };
}

describe("usePersonalUsage", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;
  let latest: ReturnType<typeof usePersonalUsage> | null;
  let options: Parameters<typeof usePersonalUsage>[0];
  let pending: Array<{ resolve: (value: PersonalUsageResponse) => void }>;

  function Probe() {
    latest = usePersonalUsage(options);
    return null;
  }

  async function render() {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <Probe />
        </QueryClientProvider>,
      );
      await Promise.resolve();
    });
  }

  beforeEach(() => {
    h.userId = "user-1";
    latest = null;
    options = {
      days: 7,
      capability: "all",
      offset: 0,
      limit: 20,
      tzOffsetMinutes: 480,
    };
    pending = [];
    vi.mocked(fetchPersonalUsage).mockReset();
    vi.mocked(fetchPersonalUsage).mockImplementation(
      () =>
        new Promise((resolve) => {
          pending.push({ resolve });
        }),
    );
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    queryClient.clear();
    container.remove();
  });

  it("uses the full user-scoped key and only reuses placeholders for the same user", async () => {
    await render();
    expect(fetchPersonalUsage).toHaveBeenCalledWith(options);
    await act(async () => {
      pending[0]!.resolve(response(11));
      await vi.waitFor(() => expect(latest?.data?.summary.cost).toBe(11));
    });

    await render();
    expect(fetchPersonalUsage).toHaveBeenCalledTimes(1);

    h.userId = "user-2";
    await render();
    expect(latest?.data).toBeUndefined();
    expect(fetchPersonalUsage).toHaveBeenCalledTimes(2);

    await act(async () => {
      pending[1]!.resolve(response(22));
      await vi.waitFor(() => expect(latest?.data?.summary.cost).toBe(22));
    });

    options = { ...options, days: 14 };
    await render();
    expect(latest?.data?.summary.cost).toBe(22);
    expect(fetchPersonalUsage).toHaveBeenCalledTimes(3);

    const keys = queryClient
      .getQueryCache()
      .getAll()
      .map((query) => query.queryKey);
    expect(keys).toContainEqual([
      "personal-usage",
      "user-1",
      7,
      "all",
      0,
      20,
      480,
    ]);
    expect(keys).toContainEqual([
      "personal-usage",
      "user-2",
      7,
      "all",
      0,
      20,
      480,
    ]);
    expect(keys).toContainEqual([
      "personal-usage",
      "user-2",
      14,
      "all",
      0,
      20,
      480,
    ]);

    await act(async () => {
      pending[2]!.resolve(response(33));
      await vi.waitFor(() => expect(latest?.data?.summary.cost).toBe(33));
    });
  });
});
