import { describe, expect, it, vi } from "vitest";

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://backend",
}));

import {
  encodeArtifactPath,
  resolveArtifactURL,
  urlOfArtifact,
} from "@/core/artifacts/utils";

describe("encodeArtifactPath", () => {
  it("keeps slashes as separators", () => {
    expect(encodeArtifactPath("/mnt/user-data/outputs/notes.txt")).toBe(
      "/mnt/user-data/outputs/notes.txt",
    );
  });

  it("percent-encodes non-ASCII segments", () => {
    expect(encodeArtifactPath("/mnt/user-data/outputs/课程大纲.md")).toBe(
      "/mnt/user-data/outputs/%E8%AF%BE%E7%A8%8B%E5%A4%A7%E7%BA%B2.md",
    );
  });

  it("encodes spaces and characters that would break the URL", () => {
    expect(encodeArtifactPath("/mnt/outputs/my report #1.pdf")).toBe(
      "/mnt/outputs/my%20report%20%231.pdf",
    );
  });

  it("does not double-encode the separators of nested folders", () => {
    expect(encodeArtifactPath("/mnt/输出/第一章/图.png")).toBe(
      "/mnt/%E8%BE%93%E5%87%BA/%E7%AC%AC%E4%B8%80%E7%AB%A0/%E5%9B%BE.png",
    );
  });
});

// P2-14: encoding must be idempotent. Some callers hand us a path that is
// already in encoded form (e.g. a URL pathname round-tripped through the
// artifact list); encoding it a second time turned "%E8" into "%25E8" and the
// backend 404'd on a filename that literally contained "%25E8".
describe("encodeArtifactPath idempotency", () => {
  it("does not re-encode an already-encoded Chinese path", () => {
    const once = encodeArtifactPath("/mnt/user-data/outputs/课程大纲.md");

    expect(encodeArtifactPath(once)).toBe(once);
  });

  it("is stable under repeated application", () => {
    const once = encodeArtifactPath("/mnt/输出/第一章/图 1.png");

    expect(encodeArtifactPath(encodeArtifactPath(once))).toBe(once);
  });

  it("keeps a literal percent sign working (decode throws -> pass through)", () => {
    // "100%.txt" is not valid percent-encoding, so decoding must fail soft and
    // leave today's behaviour untouched.
    expect(encodeArtifactPath("/mnt/outputs/100%.txt")).toBe(
      "/mnt/outputs/100%25.txt",
    );
  });

  it("regression guard: literal Chinese filenames are unchanged", () => {
    // The mainstream case. Idempotency must not touch it.
    expect(encodeArtifactPath("/mnt/user-data/outputs/课程大纲.md")).toBe(
      "/mnt/user-data/outputs/%E8%AF%BE%E7%A8%8B%E5%A4%A7%E7%BA%B2.md",
    );
    expect(
      urlOfArtifact({ filepath: "/mnt/outputs/课程.md", threadId: "t1" }),
    ).toBe(
      "http://backend/api/threads/t1/artifacts/mnt/outputs/%E8%AF%BE%E7%A8%8B.md",
    );
  });

  it("regression guard: spaces and '#' still encode as before", () => {
    expect(encodeArtifactPath("/mnt/outputs/my report #1.pdf")).toBe(
      "/mnt/outputs/my%20report%20%231.pdf",
    );
  });
});

// SEC-M19 (F5): decode-then-encode turned "%2e%2e" into a literal ".."
// segment that sailed through to the backend — the frontend had no traversal
// defense at all. Traversal/relative segments must be filtered after decode.
describe("encodeArtifactPath traversal filtering", () => {
  it("strips encoded %2e%2e / %2e segments (encoded bypass)", () => {
    expect(encodeArtifactPath("/mnt/outputs/%2e%2e/%2e%2e/etc/passwd")).toBe(
      "/mnt/outputs/etc/passwd",
    );
  });

  it("strips plain .. and . segments", () => {
    expect(encodeArtifactPath("../../etc/passwd")).toBe("etc/passwd");
    expect(encodeArtifactPath("/mnt/./outputs/a.txt")).toBe(
      "/mnt/outputs/a.txt",
    );
  });

  it("collapses interior empty segments but keeps the leading separator", () => {
    // absolute paths (resolveArtifactURL "/tmp/x/report.md") keep their
    // leading slash; "//" or "a//b" collapses
    expect(encodeArtifactPath("//mnt//outputs//a.txt")).toBe(
      "/mnt/outputs/a.txt",
    );
    expect(encodeArtifactPath("a//b.txt")).toBe("a/b.txt");
  });

  it("traversal filtering survives the full URL builder", () => {
    expect(
      urlOfArtifact({
        filepath: "/mnt/outputs/%2e%2e/%2e%2e/secrets.txt",
        threadId: "t1",
      }),
    ).toBe("http://backend/api/threads/t1/artifacts/mnt/outputs/secrets.txt");
  });
});

describe("urlOfArtifact", () => {
  it("builds an encoded artifact URL", () => {
    expect(
      urlOfArtifact({ filepath: "/mnt/outputs/课程.md", threadId: "t1" }),
    ).toBe(
      "http://backend/api/threads/t1/artifacts/mnt/outputs/%E8%AF%BE%E7%A8%8B.md",
    );
  });

  it("appends the download flag after the encoded path", () => {
    expect(
      urlOfArtifact({
        filepath: "/mnt/outputs/课程.md",
        threadId: "t1",
        download: true,
      }),
    ).toBe(
      "http://backend/api/threads/t1/artifacts/mnt/outputs/%E8%AF%BE%E7%A8%8B.md?download=true",
    );
  });
});

describe("resolveArtifactURL", () => {
  it("encodes inline image and link paths", () => {
    expect(resolveArtifactURL("/mnt/outputs/图 1.png", "t9")).toBe(
      "http://backend/api/threads/t9/artifacts/mnt/outputs/%E5%9B%BE%201.png",
    );
  });
});
