/**
 * Data access for the operations console.
 *
 * GET endpoints skip the CSRF header entirely — but they still go through
 * `core/api/fetcher`'s wrapper for `credentials: "include"` (the access_token
 * cookie is HttpOnly) and its shared 401 → /login redirect.
 *
 * PUT (model pricing) is the first write call in the console; it goes
 * through the same `fetch` wrapper which injects the X-CSRF-Token header
 * automatically for state-changing methods.
 */

import { fetch } from "@/core/api/fetcher";
import { readFastApiErrorDetail } from "@/core/api/read-fastapi-error";

import { localTzOffsetMinutes } from "./format";
import type {
  ConsoleExternalCallsResponse,
  ConsoleLlmUsageResponse,
  ConsoleRunSortKey,
  ConsoleRunsPage,
  ConsoleStats,
  ConsoleUsage,
  ConsoleUsersResponse,
  MediaPricingPatchRequest,
  MediaPricingReplaceRequest,
  MediaPricingResponse,
  ModelPricingResponse,
  ModelPricingUpdateRequest,
  SortDirection,
} from "./types";

/** Thrown for non-2xx responses so callers can branch on 403 specifically. */
export class ConsoleApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ConsoleApiError";
    this.status = status;
  }
}

async function getJson<T>(
  path: string,
  params: Record<string, string | number | undefined>,
): Promise<T> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") {
      query.set(key, String(value));
    }
  }
  const suffix = query.toString();
  const res = await fetch(`${path}${suffix ? `?${suffix}` : ""}`);
  if (!res.ok) {
    throw new ConsoleApiError(
      res.status,
      await readFastApiErrorDetail(res, res.statusText),
    );
  }
  return (await res.json()) as T;
}

/**
 * PUT helper with the same error contract as `getJson`.
 * The `fetch` wrapper handles CSRF and credentials.
 */
async function putJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new ConsoleApiError(
      res.status,
      await readFastApiErrorDetail(res, res.statusText),
    );
  }
  return (await res.json()) as T;
}

/**
 * `userId` is meaningful for admins only. A non-admin passing another user's
 * id gets a 403 from the gateway by design — it does not silently fall back
 * to their own data, so a rejected drill-down surfaces as an error here.
 */
export function fetchConsoleStats(
  userId?: string,
  days?: number,
): Promise<ConsoleStats> {
  // C5: absent days = all-time on the API side; the frontend's day selector
  // always passes it explicitly (default 7, A17).
  return getJson<ConsoleStats>("/api/console/stats", {
    user_id: userId,
    days,
  });
}

export function fetchConsoleRuns(options: {
  limit?: number;
  offset?: number;
  status?: string;
  sortBy?: ConsoleRunSortKey;
  sortDir?: SortDirection;
  userId?: string;
}): Promise<ConsoleRunsPage> {
  return getJson<ConsoleRunsPage>("/api/console/runs", {
    limit: options.limit ?? 20,
    offset: options.offset ?? 0,
    status: options.status,
    sort_by: options.sortBy ?? "created_at",
    sort_dir: options.sortDir ?? "desc",
    user_id: options.userId,
  });
}

export function fetchConsoleUsage(options: {
  days?: number;
  userId?: string;
}): Promise<ConsoleUsage> {
  return getJson<ConsoleUsage>("/api/console/usage", {
    days: options.days ?? 14,
    // Bucket by the viewer's local day, not UTC, so "today" means today.
    tz_offset_minutes: localTzOffsetMinutes(),
    user_id: options.userId,
  });
}

export function fetchConsoleUsers(): Promise<ConsoleUsersResponse> {
  return getJson<ConsoleUsersResponse>("/api/console/users", {});
}

// ---------------------------------------------------------------------------
// Non-conversation LLM usage (C2)
// ---------------------------------------------------------------------------

export function fetchConsoleLlmUsage(options: {
  days?: number;
  userId?: string;
}): Promise<ConsoleLlmUsageResponse> {
  return getJson<ConsoleLlmUsageResponse>("/api/console/llm-usage", {
    days: options.days ?? 14,
    user_id: options.userId,
  });
}

// F2: External API call counts

export function fetchConsoleExternalCalls(options: {
  days?: number;
  userId?: string;
}): Promise<ConsoleExternalCallsResponse> {
  return getJson<ConsoleExternalCallsResponse>("/api/console/external-calls", {
    days: options.days ?? 14,
    tz_offset_minutes: localTzOffsetMinutes(),
    user_id: options.userId,
  });
}

// ---------------------------------------------------------------------------
// Model pricing (admin-only)
// ---------------------------------------------------------------------------

export function fetchModelPricing(): Promise<ModelPricingResponse> {
  return getJson<ModelPricingResponse>("/api/admin/model-pricing", {});
}

export function putModelPricing(
  body: ModelPricingUpdateRequest,
): Promise<ModelPricingResponse> {
  return putJson<ModelPricingResponse>("/api/admin/model-pricing", body);
}

// ---------------------------------------------------------------------------
// Media pricing (admin-only, C2)
// ---------------------------------------------------------------------------

export function fetchMediaPricing(): Promise<MediaPricingResponse> {
  return getJson<MediaPricingResponse>("/api/admin/media-pricing", {});
}

export function putMediaPricing(
  body: MediaPricingReplaceRequest,
): Promise<MediaPricingResponse> {
  return putJson<MediaPricingResponse>("/api/admin/media-pricing", body);
}

/**
 * Shared helper for PATCH/DELETE with the same error contract as `putJson`.
 * The `fetch` wrapper handles CSRF and credentials.
 */
async function requestJson<T>(
  method: "PATCH" | "DELETE",
  path: string,
  body?: unknown,
): Promise<T> {
  const res = await fetch(path, {
    method,
    headers:
      body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    throw new ConsoleApiError(
      res.status,
      await readFastApiErrorDetail(res, res.statusText),
    );
  }
  return (await res.json()) as T;
}

/** C7 (A20): single-row upsert — no more whole-table resend to change one price. */
export function patchMediaPricing(
  body: MediaPricingPatchRequest,
): Promise<MediaPricingResponse> {
  return requestJson<MediaPricingResponse>(
    "PATCH",
    "/api/admin/media-pricing",
    body,
  );
}

/** C7 (A20): single-row delete by key (model_id null = provider-level row). */
export function deleteMediaPricing(params: {
  provider: string;
  mediaType: string;
  modelId?: string | null;
}): Promise<MediaPricingResponse> {
  const query = new URLSearchParams({
    provider: params.provider,
    media_type: params.mediaType,
  });
  if (params.modelId) {
    query.set("model_id", params.modelId);
  }
  return requestJson<MediaPricingResponse>(
    "DELETE",
    `/api/admin/media-pricing?${query.toString()}`,
  );
}
