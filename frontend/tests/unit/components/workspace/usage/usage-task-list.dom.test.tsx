import { useState } from "react";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { UsageTaskList } from "@/components/workspace/usage/usage-task-list";
import { I18nProvider } from "@/core/i18n/context";
import type { UsageTask } from "@/core/usage/types";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function task(id: string, overrides: Partial<UsageTask> = {}): UsageTask {
  return {
    task_key: `agent:${id}`,
    source_id: id,
    capability: "course-video",
    title: `${id}任务`,
    status: "completed",
    cost: 1.5,
    llm_cost: 1.5,
    media_cost: null,
    currency: "CNY",
    cost_source: "real",
    total_tokens: 100,
    models: [
      {
        name: "real-model",
        input_tokens: 60,
        output_tokens: 40,
        cache_read_tokens: null,
        total_tokens: 100,
        cost: 1.5,
        cost_source: "real",
      },
    ],
    updated_at: "2026-11-21T13:00:00Z",
    ...overrides,
  };
}

function Harness({
  tasks,
  expandAll = false,
}: {
  tasks: UsageTask[];
  expandAll?: boolean;
}) {
  const [expanded, setExpanded] = useState<Set<string>>(
    () => new Set(expandAll ? tasks.map((item) => item.task_key) : []),
  );
  const onToggle = (taskKey: string) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(taskKey)) next.delete(taskKey);
      else next.add(taskKey);
      return next;
    });
  };
  return (
    <UsageTaskList tasks={tasks} expanded={expanded} onToggle={onToggle} />
  );
}

describe("UsageTaskList", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });

  async function render(tasks: UsageTask[], expandAll = false) {
    await act(async () => {
      root.render(
        <I18nProvider initialLocale="zh-CN">
          <Harness tasks={tasks} expandAll={expandAll} />
        </I18nProvider>,
      );
    });
  }

  async function key(selector: string, key: string) {
    await act(async () => {
      container
        .querySelector(selector)!
        .dispatchEvent(
          new KeyboardEvent("keydown", {
            key,
            bubbles: true,
            cancelable: true,
          }),
        );
    });
  }

  it("supports focus, Enter, Space, and an independent keyboard-opened original-task link", async () => {
    await render([task("completed")]);
    const row = container.querySelector<HTMLElement>(
      "[data-testid='task-row-completed']",
    )!;
    expect(row.getAttribute("role")).toBe("button");
    expect(row.tabIndex).toBe(0);
    row.focus();
    expect(document.activeElement).toBe(row);
    expect(row.querySelector("button, a")).toBeNull();

    await key("[data-testid='task-row-completed']", "Enter");
    expect(row.getAttribute("aria-expanded")).toBe("true");
    expect(
      container.querySelector("[data-testid='task-details-completed']"),
    ).not.toBeNull();

    const original = container.querySelector<HTMLAnchorElement>(
      "[data-testid='open-original-completed']",
    )!;
    expect(original.getAttribute("href")).toBe(
      "/workspace/agents/course-video/chats/completed",
    );
    let clicks = 0;
    original.addEventListener("click", (event) => {
      event.preventDefault();
      clicks += 1;
    });
    original.focus();
    expect(document.activeElement).toBe(original);
    await key("[data-testid='open-original-completed']", "Enter");
    expect(clicks).toBe(1);
    expect(row.getAttribute("aria-expanded")).toBe("true");

    row.focus();
    await key("[data-testid='task-row-completed']", " ");
    expect(row.getAttribute("aria-expanded")).toBe("false");
    expect(
      container.querySelector("[data-testid='task-details-completed']"),
    ).toBeNull();
  });

  it("distinguishes unknown media and cost components without inventing an equation", async () => {
    await render(
      [
        task("known", { media_cost: 0.5, cost: 2, cost_source: "mixed" }),
        task("unknown-media"),
        task("unpriced", {
          cost: null,
          llm_cost: null,
          media_cost: null,
          currency: null,
          cost_source: null,
          total_tokens: 12,
          models: [],
        }),
      ],
      true,
    );

    expect(
      container.querySelector("[data-testid='cost-equation-known']")
        ?.textContent,
    ).toContain("+");
    expect(
      container.querySelector("[data-testid='cost-equation-known']")
        ?.textContent,
    ).toContain("=");
    expect(
      container.querySelector("[data-testid='cost-equation-unknown-media']"),
    ).toBeNull();
    expect(
      container.querySelector("[data-testid='cost-equation-unpriced']"),
    ).toBeNull();
    expect(container.textContent).toContain("媒体费用未核算");
    expect(container.textContent).not.toContain("未归因");
    expect(container.textContent).toContain("暂无模型明细");
    expect(
      container.querySelector("[data-testid='task-tokens-unpriced']")
        ?.textContent,
    ).toContain("12");
    expect(
      container.querySelector("[data-testid='task-cost-unpriced']")
        ?.textContent,
    ).toContain("—");
    expect(
      container.querySelector("[data-testid='task-cost-source-unknown-media']")
        ?.textContent,
    ).toBe("供应商回传");
    expect(
      container.querySelector("[data-testid='task-cost-source-unknown-media']")
        ?.textContent,
    ).not.toContain("估算");
  });

  it("marks running values as current and preserves incomplete consumption", async () => {
    await render(
      [
        task("running", {
          status: "running",
          cost: null,
          llm_cost: null,
          media_cost: null,
          currency: null,
          cost_source: null,
          total_tokens: null,
          models: [],
        }),
        task("incomplete", {
          status: "incomplete",
          cost: 0.25,
          llm_cost: 0.25,
          total_tokens: 20,
        }),
      ],
      true,
    );

    expect(
      container.querySelector("[data-testid='task-running-note-running']")
        ?.textContent,
    ).toContain("截至目前");
    expect(container.textContent).toContain("核算中");
    expect(
      container.querySelector("[data-testid='task-cost-incomplete']")
        ?.textContent,
    ).toContain("¥0.2500");
    expect(
      container.querySelector("[data-testid='task-tokens-incomplete']")
        ?.textContent,
    ).toContain("20");
  });
});
