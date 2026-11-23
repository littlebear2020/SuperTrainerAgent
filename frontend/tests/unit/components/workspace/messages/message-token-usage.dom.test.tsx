import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TurnTokenUsage } from "@/components/workspace/messages/message-token-usage";
import type { TokenUsage } from "@/core/messages/usage";

vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      tokenUsage: {
        label: "Tokens",
        input: "Input",
        output: "Output",
        total: "Total",
        unavailableShort: "No usage returned",
      },
    },
  }),
}));

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function renderTurnUsage(usage: TokenUsage | null, missingCount: number) {
  act(() => {
    root.render(<TurnTokenUsage usage={usage} missingCount={missingCount} />);
  });
  return container;
}

describe("TurnTokenUsage", () => {
  it("renders input, output, and total tokens in the turn row", () => {
    const element = renderTurnUsage(
      { inputTokens: 1234, outputTokens: 2345, totalTokens: 3579 },
      0,
    );
    const row = element.querySelector('[data-testid="turn-token-usage"]');

    expect(row).not.toBeNull();
    expect(row?.textContent).toContain("Tokens");
    expect(row?.textContent).toContain("Input: 1,234");
    expect(row?.textContent).toContain("Output: 2,345");
    expect(row?.textContent).toContain("Total: 3,579");
    expect(row?.textContent).not.toContain("No usage returned");
  });

  it("shows only the unavailable label when every AI message lacks usage", () => {
    const element = renderTurnUsage(null, 3);
    const row = element.querySelector('[data-testid="turn-token-usage"]');

    expect(row?.textContent).toContain("No usage returned");
    expect(row?.textContent).not.toContain("Input:");
    expect(row?.textContent).not.toContain("Output:");
    expect(row?.textContent).not.toContain("Total:");
    expect(row?.textContent).not.toContain("×");
  });

  it("appends the missing count after returned sums for a partial turn", () => {
    const element = renderTurnUsage(
      { inputTokens: 10, outputTokens: 20, totalTokens: 30 },
      2,
    );
    const row = element.querySelector('[data-testid="turn-token-usage"]');

    expect(row?.textContent).toContain("Input: 10");
    expect(row?.textContent).toContain("Output: 20");
    expect(row?.textContent).toContain("Total: 30");
    expect(row?.textContent).toContain("No usage returned ×2");
  });
});
