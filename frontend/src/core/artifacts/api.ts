import { fetch } from "@/core/api/fetcher";
import { readFastApiErrorDetail } from "@/core/api/read-fastapi-error";

import { urlOfArtifact } from "./utils";

export interface ArtifactUpdateResponse {
  path: string;
  sha256: string;
  size: number;
}

export class ArtifactRequestError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ArtifactRequestError";
    this.status = status;
  }
}

export async function updateArtifactContent({
  threadId,
  filepath,
  content,
  expectedSha256,
}: {
  threadId: string;
  filepath: string;
  content: string;
  expectedSha256: string;
}): Promise<ArtifactUpdateResponse> {
  const response = await fetch(urlOfArtifact({ filepath, threadId }), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      content,
      expected_sha256: expectedSha256,
    }),
  });
  if (!response.ok) {
    throw new ArtifactRequestError(
      response.status,
      await readFastApiErrorDetail(response, "Failed to update artifact"),
    );
  }
  return response.json() as Promise<ArtifactUpdateResponse>;
}
