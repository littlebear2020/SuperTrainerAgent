import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, test, vi } from "vitest";

vi.mock("streamdown", () => ({
  Streamdown: ({ children }: { children: string }) =>
    createElement("div", null, children),
}));

import {
  Reasoning,
  ReasoningContent,
  ReasoningTrigger,
} from "@/components/ai-elements/reasoning";

test("ReasoningTrigger default message uses phrasing content", () => {
  const html = renderToStaticMarkup(
    createElement(
      Reasoning,
      { isStreaming: false, defaultOpen: false },
      createElement(ReasoningTrigger, null),
      createElement(ReasoningContent, null, "test"),
    ),
  );

  // Existence baseline (copy-agnostic): the trigger rendered a button.
  expect(html).toContain("<button");
  // NOTE: src/components/ai-elements/ is registry-generated (do-not-hand-edit,
  // see AGENTS.md). The default English copy ("Thought for a few seconds" at
  // the time of writing) changes whenever the registry component is synced, so
  // this test deliberately does NOT pin the exact copy. The invariant we own
  // is HTML validity: no <p> inside the <button>.
  expect(html).not.toMatch(/<button\b[^>]*>[\s\S]*?<p\b/i);
});
