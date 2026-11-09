/**
 * A1: pricing data layer tests.
 *
 * Mocks global fetch to assert that the api functions call the right
 * endpoints with the right method and payload structure.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchModelPricing, putModelPricing } from "@/core/console/api";
import type { ModelPricingResponse } from "@/core/console/types";

const PRICING_RESPONSE: ModelPricingResponse = {
  pricing: {
    "model-a": {
      currency: "CNY",
      input_per_million: 8,
      output_per_million: 32,
      input_cache_hit_per_million: 0.8,
      updated_at: "2026-08-08T00:00:00Z",
      updated_by: "admin-1",
      source: "db",
    },
    "model-b": {
      currency: "CNY",
      input_per_million: 5,
      output_per_million: 20,
      input_cache_hit_per_million: null,
      updated_at: null,
      updated_by: "",
      source: "yaml",
    },
  },
};

function mockFetchOk(body: unknown) {
  return vi.fn().mockResolvedValue({
    ok: true,
    json: () => Promise.resolve(body),
  } as Response);
}

function mockFetchError(status: number, detail: string) {
  return vi.fn().mockResolvedValue({
    ok: false,
    statusText: "Error",
    status,
    json: () => Promise.resolve({ detail }),
  } as Response);
}

describe("fetchModelPricing", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });
  afterEach(() => {
    vi.restoreAllMocks();
    // restoreAllMocks does NOT undo stubGlobal — without this the stubbed
    // fetch outlives each test.
    vi.unstubAllGlobals();
  });

  it("calls GET /api/admin/model-pricing and returns parsed response", async () => {
    const mockFetch = mockFetchOk(PRICING_RESPONSE);
    vi.stubGlobal("fetch", mockFetch);

    const result = await fetchModelPricing();
    expect(result).toEqual(PRICING_RESPONSE);
    expect(mockFetch).toHaveBeenCalledTimes(1);
    const url = mockFetch.mock.calls[0]![0] as string;
    expect(url).toContain("/api/admin/model-pricing");
  });

  it("throws ConsoleApiError on non-2xx with detail extracted from body", async () => {
    vi.stubGlobal("fetch", mockFetchError(503, "Pricing requires SQL backend"));
    await expect(fetchModelPricing()).rejects.toMatchObject({
      name: "ConsoleApiError",
      status: 503,
      message: "Pricing requires SQL backend",
    });
  });
});

describe("putModelPricing", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });
  afterEach(() => {
    vi.restoreAllMocks();
    // restoreAllMocks does NOT undo stubGlobal — without this the stubbed
    // fetch outlives each test.
    vi.unstubAllGlobals();
  });

  it("calls PUT /api/admin/model-pricing with JSON body", async () => {
    const mockFetch = mockFetchOk(PRICING_RESPONSE);
    vi.stubGlobal("fetch", mockFetch);

    const payload = {
      pricing: {
        "model-a": {
          currency: "CNY",
          input_per_million: 10,
          output_per_million: 40,
          input_cache_hit_per_million: null,
        },
      },
    };

    const result = await putModelPricing(payload);
    expect(result).toEqual(PRICING_RESPONSE);

    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [url, init] = mockFetch.mock.calls[0]!;
    expect(url).toContain("/api/admin/model-pricing");
    expect(init?.method).toBe("PUT");
    expect(init?.headers).toMatchObject({ "Content-Type": "application/json" });

    const sentBody = JSON.parse(init?.body as string);
    // Full payload structure (all models present)
    expect(sentBody).toEqual(payload);
  });

  it("includes all models in the payload (full-table replace)", async () => {
    const mockFetch = mockFetchOk(PRICING_RESPONSE);
    vi.stubGlobal("fetch", mockFetch);

    const payload = {
      pricing: {
        a: {
          currency: "CNY",
          input_per_million: 1,
          output_per_million: 2,
          input_cache_hit_per_million: null,
        },
        b: {
          currency: "CNY",
          input_per_million: 3,
          output_per_million: 4,
          input_cache_hit_per_million: 0.5,
        },
      },
    };

    await putModelPricing(payload);
    const init = mockFetch.mock.calls[0]![1] as RequestInit;
    const sentBody = JSON.parse(init.body as string);
    expect(Object.keys(sentBody.pricing)).toEqual(["a", "b"]);
  });

  it("throws ConsoleApiError on 422 with validation detail", async () => {
    vi.stubGlobal("fetch", mockFetchError(422, "Mixed currencies"));
    await expect(putModelPricing({ pricing: {} })).rejects.toMatchObject({
      name: "ConsoleApiError",
      status: 422,
      message: "Mixed currencies",
    });
  });
});
