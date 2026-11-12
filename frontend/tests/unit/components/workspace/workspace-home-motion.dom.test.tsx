import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import WorkspaceHome from "@/components/workspace/workspace-home";

const h = vi.hoisted(() => ({ coarse: false, reduced: false }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/core/i18n/hooks", async () => {
  const { translations } = await import("@/core/i18n/translations");
  return { useI18n: () => ({ t: translations["zh-CN"] }) };
});

vi.mock("@/components/workspace/home-course-entry", () => ({
  HomeCourseEntry: () => <div data-testid="home-course-entry" />,
}));

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

describe("WorkspaceHome ambient motion preferences", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    h.coarse = false;
    h.reduced = false;
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn((query: string) => ({
        get matches() {
          return query.includes("pointer: coarse") ? h.coarse : h.reduced;
        },
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });

  async function renderHome() {
    await act(async () => root.render(<WorkspaceHome />));
  }

  function ambientNodes() {
    return container.querySelectorAll(".ws-ambient, .ws-ambient-orb-cyan");
  }

  it("keeps ambient decorations for fine pointer and full motion", async () => {
    await renderHome();
    expect(ambientNodes()).toHaveLength(2);
  });

  it("unmounts both pseudo-element sources and the cyan orb for coarse only", async () => {
    h.coarse = true;
    h.reduced = false;
    await renderHome();
    expect(ambientNodes()).toHaveLength(0);
  });

  it("unmounts both pseudo-element sources and the cyan orb for reduced motion only", async () => {
    h.coarse = false;
    h.reduced = true;
    await renderHome();
    expect(ambientNodes()).toHaveLength(0);
  });
});
