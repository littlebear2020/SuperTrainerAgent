import { renderToString } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  pending: new Promise<void>(() => undefined),
}));

vi.mock("@/components/workspace/video-gen", async () => {
  const { use } = await import("react");
  return {
    VideoGenPage: () => {
      use(h.pending);
      return null;
    },
  };
});

import Page from "@/app/workspace/video-gen/page";

describe("video-gen route", () => {
  it("renders a lightweight fallback while the search-param content suspends", () => {
    expect(renderToString(<Page />)).toContain('aria-busy="true"');
  });
});
