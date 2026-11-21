import { fetch } from "@/core/api/fetcher";
import { readFastApiErrorDetail } from "@/core/api/read-fastapi-error";

import type {
  PersonalUsageResponse,
  UsageCapabilityFilter,
  UsageDays,
} from "./types";

export interface FetchPersonalUsageOptions {
  days: UsageDays;
  capability: UsageCapabilityFilter;
  offset: number;
  limit: number;
  tzOffsetMinutes: number;
}

export class PersonalUsageApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "PersonalUsageApiError";
    this.status = status;
  }
}

export async function fetchPersonalUsage(
  options: FetchPersonalUsageOptions,
): Promise<PersonalUsageResponse> {
  const query = new URLSearchParams({
    days: String(options.days),
    capability: options.capability,
    offset: String(options.offset),
    limit: String(options.limit),
    tz_offset_minutes: String(options.tzOffsetMinutes),
  });
  const response = await fetch(`/api/usage?${query.toString()}`);
  if (!response.ok) {
    throw new PersonalUsageApiError(
      response.status,
      await readFastApiErrorDetail(response, response.statusText),
    );
  }
  return (await response.json()) as PersonalUsageResponse;
}
