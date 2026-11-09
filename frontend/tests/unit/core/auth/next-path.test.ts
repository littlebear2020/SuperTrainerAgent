/**
 * Post-auth redirect (`?next=`) validation tests.
 *
 * The guard exists to stop open redirects: a crafted login link must never
 * be able to bounce an authenticated user to an attacker-controlled origin.
 *
 * The backslash cases are the real exploit. Browsers (WHATWG URL) treat `\`
 * as equivalent to `/` when resolving a relative reference, so `/\evil.com`
 * resolves to `http://evil.com/` even though it passes a naive
 * `startsWith("/")` + `startsWith("//")` check. Each rejection case is
 * paired with an assertion on real URL resolution so the test states *why*
 * the string is unsafe, not just that we happen to reject it.
 */

import { describe, expect, test } from "vitest";

import {
  DEFAULT_AUTH_NEXT_PATH,
  resolveNextPath,
  validateNextParam,
} from "@/core/auth/next-path";

const APP_ORIGIN = "http://intranet.company:2026";

/** Origin a browser would actually navigate to for this redirect target. */
function resolvedOrigin(next: string): string {
  return new URL(next, APP_ORIGIN).origin;
}

describe("validateNextParam", () => {
  test("accepts same-origin relative paths", () => {
    expect(validateNextParam("/workspace")).toBe("/workspace");
    expect(validateNextParam("/workspace/chats/abc?tab=recent#top")).toBe(
      "/workspace/chats/abc?tab=recent#top",
    );
  });

  test("rejects empty and non-relative input", () => {
    expect(validateNextParam(null)).toBeNull();
    expect(validateNextParam("")).toBeNull();
    expect(validateNextParam("workspace")).toBeNull();
    expect(validateNextParam("https://evil.com")).toBeNull();
    expect(validateNextParam("//evil.com")).toBeNull();
  });

  test("rejects backslash paths that browsers resolve off-origin", () => {
    // Precondition: these really do escape the origin in a browser.
    expect(resolvedOrigin("/\\evil.com")).toBe("http://evil.com");
    expect(resolvedOrigin("/\\evil.com/x")).toBe("http://evil.com");
    expect(resolvedOrigin("/\\\\evil.com")).toBe("http://evil.com");

    expect(validateNextParam("/\\evil.com")).toBeNull();
    expect(validateNextParam("/\\evil.com/x")).toBeNull();
    expect(validateNextParam("/\\\\evil.com")).toBeNull();
  });

  test("rejects backslashes anywhere in the path", () => {
    expect(validateNextParam("/workspace\\@evil.com")).toBeNull();
    expect(validateNextParam("/foo\\bar")).toBeNull();
  });

  test("rejects scheme-bearing paths", () => {
    expect(validateNextParam("/:evil")).toBeNull();
    expect(validateNextParam("/redirect?to=javascript:alert(1)")).toBeNull();
  });

  test("every rejected value would have left the app origin, and every accepted one stays", () => {
    const hostile = [
      "//evil.com",
      "https://evil.com",
      "/\\evil.com",
      "/\\evil.com/x",
      "/\\\\evil.com",
    ];
    for (const next of hostile) {
      expect(validateNextParam(next)).toBeNull();
    }

    const safe = ["/workspace", "/workspace/chats/abc?tab=1#x", "/setup"];
    for (const next of safe) {
      expect(validateNextParam(next)).toBe(next);
      expect(resolvedOrigin(next)).toBe(APP_ORIGIN);
    }
  });
});

describe("resolveNextPath", () => {
  test("falls back to the workspace for unsafe targets", () => {
    expect(resolveNextPath("/\\evil.com")).toBe(DEFAULT_AUTH_NEXT_PATH);
    expect(resolveNextPath(null)).toBe(DEFAULT_AUTH_NEXT_PATH);
  });

  test("passes through safe targets and honours a custom fallback", () => {
    expect(resolveNextPath("/safe")).toBe("/safe");
    expect(resolveNextPath("//evil.com", "/fallback")).toBe("/fallback");
  });
});
