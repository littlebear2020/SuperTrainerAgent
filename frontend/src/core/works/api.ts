import { fetch } from "@/core/api/fetcher";
import { readFastApiErrorDetail } from "@/core/api/read-fastapi-error";
import { getBackendBaseURL } from "@/core/config";

import type { ThreadArtifactSummary } from "./types";

export async function fetchArtifactSummaries(
  threadIds: string[],
): Promise<ThreadArtifactSummary[]> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/works/artifact-summaries`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ thread_ids: threadIds }),
    },
  );
  if (!response.ok) {
    throw new Error(
      await readFastApiErrorDetail(
        response,
        "Failed to load artifact summaries",
      ),
    );
  }
  return response.json() as Promise<ThreadArtifactSummary[]>;
}
