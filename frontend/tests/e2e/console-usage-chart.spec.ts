/**
 * E2E test for the console usage chart (P0-1: CSS percentage-height bug).
 *
 * The chart's bar wrapper was missing `h-full`, so `height: X%` on the bar
 * resolved against an `auto`-height parent → 0px. This can only be caught by
 * a real layout engine (Chromium), not happy-dom (which has no layout engine
 * and always reports 0 for offsetHeight/getBoundingClientRect).
 *
 * Mock endpoints (7 console + admin — all must be covered because
 * DEER_FLOW_AUTH_DISABLED=1 returns a synthetic admin user whose queries
 * fire on page load):
 *   1. GET /api/console/stats
 *   2. GET /api/console/usage
 *   3. GET /api/console/runs
 *   4. GET /api/console/llm-usage
 *   5. GET /api/console/external-calls
 *   6. GET /api/console/users       (admin-only, auto-fires)
 *   7. GET /api/admin/model-pricing  (admin-only, auto-fires)
 * Shared endpoints (/api/models, /api/langgraph/*) are handled by
 * mockLangGraphAPI from tests/e2e/utils/mock-api.ts.
 *
 * Run:  DEER_FLOW_AUTH_DISABLED=1 npx playwright test tests/e2e/console-usage-chart.spec.ts
 */

import { expect, test, type Page } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

// ── Mock data ──────────────────────────────────────────────────────────

const scope = {
  is_admin: true,
  all_users: true,
  user_id: null,
};

/** Build 14 days of usage data with a known peak and a zero day. */
function buildUsageDays() {
  const today = new Date();
  const days: Array<{
    date: string;
    total_tokens: number;
    input_tokens: number;
    output_tokens: number;
    runs: number;
    cost: number | null;
  }> = [];
  for (let i = 13; i >= 0; i--) {
    const d = new Date(today);
    d.setDate(d.getDate() - i);
    const dateStr = d.toISOString().slice(0, 10);

    let tokens: number;
    if (i === 0) {
      // Day 14 (last): zero tokens — bar must be 0px.
      tokens = 0;
    } else if (i === 6) {
      // Day 7: peak — bar must be ~160px.
      tokens = 1_000_000;
    } else if (i === 12) {
      // Day 1: small but non-zero — 2% floor rule → ≥ 3px.
      tokens = 5_000;
    } else {
      tokens = 100_000 + i * 10_000;
    }

    days.push({
      date: dateStr,
      total_tokens: tokens,
      input_tokens: Math.floor(tokens * 0.6),
      output_tokens: Math.floor(tokens * 0.4),
      runs: tokens > 0 ? 1 : 0,
      cost: tokens > 0 ? 0.5 : null,
    });
  }
  return days;
}

// ── Mock helper ─────────────────────────────────────────────────────────

function mockConsoleAPI(page: Page) {
  // Shared endpoints (models, langgraph threads, etc.)
  mockLangGraphAPI(page);

  const usageDays = buildUsageDays();
  const peakDay = usageDays.find((d) => d.total_tokens === 1_000_000)!;
  const zeroDay = usageDays.find((d) => d.total_tokens === 0)!;
  const smallDay = usageDays.find((d) => d.total_tokens === 5_000)!;

  // 1. /api/console/stats
  void page.route("**/api/console/stats**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        total_runs: 42,
        active_runs: 1,
        failed_runs: 2,
        total_tokens: 1_500_000,
        total_cost: 12.5,
        currency: "CNY",
        over_threshold_runs: 1,
        scope,
      }),
    }),
  );

  // 2. /api/console/usage
  void page.route("**/api/console/usage**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        days: usageDays,
        by_model: {
          "test-model": {
            tokens: 1_500_000,
            runs: 13,
            cost: 12.5,
            input_tokens: 900_000,
            output_tokens: 600_000,
            cache_read_tokens: 0,
          },
        },
        total_tokens: usageDays.reduce((s, d) => s + d.total_tokens, 0),
        total_runs: 13,
        total_cost: 6.5,
        media_cost_by_provider: {},
        currency: "CNY",
        subagent_tokens: 0,
        scope,
      }),
    }),
  );

  // 3. /api/console/runs
  void page.route("**/api/console/runs**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ runs: [], has_more: false, scope }),
    }),
  );

  // 4. /api/console/llm-usage
  void page.route("**/api/console/llm-usage**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        domains: [],
        total_cost: null,
        currency: null,
        scope,
      }),
    }),
  );

  // 5. /api/console/external-calls
  void page.route("**/api/console/external-calls**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        total_calls: 0,
        by_provider: [],
        scope,
      }),
    }),
  );

  // 6. /api/console/users (admin-only, auto-fires)
  void page.route("**/api/console/users**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ users: [] }),
    }),
  );

  // 7. /api/admin/model-pricing (admin-only, auto-fires)
  void page.route("**/api/admin/model-pricing**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ pricing: {} }),
    }),
  );

  return { peakDay, zeroDay, smallDay };
}

// ── Tests ───────────────────────────────────────────────────────────────

test.describe("Console usage chart - bar visibility (P0-1)", () => {
  test("peak day bar has full height (~160px)", async ({ page }) => {
    const { peakDay } = mockConsoleAPI(page);
    await page.goto("/workspace/console");
    await page.waitForLoadState("networkidle");

    const bar = page.getByTestId(`usage-bar-${peakDay.date}`);
    await expect(bar).toBeVisible({ timeout: 10_000 });

    const rect = await bar.boundingBox();
    expect(rect).not.toBeNull();
    // h-40 = 160px; peak bar at 100% should be ~160px (±2 for sub-pixel rounding).
    expect(rect!.height).toBeGreaterThan(158);
    expect(rect!.height).toBeLessThan(162);
  });

  test("small non-zero day bar respects 2% floor (≥ 3px)", async ({ page }) => {
    const { smallDay } = mockConsoleAPI(page);
    await page.goto("/workspace/console");
    await page.waitForLoadState("networkidle");

    const bar = page.getByTestId(`usage-bar-${smallDay.date}`);
    await expect(bar).toBeVisible({ timeout: 10_000 });

    const rect = await bar.boundingBox();
    expect(rect).not.toBeNull();
    // 2% of 160px = 3.2px → must be ≥ 3px (non-zero, visible).
    expect(rect!.height).toBeGreaterThanOrEqual(3);
  });

  test("zero-token day bar has height 0", async ({ page }) => {
    const { zeroDay } = mockConsoleAPI(page);
    await page.goto("/workspace/console");
    await page.waitForLoadState("networkidle");

    const bar = page.getByTestId(`usage-bar-${zeroDay.date}`);
    // The bar element exists but its height is 0.
    await expect(bar).toHaveCount(1);

    const rect = await bar.boundingBox();
    expect(rect).not.toBeNull();
    expect(rect!.height).toBe(0);
  });
});
