"use client";

import { useQuery } from "@tanstack/react-query";

import { useAuth } from "@/core/auth/AuthProvider";

import {
  fetchPersonalUsage,
  type PERSONAL_USAGE_PAGE_SIZE,
  type FetchPersonalUsageOptions,
} from "./api";

export function usePersonalUsage(
  options: FetchPersonalUsageOptions & {
    limit: typeof PERSONAL_USAGE_PAGE_SIZE;
  },
) {
  const { user } = useAuth();
  const userKey = user?.id ?? "anonymous";
  return useQuery({
    queryKey: [
      "personal-usage",
      userKey,
      options.days,
      options.capability,
      options.offset,
      options.limit,
      options.tzOffsetMinutes,
    ],
    queryFn: () => fetchPersonalUsage(options),
    staleTime: 30_000,
    placeholderData: (previous, previousQuery) =>
      previousQuery?.queryKey[1] === userKey ? previous : undefined,
  });
}
