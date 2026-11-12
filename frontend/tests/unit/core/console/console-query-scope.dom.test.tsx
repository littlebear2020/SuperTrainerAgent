import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchConsoleRuns, fetchConsoleStats } from "@/core/console/api";
import {
  useConsoleExternalCalls,
  useConsoleLlmUsage,
  useConsoleRuns,
  useConsoleStats,
  useConsoleUsage,
  useConsoleUsers,
  useMediaPricing,
  useModelPricing,
} from "@/core/console/hooks";

const h = vi.hoisted(() => ({
  isAdmin: true,
  userId: "user-1",
}));

vi.mock("@/core/auth/AuthProvider", () => ({
  useAuth: () => ({
    isAdmin: h.isAdmin,
    user: { id: h.userId },
  }),
}));

vi.mock("@/core/console/api", async () => {
  const actual =
    await vi.importActual<Record<string, unknown>>("@/core/console/api");
  return {
    ...actual,
    fetchConsoleExternalCalls: vi.fn(() => Promise.resolve({})),
    fetchConsoleLlmUsage: vi.fn(() => Promise.resolve({})),
    fetchConsoleRuns: vi.fn(() => Promise.resolve({})),
    fetchConsoleStats: vi.fn(() => Promise.resolve({})),
    fetchConsoleUsage: vi.fn(() => Promise.resolve({})),
    fetchConsoleUsers: vi.fn(() => Promise.resolve({})),
    fetchModelPricing: vi.fn(() => Promise.resolve({})),
    fetchMediaPricing: vi.fn(() => Promise.resolve({})),
  };
});

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const RUN_OPTIONS = {
  limit: 20,
  offset: 0,
  sortBy: "created_at",
  sortDir: "desc",
} as const;

function AllConsoleQueries() {
  useConsoleStats(undefined, 7);
  useConsoleRuns(RUN_OPTIONS);
  useConsoleUsage({ days: 7 });
  useConsoleUsers(true);
  useConsoleLlmUsage({ days: 7 });
  useConsoleExternalCalls({ days: 7 });
  useModelPricing(true);
  useMediaPricing(true);
  return null;
}

function ScopeTransitionProbe() {
  const stats = useConsoleStats(undefined, 7);
  const runs = useConsoleRuns(RUN_OPTIONS);
  return (
    <>
      <output data-testid="stats-value">
        {stats.data?.total_runs ?? "empty"}
      </output>
      <output data-testid="run-value">
        {runs.data?.runs[0]?.run_id ?? "empty"}
      </output>
    </>
  );
}

describe("console query scope isolation", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    h.isAdmin = true;
    h.userId = "user-1";
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    queryClient.clear();
    container.remove();
    vi.clearAllMocks();
  });

  async function render(children: React.ReactNode) {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          {children}
        </QueryClientProvider>,
      );
      await Promise.resolve();
    });
  }

  it("keys every console query by admin scope or the real regular-user id", async () => {
    await render(<AllConsoleQueries />);
    const adminKeys = queryClient
      .getQueryCache()
      .getAll()
      .map((query) => query.queryKey);
    expect(adminKeys).toHaveLength(8);
    for (const key of adminKeys) {
      expect(key[0]).toBe("console");
      expect(key[1]).toBe("admin");
    }

    h.isAdmin = false;
    await render(<AllConsoleQueries />);
    const userKeys = queryClient
      .getQueryCache()
      .getAll()
      .map((query) => query.queryKey)
      .filter((key) => key[1] === "user:user-1");
    expect(userKeys).toHaveLength(8);
  });

  it("does not expose cached admin data or skip refetch after admin becomes user", async () => {
    let resolveUserStats: ((value: unknown) => void) | undefined;
    let resolveUserRuns: ((value: unknown) => void) | undefined;
    const userStats = new Promise((resolve) => {
      resolveUserStats = resolve;
    });
    const userRuns = new Promise((resolve) => {
      resolveUserRuns = resolve;
    });
    vi.mocked(fetchConsoleStats).mockImplementation(() =>
      h.isAdmin
        ? Promise.resolve({ total_runs: 99 } as never)
        : (userStats as never),
    );
    vi.mocked(fetchConsoleRuns).mockImplementation(() =>
      h.isAdmin
        ? Promise.resolve({
            runs: [{ run_id: "admin-run" }],
            has_more: false,
          } as never)
        : (userRuns as never),
    );

    await render(<ScopeTransitionProbe />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(
          container.querySelector("[data-testid='stats-value']")?.textContent,
        ).toBe("99");
        expect(
          container.querySelector("[data-testid='run-value']")?.textContent,
        ).toBe("admin-run");
      });
    });

    h.isAdmin = false;
    await render(<ScopeTransitionProbe />);

    expect(
      container.querySelector("[data-testid='stats-value']")?.textContent,
    ).toBe("empty");
    expect(
      container.querySelector("[data-testid='run-value']")?.textContent,
    ).toBe("empty");
    expect(fetchConsoleStats).toHaveBeenCalledTimes(2);
    expect(fetchConsoleRuns).toHaveBeenCalledTimes(2);
    expect(
      queryClient
        .getQueryCache()
        .getAll()
        .some((query) => query.queryKey[1] === "user:user-1"),
    ).toBe(true);

    await act(async () => {
      resolveUserStats?.({ total_runs: 1 });
      resolveUserRuns?.({ runs: [{ run_id: "user-run" }], has_more: false });
      await vi.waitFor(() => {
        expect(
          container.querySelector("[data-testid='stats-value']")?.textContent,
        ).toBe("1");
        expect(
          container.querySelector("[data-testid='run-value']")?.textContent,
        ).toBe("user-run");
      });
    });
  });
});
