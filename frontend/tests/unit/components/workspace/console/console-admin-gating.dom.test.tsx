/**
 * T21: the console's admin boundary, on the UI side.
 *
 * The gateway is the authority here — a non-admin passing another user's
 * `user_id` gets a 403, proven in `backend/tests/test_console_router.py`.
 * These tests cover the half that lives in the browser:
 *
 * 1. A regular user gets **no** user-switch affordance at all. Not a disabled
 *    one — the plan is explicit that "普通用户不渲染该入口", and a greyed-out
 *    control would still advertise that platform-wide data exists.
 * 2. A regular user's run table has no owner column (it would only ever echo
 *    themselves back).
 * 3. An admin gets both.
 * 4. The table renders the real-world token range compactly rather than
 *    letting 256,517,593 blow the column apart.
 *
 * No @testing-library/react in this repo: uses the createRoot + act +
 * happy-dom harness established by
 * tests/unit/components/workspace/video-gen/bgm-picker-clickable.test.tsx.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ConsoleRunsTable } from "@/components/workspace/console/console-runs-table";
import {
  ALL_USERS_VALUE,
  ConsoleUserPicker,
} from "@/components/workspace/console/console-user-picker";
import type { ConsoleRun, ConsoleRunsPage } from "@/core/console/types";
import { I18nProvider } from "@/core/i18n/context";
import { translations } from "@/core/i18n/translations";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

/**
 * Real providers, not stubs: a mocked `useI18n` would let a missing
 * translation key pass as a hit, and adding the console block to
 * locales/{types,zh-CN,en-US} is part of this change.
 *
 * Assertions below compare against the resolved translation table rather than
 * hard-coded Chinese, because `useI18n`'s mount effect re-detects the locale
 * from the environment — pinning a language would make these tests depend on
 * happy-dom's navigator.language.
 */
function Wrap({
  children,
  queryClient,
}: {
  children: ReactNode;
  queryClient: QueryClient;
}) {
  return (
    <QueryClientProvider client={queryClient}>
      <I18nProvider initialLocale="zh-CN">{children}</I18nProvider>
    </QueryClientProvider>
  );
}

// The picker fetches the user list; stub the network, not the component.
vi.mock("@/core/console/api", () => ({
  ConsoleApiError: class extends Error {},
  fetchConsoleUsers: vi.fn(async () => ({
    users: [
      {
        user_id: "u-1",
        email: "a@example.com",
        system_role: "user",
        total_runs: 3,
        total_tokens: 1_500_000,
      },
    ],
  })),
  fetchConsoleStats: vi.fn(),
  fetchConsoleRuns: vi.fn(),
  fetchConsoleUsage: vi.fn(),
}));

/**
 * Every locale's wording for one console key.
 *
 * Reading them off the real translation tables (rather than hard-coding a
 * string) means a renamed or deleted key breaks compilation instead of
 * silently turning an assertion into a no-op. Tests then assert "some locale's
 * label is present" / "no locale's label is present", which holds regardless
 * of which locale `useI18n` detects in happy-dom.
 */
function allLabels(key: "colUser" | "noRuns"): string[] {
  const labels = Object.values(translations).map((entry) => entry.console[key]);
  if (labels.length === 0) throw new Error("no translations loaded");
  return labels;
}

function makeRun(overrides: Partial<ConsoleRun> = {}): ConsoleRun {
  return {
    run_id: "run-1",
    thread_id: "thread-1",
    thread_title: "做一个视频",
    assistant_id: "lead",
    user_id: "u-1",
    user_email: "a@example.com",
    status: "success",
    model_name: "claude-sonnet-5-5",
    created_at: "2026-08-03T10:00:00Z",
    updated_at: "2026-08-03T11:00:00Z",
    duration_seconds: 3600,
    total_tokens: 256_517_593,
    subagent_tokens: 1_000_000,
    message_count: 12,
    cost: 42.5,
    error: null,
    stop_reason: null,
    over_threshold: true,
    ...overrides,
  };
}

function makePage(runs: ConsoleRun[]): ConsoleRunsPage {
  return {
    runs,
    has_more: false,
    scope: { is_admin: false, all_users: false, user_id: "u-1" },
  };
}

describe("T21 console: admin-only affordances", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    queryClient.clear();
  });

  const renderPicker = async (enabled: boolean) => {
    await act(async () => {
      root.render(
        <Wrap queryClient={queryClient}>
          <ConsoleUserPicker
            enabled={enabled}
            value={ALL_USERS_VALUE}
            onChange={() => undefined}
          />
        </Wrap>,
      );
    });
  };

  it("renders NO user picker for a regular user — not even a disabled one", async () => {
    await renderPicker(false);

    expect(
      container.querySelector('[data-testid="console-user-picker"]'),
      "a regular user must have no cross-user drill-down affordance at all",
    ).toBeNull();
    // Nothing left behind that hints at the platform-wide view.
    expect(container.textContent ?? "").toBe("");
    expect(container.querySelector("button")).toBeNull();
  });

  it("renders the user picker for an admin", async () => {
    await renderPicker(true);

    expect(
      container.querySelector('[data-testid="console-user-picker"]'),
      "an admin must be able to drill into a single user",
    ).not.toBeNull();
    expect(container.querySelector("button")).not.toBeNull();
  });

  const renderTable = async (isAdmin: boolean, page: ConsoleRunsPage) => {
    await act(async () => {
      root.render(
        <Wrap queryClient={queryClient}>
          <ConsoleRunsTable
            page={page}
            isLoading={false}
            isAdmin={isAdmin}
            sortBy="created_at"
            sortDir="desc"
            onSort={() => undefined}
            currency="CNY"
          />
        </Wrap>,
      );
    });
  };

  it("hides the owner column from a regular user", async () => {
    await renderTable(false, makePage([makeRun()]));

    const headers = [...container.querySelectorAll("thead th")].map((th) =>
      (th.textContent ?? "").trim(),
    );
    for (const label of allLabels("colUser")) {
      expect(
        headers,
        `owner column must be absent (checked "${label}")`,
      ).not.toContain(label);
    }
    // And the owner's email is nowhere in the rendered output.
    expect(container.textContent).not.toContain("a@example.com");
  });

  it("shows the owner column to an admin", async () => {
    await renderTable(true, makePage([makeRun()]));

    const headers = [...container.querySelectorAll("thead th")].map((th) =>
      (th.textContent ?? "").trim(),
    );
    expect(
      allLabels("colUser").some((label) => headers.includes(label)),
      `expected an owner column header, got ${JSON.stringify(headers)}`,
    ).toBe(true);
    expect(container.textContent).toContain("a@example.com");
  });

  it("keeps the column count consistent with the header row", async () => {
    // A mismatch here is how "shifted by one column" bugs get shipped.
    await renderTable(true, makePage([makeRun()]));
    const headerCount = container.querySelectorAll("thead th").length;
    const cellCount = container.querySelectorAll("tbody tr td").length;
    expect(cellCount).toBe(headerCount);
  });
});

describe("T21 console: number rendering against real data ranges", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    queryClient.clear();
  });

  const render = async (page: ConsoleRunsPage) => {
    await act(async () => {
      root.render(
        <Wrap queryClient={queryClient}>
          <ConsoleRunsTable
            page={page}
            isLoading={false}
            isAdmin={false}
            sortBy="created_at"
            sortDir="desc"
            onSort={() => undefined}
            currency="CNY"
          />
        </Wrap>,
      );
    });
  };

  it("compacts the 256M-token outlier but keeps the exact value reachable", async () => {
    await render(makePage([makeRun({ total_tokens: 256_517_593 })]));

    expect(container.textContent).toContain("256.5M");
    // Raw grouped integer would be 11 chars wide in the column.
    expect(container.textContent).not.toContain("256,517,593");
    // ...but it is still available on hover, so precision is not lost.
    const titles = [...container.querySelectorAll("[title]")].map((el) =>
      el.getAttribute("title"),
    );
    expect(titles.some((t) => t?.includes("256,517,593"))).toBe(true);
  });

  it("flags an over-threshold run (T20 surfacing)", async () => {
    await render(makePage([makeRun({ over_threshold: true })]));
    expect(
      container.querySelector('[data-testid="over-threshold-run-1"]'),
    ).not.toBeNull();
  });

  it("does not flag a run below the threshold", async () => {
    await render(makePage([makeRun({ over_threshold: false })]));
    expect(
      container.querySelector('[data-testid="over-threshold-run-1"]'),
    ).toBeNull();
  });

  it("shows a dash for unpriced runs rather than a zero cost", async () => {
    await render(makePage([makeRun({ cost: null })]));
    // "¥0.00" here would claim the run was free when we simply do not know.
    expect(container.textContent).not.toContain("¥0.00");
    expect(container.textContent).toContain("—");
  });

  it("renders an empty state instead of an empty table body", async () => {
    await render(makePage([]));
    expect(container.querySelectorAll("tbody tr").length).toBe(1);
    const rendered = container.textContent ?? "";
    expect(
      allLabels("noRuns").some((label) => rendered.includes(label)),
      `expected an empty-state message, got "${rendered}"`,
    ).toBe(true);
  });

  it("truncates the failure reason but keeps it in the tooltip", async () => {
    const longError = "boom ".repeat(60);
    await render(makePage([makeRun({ status: "error", error: longError })]));
    const titles = [...container.querySelectorAll("[title]")].map((el) =>
      el.getAttribute("title"),
    );
    expect(titles).toContain(longError);
  });
});
