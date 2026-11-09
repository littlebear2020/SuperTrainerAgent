import { describe, expect, it } from "vitest";

import {
  createBuildEnvironment,
  evaluateBudgets,
  extractAssetPaths,
  formatMeasurementSummary,
  ROUTES,
} from "../../../scripts/measure-route-assets.mjs";

describe("route asset measurement", () => {
  it("covers every approved representative route", () => {
    expect(ROUTES).toContain("/");
    expect(ROUTES).toContain("/workspace/chats");
    expect(ROUTES).toContain("/workspace/video-gen");
    expect(ROUTES).toContain("/workspace/writer");
  });

  it("excludes /login (AUTH_DISABLED 下 307 无意义，PR-1 2.1)", () => {
    expect(ROUTES).not.toContain("/login");
  });

  it("injects the fixed auth-skipping build environment", () => {
    expect(createBuildEnvironment({ KEEP_ME: "yes" })).toEqual({
      KEEP_ME: "yes",
      SKIP_ENV_VALIDATION: "1",
      DEER_FLOW_AUTH_DISABLED: "1",
    });
  });

  it("extracts unique Next.js scripts and styles", () => {
    const html = `
      <link rel="stylesheet" href="/_next/static/css/app.css?dpl=1">
      <script src="/_next/static/chunks/app.js"></script>
      <script src="/_next/static/chunks/app.js"></script>
      <script src="https://cdn.example.com/external.js"></script>
    `;

    expect(extractAssetPaths(html)).toEqual({
      css: ["css/app.css"],
      js: ["chunks/app.js"],
    });
  });

  it("reports every route budget overage with exact values", () => {
    const result = evaluateBudgets(
      {
        "/": { css: 101, js: 200 },
        "/workspace/writer": { css: 50, js: 301 },
      },
      {
        "/": { css: 100, js: 200 },
        "/workspace/writer": { css: 50, js: 300 },
      },
    );

    expect(result).toEqual([
      "/ css: 101 bytes exceeds 100 bytes by 1 byte",
      "/workspace/writer js: 301 bytes exceeds 300 bytes by 1 byte",
    ]);
  });

  it("flags routes present in budgets but not measured", () => {
    expect(evaluateBudgets({}, { "/missing": { css: 1, js: 1 } })).toEqual([
      "/missing: route was not measured",
    ]);
  });

  it("formats a stable human-readable route summary", () => {
    expect(
      formatMeasurementSummary({
        "/": { css: 12, js: 345, html: 67 },
      }),
    ).toBe("Route asset summary\n/ JS 345 B | CSS 12 B | HTML 67 B");
  });
});
