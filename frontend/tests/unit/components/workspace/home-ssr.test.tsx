import { renderToString } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { HomeCourseEntry } from "@/components/workspace/home-course-entry";
import WorkspaceHome from "@/components/workspace/workspace-home";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/core/i18n/hooks", async () => {
  const { translations } = await import("@/core/i18n/translations");
  return { useI18n: () => ({ t: translations["zh-CN"] }) };
});

describe("home SSR without browser globals", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the course entry without window or document", () => {
    vi.stubGlobal("window", undefined);
    vi.stubGlobal("document", undefined);
    expect(() =>
      renderToString(<HomeCourseEntry onSelect={vi.fn()} />),
    ).not.toThrow();
  });

  it("renders workspace home without window or document", () => {
    vi.stubGlobal("window", undefined);
    vi.stubGlobal("document", undefined);
    expect(() => renderToString(<WorkspaceHome />)).not.toThrow();
  });
});
