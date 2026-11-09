/**
 * A1: the console page must own a scroll container.
 *
 * The workspace shell is deliberately non-scrolling — `WorkspaceContent`
 * mounts `SidebarProvider` with `h-screen overflow-hidden`, and `SidebarInset`
 * is `flex-1 flex-col` inside it. So a page whose root only says
 * `flex flex-col` renders its overflow *outside* the clipped viewport: the
 * runs table's last rows and the pagination controls become unreachable, with
 * no scrollbar anywhere to hint that content was lost.
 *
 * Every sibling page solves this the same way, by scoping the scroll to the
 * page root rather than touching the shared shell (see
 * `app/workspace/chats/page.tsx` and the rationale comment in
 * `app/workspace/video-gen/[taskId]/storyboard/page.tsx`, which notes that
 * `h-full` resolves to viewport height precisely because of the `h-screen`
 * provider above it). This test locks the console into that convention.
 *
 * Only the network layer is stubbed; the real `ConsolePage` and its real
 * children render, so the assertion is against the DOM actually shipped.
 * No @testing-library/react in this repo: uses the createRoot + act +
 * happy-dom harness established by `console-admin-gating.test.tsx`.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ConsolePage } from "@/components/workspace/console/console-page";
import { AuthProvider } from "@/core/auth/AuthProvider";
import type { ConsoleScope } from "@/core/console/types";
import { I18nProvider } from "@/core/i18n/context";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), back: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/workspace/console",
}));

const scope: ConsoleScope = {
  is_admin: false,
  all_users: false,
  user_id: "u-1",
};

/**
 * Stub the transport, not the components. Returning a populated payload (rather
 * than empty/loading states) is deliberate: the bug this guards against only
 * manifests when the page is tall, so the tree under test must be the fully
 * rendered one — stat cards, chart, breakdown and a run table all present.
 */
vi.mock("@/core/console/api", () => ({
  ConsoleApiError: class extends Error {},
  fetchConsoleStats: vi.fn(async () => ({
    total_runs: 42,
    active_runs: 1,
    failed_runs: 2,
    total_tokens: 1_500_000,
    total_cost: 12.5,
    currency: "CNY",
    over_threshold_runs: 1,
    scope,
  })),
  fetchConsoleUsage: vi.fn(async () => ({
    days: [
      {
        date: "2026-08-03",
        total_tokens: 1000,
        input_tokens: 600,
        output_tokens: 400,
        runs: 3,
        cost: 0.5,
      },
    ],
    by_model: {
      "claude-sonnet-5-5": {
        tokens: 1000,
        runs: 3,
        cost: 0.5,
        input_tokens: 600,
        output_tokens: 400,
        cache_read_tokens: 0,
      },
    },
    total_tokens: 1000,
    total_runs: 3,
    total_cost: 0.5,
    currency: "CNY",
    subagent_tokens: 100,
    scope,
  })),
  fetchConsoleRuns: vi.fn(async () => ({
    runs: [
      {
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
        total_tokens: 256_517,
        subagent_tokens: 1000,
        message_count: 12,
        cost: 1.5,
        error: null,
        stop_reason: null,
        over_threshold: false,
      },
    ],
    has_more: true,
    scope,
  })),
  fetchConsoleUsers: vi.fn(async () => ({ users: [] })),
  fetchConsoleLlmUsage: vi.fn(async () => ({
    domains: [],
    total_cost: null,
    currency: null,
    scope,
  })),
  fetchConsoleExternalCalls: vi.fn(async () => ({
    total_calls: 0,
    by_provider: [],
    scope,
  })),
  fetchModelPricing: vi.fn(async () => ({ pricing: {} })),
  putModelPricing: vi.fn(),
  fetchMediaPricing: vi.fn(async () => ({ pricing: [] })),
  putMediaPricing: vi.fn(),
}));

vi.mock("@/core/models/hooks", () => {
  const stableResult = {
    models: [],
    tokenUsageEnabled: false,
    isLoading: false,
    error: null,
  };
  return { useModels: () => stableResult };
});

describe("A1 console page: scroll container", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    // AuthProvider bootstraps the csrf cookie on mount; keep it off the network.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("{}", { status: 200 })),
    );
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
    vi.unstubAllGlobals();
  });

  /** Renders the real page and returns its root element. */
  const renderPage = async (): Promise<HTMLElement> => {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <I18nProvider initialLocale="zh-CN">
            <AuthProvider
              initialUser={{
                id: "u-1",
                email: "a@example.com",
                system_role: "user",
                needs_setup: false,
              }}
            >
              <ConsolePage />
            </AuthProvider>
          </I18nProvider>
        </QueryClientProvider>,
      );
    });

    // Let the stubbed queries resolve, otherwise the table is still a skeleton
    // and the "did it really render" assertion below would be testing nothing.
    // Needs a real macrotask tick: react-query's state updates do not settle on
    // microtask flushes alone.
    for (let i = 0; i < 5; i++) {
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
    }

    const el = container.firstElementChild as HTMLElement | null;
    if (!el) throw new Error("ConsolePage rendered nothing");
    // Guard against asserting on the wrong node if the tree ever gains a
    // wrapper: the page root is the element that owns the title header.
    expect(
      el.querySelector("header"),
      "expected the first child to be the page root (the element holding <header>)",
    ).not.toBeNull();
    return el;
  };

  it("scrolls its own overflow instead of spilling out of the clipped shell", async () => {
    const pageRoot = await renderPage();

    expect(
      [...pageRoot.classList],
      `console page root must scroll vertically; got "${pageRoot.className}"`,
    ).toContain("overflow-y-auto");
  });

  it("is bounded to the viewport, so overflow-y-auto has something to clip", async () => {
    // Without a height bound the container grows to fit its content and
    // `overflow-y-auto` never engages — the two classes only work as a pair.
    const pageRoot = await renderPage();
    const classes = [...pageRoot.classList];

    expect(
      classes.some((c) => c === "h-full" || c === "size-full"),
      `console page root needs a viewport height bound; got "${pageRoot.className}"`,
    ).toBe(true);
  });

  it("renders the full page body, so the scroll container is the outermost node", async () => {
    // Sanity: proves the component really rendered (a broken mock or an early
    // error return would otherwise let the class assertions pass vacuously).
    const pageRoot = await renderPage();

    expect(pageRoot.querySelector("table")).not.toBeNull();
    expect(container.querySelector('[data-testid="console-error"]')).toBeNull();
  });
});
