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
export function encodeArtifactPath(
  filepath: string,
  options: { raw?: boolean } = {},
) {
  const segments = filepath.split("/");
  const kept: string[] = [];
  segments.forEach((segment, index) => {
    // raw：来自 tool args 的原始路径按字面编码（不先解码）——文件名字面带 %20 / %2F
    // 时，解码再编码会让后端把它当成空格 / 分隔符，读到别的文件
    const decoded = options.raw ? segment : decodePathSegment(segment);
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
  raw = false,
}: {
  filepath: string;
  threadId: string;
  download?: boolean;
  /** true = 不预解码，按字面编码每个片段（来自 tool args 的原始路径）。 */
  raw?: boolean;
}) {
  const encodedPath = encodeArtifactPath(filepath, { raw });
  return `${getBackendBaseURL()}/api/threads/${threadId}/artifacts${encodedPath}${download ? "?download=true" : ""}`;
}

export function resolveArtifactURL(absolutePath: string, threadId: string) {
  return `${getBackendBaseURL()}/api/threads/${threadId}/artifacts${encodeArtifactPath(absolutePath)}`;
}
