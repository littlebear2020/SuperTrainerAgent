"use client";

import { useQuery } from "@tanstack/react-query";

import { useAuth } from "@/core/auth/AuthProvider";

import { fetchPersonalUsage, type FetchPersonalUsageOptions } from "./api";

export function usePersonalUsage(options: FetchPersonalUsageOptions) {
  const { user } = useAuth();
  const userKey = user?.id ?? "anonymous";
  return useQuery({
    queryKey: [
      "personal-usage",
      userKey,
      options.days,
      options.capability,
      options.offset,
      20,
      options.tzOffsetMinutes,
    ],
    queryFn: () => fetchPersonalUsage(options),
    staleTime: 30_000,
    placeholderData: (previous, previousQuery) =>
      previousQuery?.queryKey[1] === userKey ? previous : undefined,
  });
}
