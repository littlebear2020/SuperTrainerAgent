import { getBackendBaseURL } from "../config";
import type { AgentThread } from "../threads";

/**
 * Decode a single path segment, falling back to the raw segment.
 *
 * A segment that is not valid percent-encoding (e.g. a filename literally
 * containing "%") makes decodeURIComponent throw; that is the common case, so
 * failing soft here keeps such names encoding exactly as before.
 */
function decodePathSegment(segment: string) {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}

/**
 * Percent-encode each path segment while keeping the "/" separators intact.
 *
 * Artifact paths come from the sandbox verbatim, so non-ASCII names (Chinese
 * filenames) or spaces would otherwise reach the backend unencoded and 404.
 *
 * Decoding first makes the encoding idempotent: callers that hand us an
 * already-encoded path get the same result instead of "%E8" becoming "%25E8".
 *
 * SEC-M19 (F5): decode-then-encode let "%2e%2e" through as a literal ".."
 * segment (encodeURIComponent doesn't encode dots), leaving the frontend with
 * zero traversal defense — every ".." reached the backend verbatim. Traversal
 * and relative segments are therefore filtered after decoding; legitimate
 * filenames can never be "." or "..".
 */
export function encodeArtifactPath(filepath: string) {
  const segments = filepath.split("/");
  const kept: string[] = [];
  segments.forEach((segment, index) => {
    const decoded = decodePathSegment(segment);
    if (decoded === "." || decoded === "..") return;
    if (decoded === "") {
      // Keep exactly one leading separator (absolute sandbox paths like
      // "/mnt/..." must stay "/mnt/..."); collapse interior empties.
      if (index === 0 && segments.length > 1) kept.push("");
      return;
    }
    kept.push(encodeURIComponent(decoded));
  });
  return kept.join("/");
}

export function urlOfArtifact({
  filepath,
  threadId,
  download = false,
}: {
  filepath: string;
  threadId: string;
  download?: boolean;
}) {
  const encodedPath = encodeArtifactPath(filepath);
  return `${getBackendBaseURL()}/api/threads/${threadId}/artifacts${encodedPath}${download ? "?download=true" : ""}`;
}

export function resolveArtifactURL(absolutePath: string, threadId: string) {
  return `${getBackendBaseURL()}/api/threads/${threadId}/artifacts${encodeArtifactPath(absolutePath)}`;
}
