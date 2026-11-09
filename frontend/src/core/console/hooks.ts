"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useAuth } from "@/core/auth/AuthProvider";

import {
  fetchConsoleExternalCalls,
  fetchConsoleLlmUsage,
  fetchConsoleRuns,
  fetchConsoleStats,
  fetchConsoleUsage,
  fetchConsoleUsers,
  fetchModelPricing,
  fetchMediaPricing,
  patchMediaPricing,
  deleteMediaPricing,
  putModelPricing,
  putMediaPricing,
} from "./api";
import type {
  ConsoleRunSortKey,
  MediaPricingPatchRequest,
  MediaPricingReplaceRequest,
  ModelPricingUpdateRequest,
  SortDirection,
} from "./types";

/**
 * Reporting data goes stale quickly while runs are in flight, but the queries
 * are full-table aggregations over the `runs` table — so refetch on focus
 * rather than on a timer, and treat 30s-old numbers as fresh.
 */
const STALE_TIME_MS = 30_000;

/** True when the signed-in user may query across users. */
export function useIsConsoleAdmin(): boolean {
  const { user } = useAuth();
  return user?.system_role === "admin";
}

export function useConsoleStats(userId?: string, days?: number) {
  return useQuery({
    // `days` is part of the key: without it, switching the day selector would
    // re-serve the cached all-time (or differently-windowed) numbers.
    queryKey: ["console", "stats", userId ?? null, days ?? null],
    queryFn: () => fetchConsoleStats(userId, days),
    staleTime: STALE_TIME_MS,
  });
}

export function useConsoleRuns(options: {
  limit: number;
  offset: number;
  status?: string;
  sortBy: ConsoleRunSortKey;
  sortDir: SortDirection;
  userId?: string;
}) {
  return useQuery({
    queryKey: [
      "console",
      "runs",
      options.limit,
      options.offset,
      options.status ?? null,
      options.sortBy,
      options.sortDir,
      options.userId ?? null,
    ],
    queryFn: () => fetchConsoleRuns(options),
    staleTime: STALE_TIME_MS,
    // Keep the previous page rendered while the next one loads so sorting and
    // paging don't blank the table on every click.
    placeholderData: (previous) => previous,
  });
}

export function useConsoleUsage(options: { days: number; userId?: string }) {
  return useQuery({
    queryKey: ["console", "usage", options.days, options.userId ?? null],
    queryFn: () => fetchConsoleUsage(options),
    staleTime: STALE_TIME_MS,
    placeholderData: (previous) => previous,
  });
}

/** Admin-only; the query is disabled entirely for regular users so no 403 is provoked. */
export function useConsoleUsers(enabled: boolean) {
  return useQuery({
    queryKey: ["console", "users"],
    queryFn: fetchConsoleUsers,
    staleTime: 5 * 60_000,
    enabled,
  });
}

// C2: Non-conversation LLM usage (all users, auto-scoped)
export function useConsoleLlmUsage(options: { days: number; userId?: string }) {
  return useQuery({
    queryKey: ["console", "llm-usage", options.days, options.userId ?? null],
    queryFn: () => fetchConsoleLlmUsage(options),
    staleTime: STALE_TIME_MS,
  });
}

// F2: External API call counts (all users, auto-scoped)
export function useConsoleExternalCalls(options: {
  days: number;
  userId?: string;
}) {
  return useQuery({
    queryKey: [
      "console",
      "external-calls",
      options.days,
      options.userId ?? null,
    ],
    queryFn: () => fetchConsoleExternalCalls(options),
    staleTime: STALE_TIME_MS,
  });
}

// ---------------------------------------------------------------------------
// Model pricing (admin-only)
// ---------------------------------------------------------------------------

/**
 * Admin-only pricing data. `enabled` gates the query so a non-admin never
 * provokes a 403.  Stale time matches the rest of the console.
 */
export function useModelPricing(enabled: boolean) {
  return useQuery({
    queryKey: ["console", "model-pricing"],
    queryFn: fetchModelPricing,
    staleTime: STALE_TIME_MS,
    enabled,
  });
}

/**
 * PUT mutation that replaces the entire pricing table.  On success it
 * invalidates both the pricing query and the broader `["console"]` namespace
 * because changed prices affect every spend figure on the console.
 */
export function useUpdateModelPricing() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ModelPricingUpdateRequest) => putModelPricing(body),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["console", "model-pricing"],
      });
      // Prices drive every cost figure (stats, usage, runs).  Invalidate the
      // whole console namespace so the UI refetches with new prices.
      void queryClient.invalidateQueries({ queryKey: ["console"] });
    },
  });
}

// ---------------------------------------------------------------------------
// Media pricing (admin-only, C2)
// ---------------------------------------------------------------------------

export function useMediaPricing(enabled: boolean) {
  return useQuery({
    queryKey: ["console", "media-pricing"],
    queryFn: fetchMediaPricing,
    staleTime: STALE_TIME_MS,
    enabled,
  });
}

export function useUpdateMediaPricing() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: MediaPricingReplaceRequest) => putMediaPricing(body),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["console", "media-pricing"],
      });
      void queryClient.invalidateQueries({ queryKey: ["console"] });
    },
  });
}

/** C7 (A20): single-row upsert — same invalidation contract as the PUT. */
export function usePatchMediaPricing() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: MediaPricingPatchRequest) => patchMediaPricing(body),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["console", "media-pricing"],
      });
      void queryClient.invalidateQueries({ queryKey: ["console"] });
    },
  });
}

/** C7 (A20): single-row delete — same invalidation contract as the PUT. */
export function useDeleteMediaPricing() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: {
      provider: string;
      mediaType: string;
      modelId?: string | null;
    }) => deleteMediaPricing(params),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["console", "media-pricing"],
      });
      void queryClient.invalidateQueries({ queryKey: ["console"] });
    },
  });
}
